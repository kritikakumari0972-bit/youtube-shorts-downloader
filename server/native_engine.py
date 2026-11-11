from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

from .security import safe_filename, validate_external_url

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).expanduser().resolve()
DOWNLOAD_DIR = DATA_DIR / "downloads"
MAX_BYTES = int(float(os.getenv("MAX_DOWNLOAD_GB", "8")) * 1024**3)
MAX_MANIFEST_BYTES = 3 * 1024 * 1024
MAX_SEGMENTS = 20_000
MAX_REDIRECTS = 5
MAX_REQUEST_RETRIES = 2
HEADERS = {"User-Agent": "NOVA-FETCH/1.0 (self-hosted media client)", "Accept": "*/*"}


class NativeEngineError(RuntimeError):
    """A source or manifest cannot be handled safely by NOVA Native."""


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
    candidate = Path(filename).suffix if filename else ""
    if not candidate:
        candidate = Path(urlsplit(url).path).suffix
    if candidate and re.fullmatch(r"\.[a-zA-Z0-9]{1,8}", candidate):
        return candidate.lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return mimetypes.guess_extension(mime) or ".bin"


def _request_following_redirects(
    client: httpx.Client,
    url: str,
    stream: bool,
    extra_headers: dict[str, str] | None = None,
):
    """GET a validated URL, retry transient failures, and validate every redirect."""
    current = validate_external_url(url)
    request_headers = {**HEADERS, **(extra_headers or {})}
    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}

    for _ in range(MAX_REDIRECTS + 1):
        response = None
        for attempt in range(MAX_REQUEST_RETRIES + 1):
            try:
                request = client.build_request("GET", current, headers=request_headers)
                response = client.send(request, stream=stream, follow_redirects=False)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= MAX_REQUEST_RETRIES:
                    raise
                time.sleep(min(0.25 * (2 ** attempt), 2.0))
                continue

            if response.status_code in retryable_statuses and attempt < MAX_REQUEST_RETRIES:
                retry_after = response.headers.get("retry-after", "")
                response.close()
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 0.25 * (2 ** attempt)
                time.sleep(max(0.0, min(delay, 3.0)))
                continue
            break

        if response is None:
            raise NativeEngineError("The source request failed without a response.")
        if response.status_code in retryable_statuses:
            response.close()
            response.raise_for_status()

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
    if path.endswith(".m3u8"):
        kind, extension = "HLS playlist", "HLS"
    elif path.endswith(".mpd"):
        kind, extension = "MPEG-DASH manifest", "DASH"
    else:
        kind, extension = "Direct media file", Path(name).suffix.lstrip(".") or "auto"
    return {
        "title": name,
        "creator": "Direct source",
        "thumbnail": None,
        "duration": 0,
        "duration_label": "Length not exposed by this source",
        "source": kind,
        "webpage_url": safe,
        "formats": [{"height": 0, "ext": extension, "has_audio": True}],
        "is_live": False,
        "playlist_count": None,
    }


class _MediaPageParser(HTMLParser):
    """Extract directly exposed HTML5 media URLs and Open Graph metadata."""

    MEDIA_META = {
        "og:video", "og:video:url", "og:video:secure_url",
        "twitter:player:stream",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[str, str]] = []
        self.title = ""
        self.creator = ""
        self.thumbnail = ""
        self.jsonld_documents: list[object] = []
        self._jsonld_chunks: list[str] = []
        self._in_jsonld = False
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag == "script" and attrs.get("type", "").lower().split(";", 1)[0].strip() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_chunks = []
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            value = attrs.get("content", "").strip()
            if key in self.MEDIA_META and value:
                self.candidates.append((value, attrs.get("type", "")))
            elif key in {"og:title", "twitter:title"} and value and not self.title:
                self.title = value
            elif key in {"og:video:secure_url", "og:image", "twitter:image"} and value and not self.thumbnail:
                if key != "og:video:secure_url":
                    self.thumbnail = value
            elif key in {"author", "article:author"} and value and not self.creator:
                self.creator = value
        if tag in {"video", "audio", "source"} and attrs.get("src"):
            self.candidates.append((attrs["src"].strip(), attrs.get("type", "")))
        if tag == "video" and attrs.get("poster") and not self.thumbnail:
            self.thumbnail = attrs["poster"].strip()
        if tag == "link" and attrs.get("href"):
            rel = attrs.get("rel", "").lower().split()
            mime = attrs.get("type", "").lower()
            if "alternate" in rel and any(marker in mime for marker in ("mpegurl", "dash+xml")):
                self.candidates.append((attrs["href"].strip(), mime))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            raw = "".join(self._jsonld_chunks).strip()
            self._in_jsonld = False
            self._jsonld_chunks = []
            if raw:
                try:
                    self.jsonld_documents.append(json.loads(raw))
                except (ValueError, TypeError):
                    # Pages frequently contain malformed unrelated JSON-LD blocks.
                    pass

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_chunks.append(data)
        if self._in_title and data.strip() and not self.title:
            self.title = data.strip()


