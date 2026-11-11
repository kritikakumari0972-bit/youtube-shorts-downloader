from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable

try:
    import yt_dlp
except ImportError:  # Keep the native engine importable without the compatibility pack.
    yt_dlp = None  # type: ignore[assignment]

from . import native_engine, store
from .extractors import get_registry
from .security import safe_filename

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
DOWNLOAD_DIR = DATA_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DownloadCancelled(Exception):
    pass


def tool_status() -> dict[str, Any]:
    try:
        version = yt_dlp.version.__version__ if yt_dlp is not None else "not installed"
    except Exception:
        version = "installed" if yt_dlp is not None else "not installed"
    registry = get_registry()
    return {
        "native_engine": "NOVA Native v1.4 · HLS WebVTT Core",
        "extractors": registry.names(),
        "yt_dlp": version,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "javascript_runtime": bool(shutil.which("deno") or shutil.which("node") or shutil.which("bun")),
        "external_downloader_api": False,
    }


def format_selector(mode: str, quality: str) -> str:
    if mode == "audio":
        return "bestaudio/best"
    if quality == "best":
        return "bv*+ba/b"
    height = int(quality)
    return f"bv*[height<={height}]+ba/b[height<={height}]/b"


def _js_runtimes() -> dict[str, dict[str, Any]]:
    if shutil.which("deno"):
        return {"deno": {}}
    if shutil.which("node"):
        return {"node": {}}
    if shutil.which("bun"):
        return {"bun": {}}
    return {}


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "Live / unknown length"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def extract_metadata(url: str) -> dict[str, Any]:
    match = get_registry().extract(url)
    if match:
        resolved = match.result
        metadata = resolved.get("metadata") or native_engine.preview(resolved["media_url"])
        metadata.update({
            "title": resolved.get("title") or metadata.get("title"),
            "creator": resolved.get("creator") or metadata.get("creator"),
            "thumbnail": resolved.get("thumbnail") or metadata.get("thumbnail"),
            "source": resolved.get("source") or f"NOVA Extractor · {match.extractor}",
            "webpage_url": url,
        })
        return metadata

    if yt_dlp is None:
        raise RuntimeError(
            "NOVA Extractor Core found no directly exposed media source on this page. "
            "Install the local site-extractor compatibility pack (yt-dlp) for additional site-specific extractors."
        )

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 25,
        "retries": 2,
        "extractor_retries": 2,
        "js_runtimes": _js_runtimes(),
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)

    if info.get("_type") == "playlist" and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), info)

    formats = []
    seen = set()
    for item in info.get("formats") or []:
        height = item.get("height")
        if not height:
            continue
        key = (int(height), item.get("ext", ""), bool(item.get("acodec") not in (None, "none")))
        if key in seen:
            continue
        seen.add(key)
        formats.append({"height": int(height), "ext": item.get("ext") or "?", "has_audio": key[2]})
    formats.sort(key=lambda item: item["height"], reverse=True)

    duration = int(info.get("duration") or 0)
    return {
        "title": str(info.get("title") or "Untitled media")[:240],
        "creator": str(info.get("uploader") or info.get("channel") or "Unknown creator")[:160],
        "thumbnail": info.get("thumbnail"),
        "duration": duration,
        "duration_label": _format_duration(duration),
        "source": info.get("extractor_key") or info.get("extractor") or "Local site extractor",
        "webpage_url": info.get("webpage_url") or url,
        "formats": formats[:16],
        "is_live": bool(info.get("is_live")),
        "playlist_count": info.get("playlist_count"),
    }


def _progress_hook(job_id: str, on_update: Callable[..., None]) -> Callable[[dict[str, Any]], None]:
    last_update = 0.0

    def hook(event: dict[str, Any]) -> None:
        nonlocal last_update
        job = store.get_job(job_id)
        if job and job.get("cancel_requested"):
            raise DownloadCancelled("Download cancelled by user.")

        info = event.get("info_dict") or {}
        if info.get("title"):
            on_update(
                title=str(info.get("title"))[:240],
                creator=str(info.get("uploader") or info.get("channel") or "")[:160],
            )
        if event.get("status") != "downloading":
            return
        import time
        now = time.monotonic()
        if now - last_update < 0.45:
            return
        last_update = now
        total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
        downloaded = event.get("downloaded_bytes") or 0
        percent = round(downloaded * 100 / total, 1) if total else 0
        speed = event.get("speed") or 0
        eta = event.get("eta")
        on_update(
            percent=min(percent, 99.5),
            downloaded_bytes=int(downloaded),
            total_bytes=int(total),
            speed=_human_bytes(speed) + "/s" if speed else "—",
            eta=f"{int(eta)}s" if eta is not None else "—",
        )

    return hook


