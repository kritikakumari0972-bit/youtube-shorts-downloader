from __future__ import annotations

import asyncio, os, secrets, shutil, time, uuid
from collections import defaultdict, deque
from contextlib import suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import engines, store
from .archives import package_subtitles
from .engines import DownloadCancelled
from .security import UnsafeURLError, safe_filename, validate_external_url

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DATA = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
FILES = DATA / "downloads"
FILES.mkdir(parents=True, exist_ok=True)
KEY = os.getenv("APP_API_KEY", "").strip()
WORKERS = max(1, min(4, int(os.getenv("MAX_ACTIVE_JOBS", "2"))))
QUEUE_LIMIT = max(1, int(os.getenv("MAX_QUEUED_JOBS", "12")))

app = FastAPI(title="NOVA FETCH API", version="1.0.0", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
queue: asyncio.Queue[str] = asyncio.Queue()
tasks: list[asyncio.Task] = []
rate: dict[str, deque[float]] = defaultdict(deque)
rate_lock = asyncio.Lock()


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    mode: Literal["video", "audio"] = "video"
    quality: Literal["best", "2160", "1440", "1080", "720", "480", "360", "240", "144"] = "best"
    container: Literal["mp4", "mkv", "webm"] = "mp4"
    provider: Literal["auto", "native", "yt-dlp"] = "auto"
    playlist: bool = False
    subtitles: bool = False
    subtitle_lang: str = Field(default="en", min_length=2, max_length=12)
    embed_metadata: bool = True
    embed_thumbnail: bool = False
    audio_bitrate: Literal["128", "192", "256", "320"] = "192"
    rights_confirmed: bool = False


@app.middleware("http")
async def protect_api(request: Request, call_next):
    if KEY and request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-api-key", "")
        if not secrets.compare_digest(supplied, KEY):
            return JSONResponse({"detail": "API key required. Add it in Settings."}, status_code=401)
    return await call_next(request)


def public(job: dict) -> dict:
    return {key: value for key, value in job.items() if key not in {"url", "output_path", "cancel_requested"}}


async def limit_requests(ip: str):
    now = time.monotonic()
    async with rate_lock:
        events = rate[ip]
        while events and now - events[0] > 60:
            events.popleft()
        if len(events) >= 8:
            raise HTTPException(429, "Queue limit reached. Wait a minute and retry.")
        events.append(now)


async def worker():
    while True:
        jid = await queue.get()
        try:
            job = store.get_job(jid)
            if not job or job.get("status") != "queued":
                continue
            if job.get("cancel_requested"):
                store.update_job(jid, status="cancelled", stage="Cancelled", cancel_requested=False)
                continue
            store.update_job(jid, status="downloading", started_at=time.time(), error=None)
            job = store.get_job(jid) or job

            def update(**changes):
                store.update_job(jid, **changes)
                current_state = store.get_job(jid) or {}
                if current_state.get("cancel_requested"):
                    raise DownloadCancelled("Download cancelled by user.")

            try:
                output = await asyncio.to_thread(engines.perform_download, job, update)
                if job.get("subtitles") and job.get("provider") != "native":
                    output = await asyncio.to_thread(package_subtitles, output, FILES / jid)
                current = store.get_job(jid) or {}
                if current.get("cancel_requested"):
                    raise DownloadCancelled("Cancelled by user.")
                output = output.resolve()
                if not output.is_relative_to(FILES.resolve()) or not output.is_file():
                    raise RuntimeError("The engine returned an invalid output path.")
                store.update_job(
                    jid, status="completed", percent=100, stage="Ready",
                    output_path=str(output), output_name=output.name,
                    output_bytes=output.stat().st_size, completed_at=time.time(),
                    error=None, cancel_requested=False
                )
            except DownloadCancelled:
                shutil.rmtree(FILES / jid, ignore_errors=True)
                store.update_job(jid, status="cancelled", stage="Cancelled", error=None, cancel_requested=False)
            except Exception as exc:
                shutil.rmtree(FILES / jid, ignore_errors=True)
                store.update_job(jid, status="failed", stage="Failed", error=str(exc)[:700], cancel_requested=False)
        finally:
            queue.task_done()


@app.on_event("startup")
async def startup():
    store.init_store()
    store.recover_jobs()
    for item in reversed(store.list_jobs(500)):
        if item.get("status") == "queued":
            queue.put_nowait(item["id"])
    for _ in range(WORKERS):
        tasks.append(asyncio.create_task(worker()))


@app.on_event("shutdown")
async def shutdown():
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


@app.get("/")
async def home():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "NOVA FETCH", "version": "1.0.0"}


