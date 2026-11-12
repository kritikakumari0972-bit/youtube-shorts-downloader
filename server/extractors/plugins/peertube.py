from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from ..registry import ExtractorRegistry


_VIDEO_PATHS = (
    re.compile(r"^/videos/watch/([A-Za-z0-9_-]{8,64})/?$"),
    re.compile(r"^/w/([A-Za-z0-9_-]{8,64})/?$"),
)
_MAX_API_BYTES = 2 * 1024 * 1024
_USER_AGENT = "NOVA-FETCH/1.0 (public PeerTube metadata adapter)"


def _video_id(url: str) -> str | None:
    path = urlsplit(url).path
    for pattern in _VIDEO_PATHS:
        match = pattern.fullmatch(path)
        if match:
            return match.group(1)
    return None


def _duration_label(seconds: int) -> str:
    if seconds <= 0:
        return "Length not exposed by this source"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _positive_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _resolution(item: dict) -> int:
    value = _positive_int(item.get("height"))
    if value:
        return value
    resolution = item.get("resolution")
    if isinstance(resolution, dict):
        return _positive_int(resolution.get("id"))
    if isinstance(resolution, (str, int)):
        match = re.search(r"\d+", str(resolution))
        return int(match.group()) if match else 0
    return 0


def _public_media_url(raw_url: Any, page_url: str) -> str | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    from ...security import validate_external_url

    candidate = urljoin(page_url, raw_url.strip())
    try:
        return validate_external_url(candidate)
    except Exception:
        return None


