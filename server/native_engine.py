from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
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


def _request_following_redirects(client: httpx.Client, url: str, stream: bool):
    current = validate_external_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        request = client.build_request("GET", current, headers=HEADERS)
        response = client.send(request, stream=stream, follow_redirects=False)
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
            types = {raw_types} if isinstance(raw_types, str) else set(raw_types or [])
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


def _write_resource(client: httpx.Client, url: str, output, on_update, state: dict, total_hint: int = 0):
    response, _ = _request_following_redirects(client, url, stream=True)
    try:
        for chunk in response.iter_bytes(256 * 1024):
            if not chunk:
                continue
            state["total"] += len(chunk)
            if state["total"] > MAX_BYTES:
                raise NativeEngineError("The download exceeds MAX_DOWNLOAD_GB.")
            output.write(chunk)
            total = total_hint or int(response.headers.get("content-length") or 0)
            percent = round(min(99.5, state["total"] * 100 / total), 1) if total else 0
            on_update(percent=percent, downloaded_bytes=state["total"], total_bytes=total,
                      stage="Downloading with NOVA Native")
    finally:
        response.close()


def _parse_hls_attrs(value: str) -> dict[str, str]:
    attrs = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("(?:[^"]*)"|[^,]*)', value):
        attrs[match.group(1)] = match.group(2).strip('"')
    return attrs


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
        raise NativeEngineError("Nested HLS master playlists are not supported yet.")
    if "#EXT-X-ENDLIST" not in lines:
        raise NativeEngineError("Live or unfinished HLS playlists are not supported by NOVA Native yet.")
    if any(line.startswith("#EXT-X-BYTERANGE") for line in lines):
        raise NativeEngineError("This HLS playlist uses byte ranges, which NOVA Native does not support yet.")

    for line in lines:
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            method = _parse_hls_attrs(line.split(":", 1)[1]).get("METHOD", "")
            if method != "NONE":
                raise NativeEngineError("Encrypted or DRM-protected HLS is not supported.")

    init_uri = None
    segments = []
    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_hls_attrs(line.split(":", 1)[1])
            if not attrs.get("URI"):
                raise NativeEngineError("HLS initialization segment is malformed.")
            init_uri = urljoin(final_url, attrs["URI"])
        elif not line.startswith("#"):
            segments.append(urljoin(final_url, line))

    if not segments or len(segments) > MAX_SEGMENTS:
        raise NativeEngineError("The HLS playlist is empty or exceeds the segment limit.")

    with target.open("wb") as output:
        if init_uri:
            _write_resource(client, init_uri, output, on_update, state)
        for index, segment in enumerate(segments, start=1):
            _write_resource(client, segment, output, on_update, state)
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
    representation, adaptation, target: Path, on_update,
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
    state = {"total": 0}
    with target.open("wb") as output:
        for index, resource in enumerate(resources, start=1):
            _write_resource(client, resource, output, on_update, state)
            on_update(stage=f"NOVA Native · DASH segment {index}/{len(resources)}")
    return target


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
        raise NativeEngineError("Live/dynamic DASH is not supported by NOVA Native yet.")
    if any(_local_name(node.tag) == "ContentProtection" for node in root.iter()):
        raise NativeEngineError("Encrypted or DRM-protected DASH is not supported.")

    periods = [node for node in root if _local_name(node.tag) == "Period"]
    if len(periods) != 1:
        raise NativeEngineError("NOVA Native currently supports one static DASH Period per manifest.")
    period = periods[0]
    adaptations = [node for node in period if _local_name(node.tag) == "AdaptationSet"]
    if len(adaptations) != 1:
        raise NativeEngineError("This DASH source has separate or multiple audio/video adaptations. Multi-track DASH muxing is a later NOVA milestone.")
    adaptation = adaptations[0]

    representations = [node for node in adaptation if _local_name(node.tag) == "Representation"]
    representation = _choose_dash_representation(representations, quality) if representations else adaptation
    if representation is None:
        raise NativeEngineError("The DASH manifest has no usable representation.")

    base = _first_not_none(
        _child(representation, "BaseURL"), _child(adaptation, "BaseURL"),
        _child(period, "BaseURL"), _child(root, "BaseURL")
    )
    base_url = urljoin(final_url, (base.text or "").strip()) if base is not None and base.text else final_url
    period_duration = _parse_iso_duration(period.attrib.get("duration"))
    if not period_duration:
        total_duration = _parse_iso_duration(root.attrib.get("mediaPresentationDuration"))
        period_start = _parse_iso_duration(period.attrib.get("start")) if period.attrib.get("start") else 0
        period_duration = max(0.0, total_duration - period_start)

    # SegmentTemplate attributes inherit from MPD -> Period -> AdaptationSet
    # -> Representation, with the most specific declaration winning.
    template, timeline = _dash_inherited_template(root, period, adaptation, representation)
    if template:
        mime = str(representation.attrib.get("mimeType") or adaptation.attrib.get("mimeType") or "")
        extension = ".webm" if "webm" in mime else ".mp4"
        target = job_dir / f"nova-native-{job_id}{extension}"
        return _write_dash_template(
            client, base_url, template, timeline, period_duration,
            representation, adaptation, target, on_update
        )

    segment_list = _first_not_none(_child(representation, "SegmentList"), _child(adaptation, "SegmentList"))
    if segment_list is None:
        if base_url == final_url:
            raise NativeEngineError("This DASH manifest uses an unsupported SegmentBase or has no resolvable BaseURL.")
        extension = Path(urlsplit(base_url).path).suffix or ".mp4"
        target = job_dir / f"nova-native-{job_id}{extension}"
        state = {"total": 0}
        with target.open("wb") as output:
            _write_resource(client, base_url, output, on_update, state)
        return target

    resources = []
    initialization = _child(segment_list, "Initialization")
    if initialization is not None and initialization.attrib.get("sourceURL"):
        resources.append(urljoin(base_url, initialization.attrib["sourceURL"]))
    for item in segment_list:
        if _local_name(item.tag) == "SegmentURL" and item.attrib.get("media"):
            resources.append(urljoin(base_url, item.attrib["media"]))
    if not resources or len(resources) > MAX_SEGMENTS:
        raise NativeEngineError("DASH SegmentList is empty or exceeds the segment limit.")

    target = job_dir / f"nova-native-{job_id}.mp4"
    state = {"total": 0}
    with target.open("wb") as output:
        for index, resource in enumerate(resources, start=1):
            _write_resource(client, resource, output, on_update, state)
            on_update(stage=f"NOVA Native · DASH segment {index}/{len(resources)}")
    return target




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