def resolve_html5_media(url: str) -> dict | None:
    """Resolve public HTML pages that expose a direct video/audio source in markup.

    This intentionally does not run JavaScript, sign requests, read browser cookies,
    or access private/app-only streams.
    """
    page_url = validate_external_url(url)
    with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=False) as client:
        response, final_url = _request_following_redirects(client, page_url, stream=True)
        try:
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml+xml")):
                return None
            chunks = []
            size = 0
            for chunk in response.iter_bytes(64 * 1024):
                size += len(chunk)
                if size > MAX_MANIFEST_BYTES:
                    raise NativeEngineError("The source HTML is larger than the configured limit.")
                chunks.append(chunk)
        finally:
            response.close()

    try:
        html = b"".join(chunks).decode("utf-8", errors="replace")
    except Exception:
        return None
    parser = _MediaPageParser()
    parser.feed(html)

    def visit_jsonld(node):
        if isinstance(node, list):
            for child in node:
                yield from visit_jsonld(child)
        elif isinstance(node, dict):
            yield node
            graph = node.get("@graph")
            if graph is not None:
                yield from visit_jsonld(graph)
            for key, value in node.items():
                if key != "@graph" and isinstance(value, (dict, list)):
                    yield from visit_jsonld(value)

    for document in parser.jsonld_documents:
        for item in visit_jsonld(document):
            raw_types = item.get("@type", [])
            if isinstance(raw_types, str):
                raw_types = [raw_types]
            types = {
                str(value).rsplit("/", 1)[-1].rsplit("#", 1)[-1]
                for value in (raw_types or [])
            }
            if not types.intersection({"VideoObject", "AudioObject", "MediaObject"}):
                continue
            candidate = item.get("contentUrl")
            if isinstance(candidate, dict):
                candidate = candidate.get("@id") or candidate.get("url")
            mime = str(item.get("encodingFormat") or item.get("fileFormat") or item.get("mimeType") or "")
            if isinstance(candidate, str) and candidate.strip():
                parser.candidates.append((candidate.strip(), mime))
            if not parser.title and item.get("name"):
                parser.title = str(item["name"])[:240]
            author = item.get("author") or item.get("creator")
            if not parser.creator and author:
                if isinstance(author, list):
                    author = author[0] if author else ""
                if isinstance(author, dict):
                    author = author.get("name") or author.get("@id") or ""
                if isinstance(author, str):
                    parser.creator = author[:160]
            thumbnail = item.get("thumbnailUrl") or item.get("thumbnail")
            if not parser.thumbnail and thumbnail:
                if isinstance(thumbnail, list):
                    thumbnail = thumbnail[0] if thumbnail else ""
                if isinstance(thumbnail, dict):
                    thumbnail = thumbnail.get("url") or thumbnail.get("@id") or ""
                if isinstance(thumbnail, str):
                    parser.thumbnail = thumbnail

    for raw_url, mime in parser.candidates:
        candidate = urljoin(final_url, raw_url)
        try:
            candidate = validate_external_url(candidate)
        except Exception:
            continue
        path = urlsplit(candidate).path.lower()
        known_manifest = path.endswith((".m3u8", ".mpd"))
        known_media = is_native_url(candidate)
        typed_media = mime.lower().startswith(("video/", "audio/")) or any(
            marker in mime.lower() for marker in ("mpegurl", "dash+xml")
        )
        if known_media or known_manifest or typed_media:
            return {
                "media_url": candidate,
                "title": (parser.title or Path(unquote(urlsplit(candidate).path)).name or "Web media")[:240],
                "creator": parser.creator[:160] or "Public HTML5 source",
                "thumbnail": urljoin(final_url, parser.thumbnail) if parser.thumbnail else None,
                "source": "NOVA HTML5 resolver",
            }
    return None


def _read_manifest(client: httpx.Client, url: str) -> tuple[str, str]:
    response, final_url = _request_following_redirects(client, url, stream=True)
    try:
        chunks = []
        size = 0
        for chunk in response.iter_bytes(64 * 1024):
            size += len(chunk)
            if size > MAX_MANIFEST_BYTES:
                raise NativeEngineError("The media manifest is larger than the configured limit.")
            chunks.append(chunk)
        try:
            return b"".join(chunks).decode("utf-8-sig"), final_url
        except UnicodeDecodeError as exc:
            raise NativeEngineError("The media manifest is not valid UTF-8 text.") from exc
    finally:
        response.close()