def _human_bytes(value: float | int) -> str:
    amount = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} GB"


def _collect_output(job_dir: Path, job_id: str) -> Path:
    allowed = {
        ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mp3", ".m4a",
        ".opus", ".ogg", ".wav", ".flac", ".ts", ".gif", ".jpg", ".jpeg", ".png",
    }
    files = [
        path for path in job_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in allowed and path.stat().st_size > 0
    ]
    if not files:
        raise RuntimeError("The extractor finished but no downloadable media file was produced.")
    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    if len(files) == 1:
        return files[0]

    import zipfile
    archive = job_dir / f"nova-fetch-{job_id}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for path in files:
            bundle.write(path, arcname=path.name)
    return archive


def _download_with_ytdlp(job: dict[str, Any], on_update: Callable[..., None]) -> Path:
    if yt_dlp is None:
        raise RuntimeError("The local site-extractor compatibility pack is not installed.")
    job_dir = DOWNLOAD_DIR / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "format": format_selector(job["mode"], job["quality"]),
        "outtmpl": str(job_dir / "%(title).160B [%(id)s].%(ext)s"),
        "restrictfilenames": True,
        "windowsfilenames": True,
        "noplaylist": not bool(job.get("playlist")),
        "playlistend": 50,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [_progress_hook(job["id"], on_update)],
        "js_runtimes": _js_runtimes(),
        "overwrites": False,
    }
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    if job["mode"] == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": str(job.get("audio_bitrate", "192"))}
        ]
    else:
        options["merge_output_format"] = job.get("container", "mp4")

    postprocessors = options.setdefault("postprocessors", [])
    if job.get("embed_metadata"):
        postprocessors.append({"key": "FFmpegMetadata"})
    if job.get("embed_thumbnail"):
        options["writethumbnail"] = True
        postprocessors.append({"key": "EmbedThumbnail"})
    if job.get("subtitles"):
        options["writesubtitles"] = True
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = [job.get("subtitle_lang") or "en"]
        options["subtitlesformat"] = "srt/best"

    def postprocessor_hook(event: dict[str, Any]) -> None:
        if event.get("status") == "started":
            on_update(status="processing", stage="FFmpeg processing")
        current = store.get_job(job["id"])
        if current and current.get("cancel_requested"):
            raise DownloadCancelled("Download cancelled by user.")

    options["postprocessor_hooks"] = [postprocessor_hook]
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([job["url"]])
    return _collect_output(job_dir, job["id"])


def perform_download(job: dict[str, Any], on_update: Callable[..., None]) -> Path:
    provider = job.get("provider", "auto")

    if provider == "yt-dlp":
        return _download_with_ytdlp(job, on_update)

    if job.get("subtitles") and provider != "native":
        # Automatic mode uses the local site-extractor pack for the broadest
        # subtitle coverage. Native mode supports declared static HLS WebVTT.
        return _download_with_ytdlp(job, on_update)

    match = get_registry().extract(job["url"])
    if provider == "native":
        if not match:
            raise RuntimeError(
                "NOVA Extractor Core could not find a directly exposed media source. "
                "This page needs a dedicated first-party adapter or the local compatibility pack."
            )
        resolved_job = {**job, "url": match.result["media_url"]}
        on_update(stage=f"NOVA Native · {match.extractor}")
        return native_engine.download(resolved_job, on_update)

    # Automatic mode runs NOVA's own extractor adapters and media engine first.
    # It falls back to the local compatibility pack only when the source needs
    # site-specific extraction or a native format is not yet implemented.
    if match:
        resolved_job = {**job, "url": match.result["media_url"]}
        try:
            on_update(stage=f"NOVA Native · {match.extractor}")
            return native_engine.download(resolved_job, on_update)
        except DownloadCancelled:
            raise
        except Exception as native_error:
            if yt_dlp is None:
                raise RuntimeError(str(native_error)[:500]) from native_error
            on_update(stage="Trying local site-extractor compatibility pack")
            try:
                return _download_with_ytdlp(job, on_update)
            except Exception as extractor_error:
                raise RuntimeError(
                    f"NOVA Native: {str(native_error)[:220]} | Local extractor: {str(extractor_error)[:220]}"
                ) from extractor_error

    if yt_dlp is None:
        raise RuntimeError("No NOVA extractor matched this source and the local compatibility pack is not installed.")
    return _download_with_ytdlp(job, on_update)
