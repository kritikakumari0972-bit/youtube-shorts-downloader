from __future__ import annotations

import asyncio
import mimetypes
import os
import re
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

from .security import UnsafeURLError, safe_filename, validate_external_url

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
DOWNLOAD_DIR = DATA_DIR / "downloads"
MAX_BYTES = int(float(os.getenv("MAX_DOWNLOAD_GB", "8")) * 1024**3)
MAX_MANIFEST_BYTES = 3 * 1024 * 1024
MAX_SEGMENTS = 20_000
REDIRECTS = 5
HEADERS = {"User-Agent": "NOVA-FETCH/1.0 (self-hosted media client)", "Accept": "*/*"}


class NativeEngineError(RuntimeError):
    pass


def is_native_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith((
        ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".mp3",
        ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac", ".ts",
        ".m3u8", ".mpd",
    ))


def _content_disposition_name(header: str | None) -> str | None:
    if not header:
        return None
    message = Message()
    message["content-disposition"] = header
    name = message.get_filename()
    return unquote(name) if name else None


def _extension(url: str, content_type: str | None = None, filename: str | None = None) -> str:
    if filename and Path(filename).suffix:
        suffix = Path(filename).suffix.lower()
        if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            return suffix
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix and re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return mimetypes.guess_extension(mime) or ".bin"


def _follow_get(client: httpx.Client, url: str, stream: bool = False):
    current = validate_external_url(url)
    for _ in range(REDIRECTS + 1):
        response = client.send(
            client.build_request("GET", current, headers=HEADERS),
            stream=stream,
            follow_redirects=False,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            response.raise_for_status()
            return response, current
        location = response.headers.get("location")
        response.close()
        if not location:
            raise NativeEngineError("The source returned an invalid redirect.")
        current = validate_external_url(urljoin(current, location))
    raise NativeEngineError("The source redirected too many times.")


def preview(url: str) -> dict:
    safe = validate_external_url(url)
    path = urlsplit(safe).path.lower()
    name = safe_filename(Path(unquote(urlsplit(safe).path)).name or "media-file")
    kind = "HLS playlist" if path.endswith(".m3u8") else "MPEG-DASH manifest" if path.endswith(".mpd") else "Direct media file"
    if path.endswith(".m3u8"):
        formats = [{"height": 0, "ext": "HLS", "has_audio": True}]
    elif path.endswith(".mpd"):
        formats = [{"height": 0, "ext": "DASH", "has_audio": True}]
    else:
        formats = [{"height": 0, "ext": Path(name).suffix.lstrip(".") or "auto", "has_audio": True}]
    return {
        "title": name,
        "creator": "Direct source",
        "thumbnail": None,
        "duration": 0,
        "duration_label": "Length not exposed by this source",
        "source": kind,
        "webpage_url": safe,
        "formats": formats,
        "is_live": False,
        "playlist_count": None,
    }


def _read_manifest(client: httpx.Client, url: str) -> tuple[str, str]:
    response, final_url = _follow_get(client, url, stream=False)
    try:
        raw = response.content
        if len(raw) > MAX_MANIFEST_BYTES:
            raise NativeEngineError("The manifest is larger than the configured limit.")
        try:
            return raw.decode("utf-8-sig"), final_url
        except UnicodeDecodeError as exc:
            raise NativeEngineError("The media manifest is not valid UTF-8 text.") from exc
    finally:
        response.close()


def _download_resource(client: httpx.Client, url: str, output, on_update, state: dict, total_hint: int = 0):
    response, final_url = _follow_get(client, url, stream=True)
    try:
        content_length = int(response.headers.get("content-length") or 0)
        for chunk in response.iter_bytes(256 * 1024):
            if not chunk:
                continue
            state["total"] += len(chunk)
            if state["total"] > MAX_BYTES:
                raise NativeEngineError("The download exceeds MAX_DOWNLOAD_GB.")
            output.write(chunk)
            on_update(
                percent=round(min(99.5, state["total"] * 100 / total_hint), 1) if total_hint else 0,
                downloaded_bytes=state["total"],
                total_bytes=total_hint or content_length or 0,
                stage="Downloading with NOVA native engine",
            )
    finally:
        response.close()


def _parse_hls_attrs(value: str) -> dict[str, str]:
    attrs = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("(?:[^"]*)"|[^,]*)', value):
        attrs[match.group(1)] = match.group(2).strip('"')
    return attrs