def _write_resource(
    client: httpx.Client,
    url: str,
    output,
    on_update,
    state: dict,
    total_hint: int = 0,
    byte_range: tuple[int, int] | None = None,
):
    """Stream a media resource, optionally requiring one exact HTTP byte range."""
    extra_headers = None
    expected_length = None
    if byte_range is not None:
        offset, length = byte_range
        if offset < 0 or length <= 0:
            raise NativeEngineError("The requested media byte range is invalid.")
        end = offset + length - 1
        extra_headers = {"Range": f"bytes={offset}-{end}"}
        expected_length = length

    response, _ = _request_following_redirects(
        client, url, stream=True, extra_headers=extra_headers
    )
    try:
        if expected_length is not None:
            if response.status_code != 206:
                raise NativeEngineError(
                    "The source ignored the HLS byte-range request; refusing to save the full resource as a segment."
                )
            content_range = response.headers.get("content-range", "")
            match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range.strip(), re.I)
            if not match or int(match.group(1)) != offset or int(match.group(2)) != end:
                raise NativeEngineError("The source returned a mismatched Content-Range for an HLS segment.")

        written = 0
        for chunk in response.iter_bytes(256 * 1024):
            if not chunk:
                continue
            written += len(chunk)
            state["total"] += len(chunk)
            if state["total"] > MAX_BYTES:
                raise NativeEngineError("The download exceeds MAX_DOWNLOAD_GB.")
            if expected_length is not None and written > expected_length:
                raise NativeEngineError("The source returned more bytes than the requested HLS range.")
            output.write(chunk)
            total = total_hint or expected_length or int(response.headers.get("content-length") or 0)
            percent = round(min(99.5, state["total"] * 100 / total), 1) if total else 0
            on_update(percent=percent, downloaded_bytes=state["total"], total_bytes=total,
                      stage="Downloading with NOVA Native")
        if expected_length is not None and written != expected_length:
            raise NativeEngineError("The source returned an incomplete HLS byte range.")
    finally:
        response.close()



def _parse_hls_attrs(value: str) -> dict[str, str]:
    attrs = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("(?:[^"]*)"|[^,]*)', value):
        attrs[match.group(1)] = match.group(2).strip('"')
    return attrs


def _parse_hls_byterange(value: str) -> tuple[int, int | None]:
    """Parse HLS length[@offset] byte-range notation."""
    match = re.fullmatch(r"\s*(\d+)(?:@(\d+))?\s*", value or "")
    if not match:
        raise NativeEngineError("The HLS playlist contains a malformed BYTERANGE.")
    length = int(match.group(1))
    offset = int(match.group(2)) if match.group(2) is not None else None
    if length <= 0 or length > MAX_BYTES:
        raise NativeEngineError("The HLS byte range has an invalid or excessive length.")
    return length, offset


def _resolve_hls_byterange(
    raw_range: str,
    resource_url: str,
    previous_ends: dict[str, int],
) -> tuple[int, int]:
    length, offset = _parse_hls_byterange(raw_range)
    if offset is None:
        offset = previous_ends.get(resource_url)
        if offset is None:
            raise NativeEngineError(
                "An HLS byte range omitted its offset without a previous range for the same resource."
            )
    previous_ends[resource_url] = offset + length
    return offset, length


def _download_hls_media_playlist(
    client: httpx.Client,
    playlist_url: str,
    target: Path,
    on_update,
    state: dict,
) -> Path:
    text, final_url = _read_manifest(client, playlist_url)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if not lines or lines[0] != "#EXTM3U":
        raise NativeEngineError("The HLS media playlist is malformed.")
    if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
        raise NativeEngineError("Nested HLS master playlists are not supported.")
    if "#EXT-X-ENDLIST" not in lines:
        raise NativeEngineError("Live or unfinished HLS playlists are not supported by NOVA Native.")

    for line in lines:
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            method = _parse_hls_attrs(line.split(":", 1)[1]).get("METHOD", "")
            if method != "NONE":
                raise NativeEngineError("Encrypted or DRM-protected HLS is not supported.")

    previous_ends: dict[str, int] = {}
    init_resource: tuple[str, tuple[int, int] | None] | None = None
    segments: list[tuple[str, tuple[int, int] | None]] = []
    pending_range: str | None = None

    for line in lines:
        if line.startswith("#EXT-X-BYTERANGE:"):
            pending_range = line.split(":", 1)[1].strip()
        elif line.startswith("#EXT-X-MAP:"):
            attrs = _parse_hls_attrs(line.split(":", 1)[1])
            raw_uri = attrs.get("URI")
            if not raw_uri:
                raise NativeEngineError("HLS initialization segment is malformed.")
            init_url = urljoin(final_url, raw_uri)
            init_range = (
                _resolve_hls_byterange(attrs["BYTERANGE"], init_url, previous_ends)
                if attrs.get("BYTERANGE") else None
            )
            init_resource = (init_url, init_range)
        elif not line.startswith("#"):
            segment_url = urljoin(final_url, line)
            segment_range = (
                _resolve_hls_byterange(pending_range, segment_url, previous_ends)
                if pending_range is not None else None
            )
            segments.append((segment_url, segment_range))
            pending_range = None

    if pending_range is not None:
        raise NativeEngineError("The HLS playlist ends with a BYTERANGE that has no media segment.")
    if not segments or len(segments) > MAX_SEGMENTS:
        raise NativeEngineError("The HLS playlist is empty or exceeds the segment limit.")

    with target.open("wb") as output:
        if init_resource:
            init_url, init_range = init_resource
            _write_resource(
                client, init_url, output, on_update, state, byte_range=init_range
            )
        for index, (segment_url, segment_range) in enumerate(segments, start=1):
            _write_resource(
                client, segment_url, output, on_update, state, byte_range=segment_range
            )
            on_update(stage=f"NOVA Native · HLS segment {index}/{len(segments)}")
    return target