def _read_video_details(url: str, video_id: str) -> dict[str, Any]:
    from ... import native_engine

    page = native_engine.validate_external_url(url)
    parsed = urlsplit(page)
    api_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/videos/{quote(video_id, safe='')}"
    api_url = native_engine.validate_external_url(api_url)

    with native_engine.httpx.Client(
        timeout=native_engine.httpx.Timeout(20, connect=10),
        follow_redirects=False,
    ) as client:
        response, _ = native_engine._request_following_redirects(
            client,
            api_url,
            stream=True,
            extra_headers={
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        try:
            content_type = response.headers.get("content-type", "").lower()
            if content_type and "json" not in content_type:
                raise RuntimeError("PeerTube's public video endpoint did not return JSON.")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes(64 * 1024):
                size += len(chunk)
                if size > _MAX_API_BYTES:
                    raise RuntimeError("PeerTube returned an oversized video metadata response.")
                chunks.append(chunk)
        finally:
            response.close()

    try:
        payload = json.loads(b"".join(chunks).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("PeerTube returned malformed video metadata.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("PeerTube returned an unexpected video metadata structure.")
    return payload


def _format_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    sources = list(payload.get("files") or [])
    for playlist in payload.get("streamingPlaylists") or []:
        if isinstance(playlist, dict):
            sources.extend(playlist.get("files") or [])

    for item in sources:
        if not isinstance(item, dict):
            continue
        height = _resolution(item)
        url = item.get("fileUrl") or item.get("fileDownloadUrl")
        ext = Path(urlsplit(url).path).suffix.lstrip(".").lower() if isinstance(url, str) else ""
        key = (height, ext)
        if key in seen:
            continue
        seen.add(key)
        if height or ext:
            entries.append({
                "height": height,
                "ext": ext or "auto",
                "has_audio": item.get("hasAudio") is not False,
            })
    entries.sort(key=lambda item: (item["height"], item["has_audio"]), reverse=True)
    return entries[:16]


def _select_media_url(
    payload: dict[str, Any],
    page_url: str,
    quality: str = "best",
    mode: str = "video",
) -> str | None:
    # Prefer PeerTube's public HLS manifest: NOVA Native can then apply the
    # requested quality to the playlist's own variants and audio renditions.
    playlists = payload.get("streamingPlaylists") or []
    if isinstance(playlists, list):
        for playlist in playlists:
            if not isinstance(playlist, dict):
                continue
            playlist_url = playlist.get("playlistUrl")
            playlist_type = _positive_int(playlist.get("type"))
            path = urlsplit(playlist_url).path.lower() if isinstance(playlist_url, str) else ""
            if playlist_url and (playlist_type == 1 or path.endswith(".m3u8")):
                safe = _public_media_url(playlist_url, page_url)
                if safe:
                    return safe

    # Some public instances disable HLS. Respect the requested quality cap
    # while choosing among the public progressive files.
    files = payload.get("files") or []
    candidates = []
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            if mode == "audio" and item.get("hasAudio") is False:
                continue
            if mode != "audio" and item.get("hasVideo") is False:
                continue
            raw_url = item.get("fileUrl") or item.get("fileDownloadUrl")
            safe = _public_media_url(raw_url, page_url)
            if safe:
                candidates.append((_resolution(item), _positive_int(item.get("size")), safe))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if quality == "best":
        return candidates[0][2]

    try:
        cap = int(quality)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return candidates[0][2]
    eligible = [item for item in candidates if 0 < item[0] <= cap]
    if eligible:
        return eligible[0][2]
    known = [item for item in candidates if item[0] > 0]
    if known:
        return min(known, key=lambda item: (item[0], -item[1]))[2]
    return candidates[0][2]

def _thumbnail(payload: dict[str, Any], page_url: str) -> str | None:
    thumbnails = payload.get("thumbnails")
    if isinstance(thumbnails, list):
        for item in sorted(
            (value for value in thumbnails if isinstance(value, dict)),
            key=lambda value: _positive_int(value.get("width")) * _positive_int(value.get("height")),
            reverse=True,
        ):
            candidate = _public_media_url(item.get("fileUrl"), page_url)
            if candidate:
                return candidate
    return _public_media_url(payload.get("thumbnailPath"), page_url)


class PeerTubeExtractor:
    """Resolves public PeerTube video pages through the instance's public REST API."""

    name = "nova-peertube"
    priority = 180

    def supports(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and _video_id(url) is not None
        )

    def extract(self, url: str) -> dict[str, Any] | None:
        return self._extract_with_context(url, {"quality": "best", "mode": "video"})

    def extract_for_job(self, url: str, context: dict[str, Any]) -> dict[str, Any] | None:
        return self._extract_with_context(url, context)

    def _extract_with_context(self, url: str, context: dict[str, Any]) -> dict[str, Any] | None:
        video_id = _video_id(url)
        if video_id is None:
            return None

        payload = _read_video_details(url, video_id)
        if payload.get("isLive"):
            # Live manifests are deliberately handed to the normal compatibility
            # path rather than pretending the static native engine supports them.
            return None

        quality = str(context.get("quality") or "best")
        mode = str(context.get("mode") or "video")
        media_url = _select_media_url(payload, url, quality=quality, mode=mode)
        if not media_url:
            raise RuntimeError(
                "This public PeerTube video exposes no downloadable media file compatible with the requested mode."
            )

        from ... import native_engine

        metadata = native_engine.preview(media_url)
        duration = payload.get("duration")
        try:
            duration_value = max(0, int(float(duration or 0)))
        except (TypeError, ValueError):
            duration_value = 0

        account = payload.get("account") or {}
        channel = payload.get("channel") or {}
        creator = (
            account.get("displayName") or account.get("name")
            if isinstance(account, dict) else ""
        ) or (
            channel.get("displayName") or channel.get("name")
            if isinstance(channel, dict) else ""
        ) or "PeerTube creator"

        return {
            "media_url": media_url,
            "title": str(payload.get("name") or metadata.get("title") or "PeerTube video")[:240],
            "creator": str(creator)[:160],
            "thumbnail": _thumbnail(payload, url),
            "source": "NOVA PeerTube adapter · public REST API",
            "metadata": {
                **metadata,
                "title": str(payload.get("name") or metadata.get("title") or "PeerTube video")[:240],
                "creator": str(creator)[:160],
                "thumbnail": _thumbnail(payload, url),
                "duration": duration_value,
                "duration_label": _duration_label(duration_value),
                "formats": _format_entries(payload) or metadata.get("formats", []),
                "source": "PeerTube · public instance API",
                "webpage_url": url,
                "is_live": False,
            },
        }

def register(registry: ExtractorRegistry) -> None:
    registry.register(PeerTubeExtractor())