def _download_hls(client: httpx.Client, url: str, job_dir: Path, job_id: str, on_update) -> Path:
    text, final_url = _read_manifest(client, url)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    variants = []
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:") and index + 1 < len(lines):
            attrs = _parse_hls_attrs(line.split(":", 1)[1])
            if attrs.get("AUDIO"):
                raise NativeEngineError("This HLS source uses a separate audio playlist; the native engine does not mux multi-rendition HLS yet.")
            variants.append((int(attrs.get("BANDWIDTH", "0") or 0), urljoin(final_url, lines[index + 1])))
    if variants:
        variants.sort(reverse=True)
        return _download_hls(client, variants[0][1], job_dir, job_id, on_update)

    if "#EXT-X-ENDLIST" not in lines:
        raise NativeEngineError("Live or unfinished HLS playlists are not supported by the native engine yet.")
    if any(line.startswith("#EXT-X-BYTERANGE") for line in lines):
        raise NativeEngineError("This HLS playlist uses byte ranges, which are not supported by the native engine yet.")
    for line in lines:
        if line.startswith("#EXT-X-KEY:"):
            method = _parse_hls_attrs(line.split(":", 1)[1]).get("METHOD", "")
            if method != "NONE":
                raise NativeEngineError("Encrypted or DRM-protected HLS is not supported.")

    init_uri = None
    segments = []
    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_hls_attrs(line.split(":", 1)[1])
            init_uri = urljoin(final_url, attrs.get("URI", ""))
            if not attrs.get("URI"):
                raise NativeEngineError("HLS initialization segment is malformed.")
        elif line and not line.startswith("#"):
            segments.append(urljoin(final_url, line))
    if not segments or len(segments) > MAX_SEGMENTS:
        raise NativeEngineError("The HLS playlist has no media segments or exceeds the segment limit.")

    extension = ".mp4" if init_uri else ".ts"
    target = job_dir / f"nova-native-{job_id}{extension}"
    state = {"total": 0}
    with target.open("wb") as output:
        if init_uri:
            _download_resource(client, init_uri, output, on_update, state)
        for index, segment in enumerate(segments, start=1):
            _download_resource(client, segment, output, on_update, state)
            on_update(stage=f"Downloading HLS segment {index}/{len(segments)}")
    return target


def _dash_duration(value: str | None) -> float:
    if not value:
        return 0
    match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not match:
        return 0
    hours, minutes, seconds = (float(item or 0) for item in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element, name: str):
    return next((item for item in element if _local_name(item.tag) == name), None)