def _choose_hls_variant(variants: list[dict], quality: str) -> dict:
    if not variants:
        raise NativeEngineError("The HLS master playlist contains no selectable video variants.")
    variants.sort(key=lambda item: (item["height"], item["bandwidth"]), reverse=True)
    if quality == "best":
        return variants[0]
    cap = int(quality)
    eligible = [item for item in variants if item["height"] and item["height"] <= cap]
    if eligible:
        return max(eligible, key=lambda item: (item["height"], item["bandwidth"]))
    known = [item for item in variants if item["height"]]
    if known:
        return min(known, key=lambda item: (item["height"], -item["bandwidth"]))
    return variants[-1]


def _mux_hls_tracks(video_track: Path, audio_track: Path, job_id: str) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NativeEngineError("This HLS variant has separate audio/video tracks. Install the local FFmpeg binary to mux them.")
    output = video_track.parent / f"nova-native-{job_id}.mp4"
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(video_track), "-i", str(audio_track),
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
         "-movflags", "+faststart", str(output)],
        capture_output=True, text=True, timeout=900, check=False
    )
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise NativeEngineError(f"FFmpeg could not mux the HLS tracks: {(result.stderr or 'unknown error')[-300:]}")
    return output


def _download_hls(
    client: httpx.Client,
    url: str,
    job_dir: Path,
    job_id: str,
    on_update,
    quality: str = "best",
) -> Path:
    text, final_url = _read_manifest(client, url)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise NativeEngineError("The HLS playlist is malformed.")

    variants = []
    audio_renditions = []
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_hls_attrs(line.split(":", 1)[1])
            if attrs.get("TYPE") == "AUDIO":
                audio_renditions.append(attrs)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = _parse_hls_attrs(line.split(":", 1)[1])
            next_uri = next((item for item in lines[index + 1:] if not item.startswith("#")), None)
            if not next_uri:
                raise NativeEngineError("HLS master playlist has a variant without a URI.")
            resolution = attrs.get("RESOLUTION", "")
            height = 0
            match = re.fullmatch(r"\d+x(\d+)", resolution)
            if match:
                height = int(match.group(1))
            variants.append({
                "bandwidth": int(attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH") or 0),
                "height": height,
                "url": urljoin(final_url, next_uri),
                "audio_group": attrs.get("AUDIO"),
            })

    if variants:
        selected = _choose_hls_variant(variants, quality)
        video_path = job_dir / f"nova-native-{job_id}-video.ts"
        state = {"total": 0}
        _download_hls_media_playlist(client, selected["url"], video_path, on_update, state)

        group = selected.get("audio_group")
        rendition_candidates = [
            item for item in audio_renditions
            if group and item.get("GROUP-ID") == group and item.get("URI")
        ]
        if rendition_candidates:
            rendition = next((item for item in rendition_candidates if item.get("DEFAULT") == "YES"), None)
            if rendition is None:
                rendition = next((item for item in rendition_candidates if item.get("AUTOSELECT") == "YES"), None)
            if rendition is None:
                rendition = rendition_candidates[0]
            audio_url = urljoin(final_url, rendition["URI"])
            audio_path = job_dir / f"nova-native-{job_id}-audio.ts"
            _download_hls_media_playlist(client, audio_url, audio_path, on_update, state)
            return _mux_hls_tracks(video_path, audio_path, job_id)
        return video_path

    # A media playlist (rather than a master playlist).
    extension = ".mp4" if any(line.startswith("#EXT-X-MAP:") for line in lines) else ".ts"
    target = job_dir / f"nova-native-{job_id}{extension}"
    return _download_hls_media_playlist(client, url, target, on_update, {"total": 0})


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element, name: str):
    if element is None:
        return None
    return next((item for item in element if _local_name(item.tag) == name), None)