@app.get("/api/config")
async def config():
    return {"tools": engines.tool_status(), "requires_api_key": bool(KEY), "queue_limit": QUEUE_LIMIT, "safe_mode": True}


@app.get("/api/preview")
async def preview(url: str):
    try:
        validate_external_url(url)
        return await asyncio.to_thread(engines.extract_metadata, url)
    except UnsafeURLError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, f"Could not read this source: {str(exc)[:260]}") from exc


@app.post("/api/downloads", status_code=202)
async def create_download(body: DownloadRequest, request: Request):
    if not body.rights_confirmed:
        raise HTTPException(400, "Confirm you own the media or have permission to save it.")
    try:
        url = validate_external_url(body.url)
    except UnsafeURLError as exc:
        raise HTTPException(400, str(exc)) from exc
    await limit_requests(request.client.host if request.client else "unknown")
    active = sum(1 for item in store.list_jobs(500) if item.get("status") in {"queued", "downloading", "processing"})
    if active >= QUEUE_LIMIT:
        raise HTTPException(429, "The queue is full. Let a few downloads finish first.")
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid, "title": "Queued media", "creator": "", "url": url,
        "mode": body.mode, "quality": body.quality, "container": body.container,
        "provider": body.provider, "playlist": body.playlist,
        "subtitles": body.subtitles, "subtitle_lang": body.subtitle_lang,
        "embed_metadata": body.embed_metadata, "embed_thumbnail": body.embed_thumbnail,
        "audio_bitrate": body.audio_bitrate, "status": "queued",
        "stage": "Waiting in queue", "percent": 0, "downloaded_bytes": 0,
        "total_bytes": 0, "speed": "—", "eta": "—", "created_at": time.time(),
        "cancel_requested": False, "output_path": None, "output_name": None,
        "output_bytes": 0, "error": None
    }
    store.save_job(job)
    queue.put_nowait(jid)
    return public(job)


@app.get("/api/downloads")
async def list_downloads():
    return [public(job) for job in store.list_jobs(80)]


@app.get("/api/downloads/{jid}")
async def get_download(jid: str):
    job = store.get_job(jid)
    if not job:
        raise HTTPException(404, "Download job not found.")
    return public(job)


@app.post("/api/downloads/{jid}/cancel")
async def cancel_download(jid: str):
    job = store.get_job(jid)
    if not job:
        raise HTTPException(404, "Download job not found.")
    if job.get("status") not in {"queued", "downloading", "processing"}:
        raise HTTPException(409, "This job is already finished.")
    store.update_job(jid, cancel_requested=True, stage="Cancelling…")
    return {"ok": True}


@app.get("/api/downloads/{jid}/file")
async def download_file(jid: str):
    job = store.get_job(jid)
    if not job or job.get("status") != "completed" or not job.get("output_path"):
        raise HTTPException(404, "The completed file is not available.")
    output = Path(job["output_path"]).resolve()
    if not output.is_relative_to(FILES.resolve()) or not output.is_file():
        raise HTTPException(404, "The file is no longer available.")
    return FileResponse(output, filename=safe_filename(job.get("output_name") or output.name),
                        media_type="application/octet-stream")


@app.delete("/api/downloads/{jid}")
async def delete_download(jid: str):
    job = store.get_job(jid)
    if not job:
        raise HTTPException(404, "Download job not found.")
    if job.get("status") in {"queued", "downloading", "processing"}:
        raise HTTPException(409, "Cancel an active job before removing it.")
    shutil.rmtree(FILES / jid, ignore_errors=True)
    store.delete_job(jid)
    return {"ok": True}
