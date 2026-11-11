import httpx
import pytest

from server import native_engine


def mock_client(routes):
    def handler(request):
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=body, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_detects_native_media_urls():
    assert native_engine.is_native_url("https://cdn.example/video.mp4")
    assert native_engine.is_native_url("https://cdn.example/list.m3u8?token=abc")
    assert native_engine.is_native_url("https://cdn.example/manifest.mpd")
    assert not native_engine.is_native_url("https://example.com/watch/123")


def test_hls_concatenates_unencrypted_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    manifest = b"#EXTM3U\n#EXTINF:2.0,\nfirst.ts\n#EXTINF:2.0,\nsecond.ts\n#EXT-X-ENDLIST\n"
    with mock_client({
        "/list.m3u8": manifest,
        "/first.ts": b"FIRST",
        "/second.ts": b"SECOND",
    }) as client:
        output = native_engine._download_hls(
            client, "https://cdn.example/list.m3u8", tmp_path, "job1",
            lambda **kwargs: None
        )
    assert output.suffix == ".ts"
    assert output.read_bytes() == b"FIRSTSECOND"


def test_hls_refuses_encrypted_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    manifest = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:2,\na.ts\n#EXT-X-ENDLIST\n'
    with mock_client({"/list.m3u8": manifest}) as client:
        with pytest.raises(native_engine.NativeEngineError, match="Encrypted or DRM-protected"):
            native_engine._download_hls(
                client, "https://cdn.example/list.m3u8", tmp_path, "job2",
                lambda **kwargs: None
            )


def test_dash_segment_list_combines_initialization_and_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    mpd = b"""<?xml version="1.0"?>
    <MPD type="static">
      <Period>
        <AdaptationSet>
          <BaseURL>https://cdn.example/media/</BaseURL>
          <Representation id="v1">
            <SegmentList>
              <Initialization sourceURL="init.mp4"/>
              <SegmentURL media="part-1.m4s"/>
              <SegmentURL media="part-2.m4s"/>
            </SegmentList>
          </Representation>
        </AdaptationSet>
      </Period>
    </MPD>"""
    with mock_client({
        "/manifest.mpd": mpd,
        "/media/init.mp4": b"INIT",
        "/media/part-1.m4s": b"ONE",
        "/media/part-2.m4s": b"TWO",
    }) as client:
        output = native_engine._download_dash(
            client, "https://cdn.example/manifest.mpd", tmp_path, "job3",
            lambda **kwargs: None
        )
    assert output.suffix == ".mp4"
    assert output.read_bytes() == b"INITONETWO"


def test_dash_refuses_content_protection(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    mpd = b"""<MPD><Period><AdaptationSet><ContentProtection schemeIdUri="urn:uuid:test"/></AdaptationSet></Period></MPD>"""
    with mock_client({"/manifest.mpd": mpd}) as client:
        with pytest.raises(native_engine.NativeEngineError, match="DRM-protected"):
            native_engine._download_dash(
                client, "https://cdn.example/manifest.mpd", tmp_path, "job4",
                lambda **kwargs: None
            )



def test_html5_page_resolves_open_graph_video(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    html = b"""<html><head>
      <title>My clip</title>
      <meta property="og:video" content="/assets/clip.mp4">
      <meta property="og:image" content="/assets/poster.jpg">
    </head><body></body></html>"""
    client = mock_client({"/watch/1": html})
    monkeypatch.setattr(native_engine.httpx, "Client", lambda **kwargs: client)

    result = native_engine.resolve_html5_media("https://site.example/watch/1")

    assert result is not None
    assert result["media_url"] == "https://site.example/assets/clip.mp4"
    assert result["title"] == "My clip"
    assert result["thumbnail"] == "https://site.example/assets/poster.jpg"