def _first_not_none(*values):
    return next((value for value in values if value is not None), None)


def _parse_iso_duration(value: str | None) -> float:
    if not value:
        return 0.0
    match = re.fullmatch(
        r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        value,
    )
    if not match:
        return 0.0
    days, hours, minutes, seconds = (float(item or 0) for item in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _choose_dash_representation(representations: list, quality: str):
    if not representations:
        return None
    if quality == "best":
        return max(representations, key=lambda node: (
            int(node.attrib.get("height", "0") or 0),
            int(node.attrib.get("bandwidth", "0") or 0),
        ))
    cap = int(quality)
    eligible = [
        node for node in representations
        if 0 < int(node.attrib.get("height", "0") or 0) <= cap
    ]
    if eligible:
        return max(eligible, key=lambda node: (
            int(node.attrib.get("height", "0") or 0),
            int(node.attrib.get("bandwidth", "0") or 0),
        ))
    known = [node for node in representations if int(node.attrib.get("height", "0") or 0) > 0]
    if known:
        return min(known, key=lambda node: (
            int(node.attrib.get("height", "0") or 0),
            -int(node.attrib.get("bandwidth", "0") or 0),
        ))
    return max(representations, key=lambda node: int(node.attrib.get("bandwidth", "0") or 0))


def _dash_template_value(pattern: str, *, representation_id: str, bandwidth: int,
                         number: int | None = None, timestamp: int | None = None) -> str:
    marker = "\x00NOVA_DOLLAR\x00"
    value = pattern.replace("$$", marker)

    def replace(match):
        token, width = match.group(1), match.group(2)
        if token == "RepresentationID":
            return representation_id
        if token == "Bandwidth":
            return str(bandwidth)
        if token == "Number":
            if number is None:
                raise NativeEngineError("DASH template requires a segment number that was not supplied.")
            return f"{number:0{int(width)}d}" if width else str(number)
        if token == "Time":
            if timestamp is None:
                raise NativeEngineError("DASH template requires a segment time that was not supplied.")
            return str(timestamp)
        return match.group(0)

    value = re.sub(r"\$(RepresentationID|Bandwidth|Number|Time)(?:%0(\d+)d)?\$", replace, value)
    if re.search(r"\$[^$]+\$", value):
        raise NativeEngineError("The DASH media template contains an unsupported variable.")
    return value.replace(marker, "$")


def _dash_template_segments(template: dict, timeline, *, period_duration: float,
                            representation_id: str, bandwidth: int) -> tuple[str | None, list[str]]:
    media = template.get("media")
    if not media:
        raise NativeEngineError("DASH SegmentTemplate has no media URL pattern.")
    initialization = template.get("initialization")
    init_url = (
        _dash_template_value(initialization, representation_id=representation_id, bandwidth=bandwidth)
        if initialization else None
    )
    start_number = int(template.get("startNumber", "1") or 1)
    timescale = int(template.get("timescale", "1") or 1)
    if timescale <= 0:
        raise NativeEngineError("DASH SegmentTemplate timescale must be greater than zero.")

    segments: list[str] = []
    if timeline is not None:
        entries = [item for item in timeline if _local_name(item.tag) == "S"]
        current_time = 0
        number = start_number
        for index, entry in enumerate(entries):
            duration = int(entry.attrib.get("d", "0") or 0)
            if duration <= 0:
                raise NativeEngineError("DASH SegmentTimeline contains a segment without a positive duration.")
            if "t" in entry.attrib:
                current_time = int(entry.attrib["t"])
            repeat = int(entry.attrib.get("r", "0") or 0)
            if repeat < -1:
                raise NativeEngineError("DASH SegmentTimeline has an invalid negative repeat count.")
            if repeat == -1:
                next_time = None
                if index + 1 < len(entries) and "t" in entries[index + 1].attrib:
                    next_time = int(entries[index + 1].attrib["t"])
                if next_time is None and period_duration:
                    next_time = int(period_duration * timescale)
                if next_time is None:
                    raise NativeEngineError("DASH SegmentTimeline r=-1 needs a period duration or a following explicit timestamp.")
                repeats = max(0, (next_time - current_time + duration - 1) // duration - 1)
            else:
                repeats = repeat
            if len(segments) + repeats + 1 > MAX_SEGMENTS:
                raise NativeEngineError("DASH SegmentTimeline exceeds the segment limit.")
            for _ in range(repeats + 1):
                segments.append(_dash_template_value(
                    media, representation_id=representation_id, bandwidth=bandwidth,
                    number=number, timestamp=current_time
                ))
                number += 1
                current_time += duration
        return init_url, segments

    duration = int(template.get("duration", "0") or 0)
    if duration <= 0:
        raise NativeEngineError("DASH SegmentTemplate needs SegmentTimeline or a positive duration.")
    if period_duration <= 0:
        raise NativeEngineError("DASH duration-based SegmentTemplate needs a known static period duration.")
    count = (int(period_duration * timescale) + duration - 1) // duration
    if count < 1 or count > MAX_SEGMENTS:
        raise NativeEngineError("DASH SegmentTemplate segment count is invalid or exceeds the limit.")
    for index in range(count):
        segments.append(_dash_template_value(
            media, representation_id=representation_id, bandwidth=bandwidth,
            number=start_number + index, timestamp=index * duration
        ))
    return init_url, segments


def _dash_inherited_template(*elements) -> tuple[dict, object | None]:
    attributes: dict[str, str] = {}
    timeline = None
    found = False
    for element in elements:
        template = _child(element, "SegmentTemplate")
        if template is None:
            continue
        found = True
        attributes.update(template.attrib)
        candidate_timeline = _child(template, "SegmentTimeline")
        if candidate_timeline is not None:
            timeline = candidate_timeline
    return (attributes if found else {}, timeline)


def _write_dash_template(
    client: httpx.Client, base_url: str, template: dict, timeline, period_duration: float,
    representation, adaptation, target: Path, on_update, state: dict | None = None,
) -> Path:
    representation_id = str(representation.attrib.get("id", "0"))
    bandwidth = int(representation.attrib.get("bandwidth", adaptation.attrib.get("bandwidth", "0")) or 0)
    init_name, segment_names = _dash_template_segments(
        template, timeline, period_duration=period_duration,
        representation_id=representation_id, bandwidth=bandwidth
    )
    if not segment_names:
        raise NativeEngineError("DASH SegmentTemplate resolved to no media segments.")

    resources = []
    if init_name:
        resources.append(urljoin(base_url, init_name))
    resources.extend(urljoin(base_url, name) for name in segment_names)
    if state is None:
        state = {"total": 0}
    with target.open("wb") as output:
        for index, resource in enumerate(resources, start=1):
            _write_resource(client, resource, output, on_update, state)
            on_update(stage=f"NOVA Native · DASH segment {index}/{len(resources)}")
    return target

def _dash_adaptation_kind(adaptation) -> str:
    """Identify audio/video/text adaptations using public MPD metadata."""
    representations = [node for node in adaptation if _local_name(node.tag) == "Representation"]
    nodes = [adaptation, *representations]
    content_types = [str(node.attrib.get("contentType", "")).lower() for node in nodes]
    mime_types = [str(node.attrib.get("mimeType", "")).lower() for node in nodes]
    codecs = " ".join(str(node.attrib.get("codecs", "")) for node in nodes).lower()
    description = " ".join(content_types + mime_types + [codecs])

    if any(token in description for token in ("text/", "subtitle", "ttml", "stpp", "wvtt")):
        return "text"
    # Prefer the explicit media type over codec substrings: some multiplexed
    # codec declarations can mention both an audio and a video codec.
    if any(value == "video" or value.startswith("video/") for value in content_types + mime_types):
        return "video"
    if any(value == "audio" or value.startswith("audio/") for value in content_types + mime_types):
        return "audio"
    if any(codec in codecs for codec in ("avc1", "avc3", "hev1", "hvc1", "vp09", "vp9", "av01")):
        return "video"
    if any(codec in codecs for codec in ("mp4a", "aac", "opus", "vorbis", "ac-3", "ec-3", "flac")):
        return "audio"
    for node in nodes:
        if int(node.attrib.get("height", "0") or 0) > 0:
            return "video"
        if node.attrib.get("audioSamplingRate") or _child(node, "AudioChannelConfiguration") is not None:
            return "audio"
    return "unknown"

def _dash_adaptation_score(adaptation, track_kind: str) -> tuple[int, int]:
    representations = [node for node in adaptation if _local_name(node.tag) == "Representation"]
    if not representations:
        representations = [adaptation]
    if track_kind == "video":
        return (
            max((int(node.attrib.get("height", "0") or 0) for node in representations), default=0),
            max((int(node.attrib.get("bandwidth", "0") or 0) for node in representations), default=0),
        )
    return (
        0,
        max((int(node.attrib.get("bandwidth", "0") or 0) for node in representations), default=0),
    )


def _download_dash_adaptation(
    client: httpx.Client,
    final_url: str,
    root,
    period,
    adaptation,
    job_dir: Path,
    target: Path,
    quality: str,
    on_update,
    state: dict | None = None,
) -> Path:
    if state is None:
        state = {"total": 0}
    representations = [node for node in adaptation if _local_name(node.tag) == "Representation"]
    representation = _choose_dash_representation(representations, quality) if representations else adaptation
    if representation is None:
        raise NativeEngineError("The DASH adaptation has no usable representation.")

    # BaseURL is hierarchical: each child URL is relative to its parent's URL.
    base_url = final_url
    for element in (root, period, adaptation, representation):
        base = _child(element, "BaseURL")
        if base is not None and base.text and base.text.strip():
            base_url = urljoin(base_url, base.text.strip())

    period_duration = _parse_iso_duration(period.attrib.get("duration"))
    if not period_duration:
        total_duration = _parse_iso_duration(root.attrib.get("mediaPresentationDuration"))
        period_start = _parse_iso_duration(period.attrib.get("start")) if period.attrib.get("start") else 0
        period_duration = max(0.0, total_duration - period_start)

    template, timeline = _dash_inherited_template(root, period, adaptation, representation)
    if template:
        target = target.with_suffix(".webm" if "webm" in (
            str(representation.attrib.get("mimeType") or adaptation.attrib.get("mimeType") or "")
        ).lower() else ".mp4")
        return _write_dash_template(
            client, base_url, template, timeline, period_duration,
            representation, adaptation, target, on_update, state
        )

    segment_list = _first_not_none(
        _child(representation, "SegmentList"),
        _child(adaptation, "SegmentList"),
        _child(period, "SegmentList"),
        _child(root, "SegmentList"),
    )
    if segment_list is None:
        if base_url == final_url:
            raise NativeEngineError(
                "This DASH adaptation uses SegmentBase or has no resolvable BaseURL."
            )
        extension = Path(urlsplit(base_url).path).suffix or target.suffix or ".mp4"
        target = target.with_suffix(extension)
        with target.open("wb") as output:
            _write_resource(client, base_url, output, on_update, state)
        return target

    resources = []
    initialization = _child(segment_list, "Initialization")
    if initialization is not None:
        if initialization.attrib.get("range"):
            raise NativeEngineError("DASH Initialization byte ranges are not supported by NOVA Native yet.")
        if initialization.attrib.get("sourceURL"):
            resources.append(urljoin(base_url, initialization.attrib["sourceURL"]))
    for item in segment_list:
        if _local_name(item.tag) == "SegmentURL":
            if item.attrib.get("mediaRange"):
                raise NativeEngineError("DASH SegmentURL mediaRange is not supported by NOVA Native yet.")
            if item.attrib.get("media"):
                resources.append(urljoin(base_url, item.attrib["media"]))
    if not resources or len(resources) > MAX_SEGMENTS:
        raise NativeEngineError("DASH SegmentList is empty or exceeds the segment limit.")

    with target.open("wb") as output:
        for index, resource in enumerate(resources, start=1):
            _write_resource(client, resource, output, on_update, state)
            on_update(stage=f"NOVA Native · DASH segment {index}/{len(resources)}")
    return target


def _mux_dash_tracks(video_track: Path, audio_track: Path, job_id: str) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NativeEngineError(
            "This DASH source uses separate audio and video tracks. Install the local FFmpeg binary to mux them."
        )
    suffixes = {video_track.suffix.lower(), audio_track.suffix.lower()}
    output_ext = ".webm" if suffixes == {".webm"} else ".mkv" if ".webm" in suffixes else ".mp4"
    output = video_track.parent / f"nova-native-{job_id}{output_ext}"
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(video_track), "-i", str(audio_track),
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
         *(["-f", "webm"] if output_ext == ".webm" else []),
         *(["-f", "matroska"] if output_ext == ".mkv" else []),
         "-movflags", "+faststart" if output_ext == ".mp4" else "+faststart", str(output)],
        capture_output=True, text=True, timeout=900, check=False
    )
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise NativeEngineError(
            f"FFmpeg could not mux the DASH tracks: {(result.stderr or 'unknown error')[-300:]}"
        )
    return output

