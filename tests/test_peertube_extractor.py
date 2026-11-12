import json
from urllib.parse import urlsplit

import httpx
import pytest

from server import native_engine, security
from server.extractors.plugins.peertube import PeerTubeExtractor


PAGE_URL = "https://videos.example.test/videos/watch/12345678-1234-1234-1234-123456789abc"
API_PATH = "/api/v1/videos/12345678-1234-1234-1234-123456789abc"


def mock_client(routes):
    def handler(request):
        response = routes.get(request.url.path)
        if response is None:
            return httpx.Response(404, request=request)
        if callable(response):
            return response(request)
        return httpx.Response(200, json=response, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def install_client(monkeypatch, routes, validate=None):
    client = mock_client(routes)
    monkeypatch.setattr(native_engine.httpx, "Client", lambda **kwargs: client)
    check = validate or (lambda value: value)
    monkeypatch.setattr(native_engine, "validate_external_url", check)
    monkeypatch.setattr(security, "validate_external_url", check)
    return client


def test_peertube_supports_watch_and_short_routes_only():
    extractor = PeerTubeExtractor()
    assert extractor.supports(PAGE_URL)
    assert extractor.supports("https://videos.example.test/w/AbCdEfGh1234")
    assert not extractor.supports("https://videos.example.test/videos")
    assert not extractor.supports("https://videos.example.test/videos/watch/short")
    assert not extractor.supports("file:///videos/watch/12345678")


def test_peertube_prefers_public_hls_and_returns_metadata(monkeypatch):
    payload = {
        "name": "Open Media Demo",
        "duration": 65,
        "isLive": False,
        "account": {"displayName": "NOVA Publisher"},
        "thumbnails": [
            {"fileUrl": "/lazy.jpg", "width": 320, "height": 180},
            {"fileUrl": "/large.jpg", "width": 1280, "height": 720},
        ],
        "files": [
            {
                "fileUrl": "https://cdn.example.test/video-720.mp4",
                "resolution": {"id": 720, "label": "720p"},
                "size": 1200,
                "hasAudio": True,
            }
        ],
        "streamingPlaylists": [
            {
                "type": 1,
                "playlistUrl": "https://videos.example.test/static/streaming-playlists/hls/123/master.m3u8",
                "files": [
                    {
                        "fileUrl": "https://cdn.example.test/fragmented-720.mp4",
                        "resolution": {"id": 720, "label": "720p"},
                        "hasAudio": False,
                    }
                ],
            }
        ],
    }
    install_client(monkeypatch, {API_PATH: payload})

    result = PeerTubeExtractor().extract(PAGE_URL)

    assert result is not None
    assert result["media_url"].endswith("/master.m3u8")
    assert result["title"] == "Open Media Demo"
    assert result["creator"] == "NOVA Publisher"
    assert result["thumbnail"] == "https://videos.example.test/large.jpg"
    assert result["source"] == "NOVA PeerTube adapter · public REST API"
    metadata = result["metadata"]
    assert metadata["duration"] == 65
    assert metadata["duration_label"] == "1:05"
    assert metadata["webpage_url"] == PAGE_URL
    assert any(item["height"] == 720 for item in metadata["formats"])


def test_peertube_falls_back_to_highest_progressive_file_when_hls_disabled(monkeypatch):
    payload = {
        "name": "Progressive only",
        "duration": 20,
        "files": [
            {
                "fileUrl": "https://cdn.example.test/240.mp4",
                "resolution": {"id": 240},
                "size": 100,
            },
            {
                "fileUrl": "https://cdn.example.test/1080.mp4",
                "resolution": {"id": 1080},
                "size": 900,
            },
            {"fileUrl": "https://cdn.example.test/720.mp4", "height": 720, "size": 400},
        ],
        "streamingPlaylists": [],
    }
    install_client(monkeypatch, {API_PATH: payload})

    result = PeerTubeExtractor().extract(PAGE_URL)

    assert result is not None
    assert result["media_url"] == "https://cdn.example.test/1080.mp4"


def test_peertube_skips_live_streams(monkeypatch):
    install_client(monkeypatch, {API_PATH: {"name": "Live", "isLive": True, "streamingPlaylists": []}})
    assert PeerTubeExtractor().extract(PAGE_URL) is None


def test_peertube_rejects_unvalidated_cdn_media(monkeypatch):
    def validate(value):
        host = urlsplit(value).hostname
        if host == "private.example.test":
            raise ValueError("blocked host")
        return value

    payload = {
        "name": "Bad CDN",
        "files": [{"fileUrl": "https://private.example.test/private.mp4", "height": 720}],
    }
    install_client(monkeypatch, {API_PATH: payload}, validate=validate)

    with pytest.raises(RuntimeError, match="no downloadable"):
        PeerTubeExtractor().extract(PAGE_URL)


def test_peertube_rejects_oversized_metadata(monkeypatch):
    def response(request):
        return httpx.Response(
            200,
            content=b" " * (2 * 1024 * 1024 + 1),
            headers={"content-type": "application/json"},
            request=request,
        )

    install_client(monkeypatch, {API_PATH: response})
    with pytest.raises(RuntimeError, match="oversized"):
        PeerTubeExtractor().extract(PAGE_URL)


def test_peertube_rejects_malformed_json(monkeypatch):
    def response(request):
        return httpx.Response(
            200, content=b"{not json",
            headers={"content-type": "application/json"},
            request=request,
        )

    install_client(monkeypatch, {API_PATH: response})
    with pytest.raises(RuntimeError, match="malformed"):
        PeerTubeExtractor().extract(PAGE_URL)


def test_peertube_api_redirect_is_followed_by_validated_native_request(monkeypatch):
    payload = {"name": "Redirected public video", "files": [
        {"fileUrl": "https://cdn.example.test/video.mp4", "height": 360}
    ]}
    client = mock_client({
        API_PATH: lambda request: httpx.Response(
            302, headers={"Location": "https://videos.example.test/api/v1/videos/redirected"},
            request=request,
        ),
        "/api/v1/videos/redirected": payload,
    })
    seen = []
    def validate(value):
        seen.append(value)
        return value
    monkeypatch.setattr(native_engine.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(native_engine, "validate_external_url", validate)
    monkeypatch.setattr(security, "validate_external_url", validate)

    result = PeerTubeExtractor().extract(PAGE_URL)

    assert result is not None
    assert result["title"] == "Redirected public video"
    assert any(value.endswith("/api/v1/videos/redirected") for value in seen)