def _download_dash(client: httpx.Client, url: str, job_dir: Path, job_id: str, on_update) -> Path:
    text, final_url = _read_manifest(client, url)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise NativeEngineError("Unsafe XML declarations are blocked.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise NativeEngineError("The DASH manifest is malformed.") from exc
    if root.attrib.get("type", "static").lower() == "dynamic":
        raise NativeEngineError("Live/dynamic DASH is not supported by the native engine yet.")
    if any(_local_name(node.tag) == "ContentProtection" for node in root.iter()):
        raise NativeEngineError("Encrypted or DRM-protected DASH is not supported.")

    period = _child(root, "Period")
    if period is None:
        raise NativeEngineError("DASH manifest has no Period.")
    adaptation = _child(period, "AdaptationSet")
    if adaptation is None:
        raise NativeEngineError("DASH manifest has no AdaptationSet.")
    representations = [node for node in adaptation if _local_name(node.tag) == "Representation"]
    if len(representations) > 1:
        representations.sort(key=lambda node: int(node.attrib.get("bandwidth", "0") or 0), reverse=True)
    representation = representations[0] if representations else adaptation
    if any(_local_name(node.tag) == "AdaptationSet" for node in period if node is not adaptation):
        raise NativeEngineError("This DASH source separates audio/video adaptations; native muxing is not implemented yet.")

    base = _child(representation, "BaseURL") or _child(adaptation, "BaseURL") or _child(period, "BaseURL") or _child(root, "BaseURL")
    base_url = urljoin(final_url, (base.text or "").strip()) if base is not None and base.text else final_url
    seg_list = _child(representation, "SegmentList") or _child(adaptation, "SegmentList")
    if seg_list is None:
        if base_url == final_url:
            raise NativeEngineError("DASH manifest uses SegmentTemplate or SegmentBase; this native build currently supports SegmentList and direct BaseURL.")
        target = job_dir / f"nova-native-{job_id}{Path(urlsplit(base_url).path).suffix or '.mp4'}"
        state = {"total": 0}
        with target.open("wb") as output:
            _download_resource(client, base_url, output, on_update, state)
        return target

    items = []
    initialization = _child(seg_list, "Initialization")
    if initialization is not None and initialization.attrib.get("sourceURL"):
        items.append(urljoin(base_url, initialization.attrib["sourceURL"]))
    for item in seg_list:
        if _local_name(item.tag) == "SegmentURL" and item.attrib.get("media"):
            items.append(urljoin(base_url, item.attrib["media"]))
    if not items or len(items) > MAX_SEGMENTS:
        raise NativeEngineError("DASH SegmentList is empty or exceeds the segment limit.")

    target = job_dir / f"nova-native-{job_id}.mp4"
    state = {"total": 0}
    with target.open("wb") as output:
        for index, item in enumerate(items, start=1):
            _download_resource(client, item, output, on_update, state)
            on_update(stage=f"Downloading DASH segment {index}/{len(items)}")
    return target


def download(job: dict, on_update) -> Path:
    url = validate_external_url(job["url"])
    job_dir = DOWNLOAD_DIR / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    state = {"total": 0}

    with httpx.Client(timeout=httpx.Timeout(60, connect=20), follow_redirects=False) as client:
        path = urlsplit(url).path.lower()
        if path.endswith(".m3u8"):
            return _download_hls(client, url, job_dir, job["id"], on_update)
        if path.endswith(".mpd"):
            return _download_dash(client, url, job_dir, job["id"], on_update)

        response, final_url = _follow_get(client, url, stream=True)
        try:
            length = int(response.headers.get("content-length") or 0)
            if length > MAX_BYTES:
                raise NativeEngineError("The source file exceeds the configured size limit.")
            name = _content_disposition_name(response.headers.get("content-disposition"))
            suffix = _extension(final_url, response.headers.get("content-type"), name)
            stem = safe_filename(Path(name or Path(unquote(urlsplit(final_url).path)).name or "media-download").stem)
            target = job_dir / f"{stem}{suffix}"
            with target.open("wb") as output:
                for chunk in response.iter_bytes(256 * 1024):
                    current = sum(1 for _ in [])  # keep the progress callback deterministic
                    if not chunk:
                        continue
                    state["total"] += len(chunk)
                    if state["total"] > MAX_BYTES:
                        raise NativeEngineError("The download exceeds MAX_DOWNLOAD_GB.")
                    output.write(chunk)
                    percent = round(min(99.5, state["total"] * 100 / length), 1) if length else 0
                    on_update(percent=percent, downloaded_bytes=state["total"], total_bytes=length,
                               stage="Downloading with NOVA native engine")
        finally:
            response.close()
    if not target.exists() or target.stat().st_size == 0:
        raise NativeEngineError("The source returned an empty file.")
    return target