def _download_dash(
    client: httpx.Client,
    url: str,
    job_dir: Path,
    job_id: str,
    on_update,
    quality: str = "best",
) -> Path:
    text, final_url = _read_manifest(client, url)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise NativeEngineError("Unsafe XML declarations are blocked.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise NativeEngineError("The DASH manifest is malformed.") from exc

    if root.attrib.get("type", "static").lower() == "dynamic":
        raise NativeEngineError("Live/dynamic DASH is not supported by NOVA Native.")
    if any(_local_name(node.tag) == "ContentProtection" for node in root.iter()):
        raise NativeEngineError("Encrypted or DRM-protected DASH is not supported.")

    periods = [node for node in root if _local_name(node.tag) == "Period"]
    if len(periods) != 1:
        raise NativeEngineError("NOVA Native currently supports one static DASH Period per manifest.")
    period = periods[0]
    adaptations = [node for node in period if _local_name(node.tag) == "AdaptationSet"]
    if not adaptations:
        raise NativeEngineError("The DASH Period has no AdaptationSet.")

    typed = {kind: [] for kind in ("video", "audio", "text", "unknown")}
    for adaptation in adaptations:
        typed[_dash_adaptation_kind(adaptation)].append(adaptation)

    # When audio and video are published as separate adaptations, fetch the
    # highest compatible video representation and the best available audio
    # representation independently, then mux the tracks locally with FFmpeg.
    if typed["video"] and typed["audio"]:
        video_adaptation = max(
            typed["video"], key=lambda node: _dash_adaptation_score(node, "video")
        )
        audio_adaptation = max(
            typed["audio"], key=lambda node: _dash_adaptation_score(node, "audio")
        )
        state = {"total": 0}
        video_path = _download_dash_adaptation(
            client, final_url, root, period, video_adaptation, job_dir,
            job_dir / f"nova-native-{job_id}-video.mp4", quality, on_update, state
        )
        audio_path = _download_dash_adaptation(
            client, final_url, root, period, audio_adaptation, job_dir,
            job_dir / f"nova-native-{job_id}-audio.mp4", "best", on_update, state
        )
        on_update(stage="NOVA Native · Muxing DASH audio and video")
        return _mux_dash_tracks(video_path, audio_path, job_id)

    if len(adaptations) == 1:
        return _download_dash_adaptation(
            client, final_url, root, period, adaptations[0], job_dir,
            job_dir / f"nova-native-{job_id}.mp4", quality, on_update
        )

    candidates = typed["video"] or typed["audio"]
    if candidates:
        selected = max(
            candidates,
            key=lambda node: _dash_adaptation_score(
                node, "video" if typed["video"] else "audio"
            )
        )
        return _download_dash_adaptation(
            client, final_url, root, period, selected, job_dir,
            job_dir / f"nova-native-{job_id}.mp4", quality, on_update
        )

    raise NativeEngineError(
        "NOVA Native could not identify a supported DASH audio or video adaptation. "
        "Text/subtitle-only and ambiguous adaptations are not downloaded as media."
    )






def _extract_mp3(source: Path, job: dict) -> Path:
    if source.suffix.lower() == ".mp3":
        return source
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NativeEngineError("Audio conversion needs the local FFmpeg binary. Install the media tools included in the NOVA FETCH Docker image.")
    target = source.with_suffix(".mp3")
    bitrate = str(job.get("audio_bitrate", "192"))
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), "-vn", "-b:a", f"{bitrate}k", str(target)],
        capture_output=True, text=True, timeout=600, check=False
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise NativeEngineError(f"FFmpeg audio conversion failed: {(result.stderr or 'unknown error')[-300:]}")
    return target


def download(job: dict, on_update) -> Path:
    url = validate_external_url(job["url"])
    job_dir = DOWNLOAD_DIR / job["id"]
    job_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=httpx.Timeout(60, connect=20), follow_redirects=False) as client:
        path = urlsplit(url).path.lower()
        if path.endswith(".m3u8"):
            output = _download_hls(client, url, job_dir, job["id"], on_update, quality=job.get("quality", "best"))
        elif path.endswith(".mpd"):
            output = _download_dash(client, url, job_dir, job["id"], on_update, quality=job.get("quality", "best"))
        else:
            response, final_url = _request_following_redirects(client, url, stream=True)
            try:
                length = int(response.headers.get("content-length") or 0)
                if length > MAX_BYTES:
                    raise NativeEngineError("The source file exceeds the configured size limit.")
                name = _content_disposition_name(response.headers.get("content-disposition"))
                suffix = _extension(final_url, response.headers.get("content-type"), name)
                stem = safe_filename(Path(name or Path(unquote(urlsplit(final_url).path)).name or "media-download").stem)
                output = job_dir / f"{stem}{suffix}"
                received = 0
                with output.open("wb") as file:
                    for chunk in response.iter_bytes(256 * 1024):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > MAX_BYTES:
                            raise NativeEngineError("The download exceeds MAX_DOWNLOAD_GB.")
                        file.write(chunk)
                        percent = round(min(99.5, received * 100 / length), 1) if length else 0
                        on_update(percent=percent, downloaded_bytes=received, total_bytes=length,
                                  stage="Downloading with NOVA Native")
            finally:
                response.close()

    if not output.exists() or output.stat().st_size == 0:
        raise NativeEngineError("The source returned an empty file.")
    if job.get("mode") == "audio":
        output = _extract_mp3(output, job)
    return output
