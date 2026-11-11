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



def test_hls_master_selects_quality_and_muxes_default_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    master = b"""#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",DEFAULT=YES,AUTOSELECT=YES,URI="audio-en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="audio"
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720,AUDIO="audio"
high.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080,AUDIO="audio"
ultra.m3u8
"""
    media = b"#EXTM3U\n#EXTINF:2,\nsegment.ts\n#EXT-X-ENDLIST\n"
    selected = {}

    def fake_mux(video, audio, job_id):
        selected["video"] = video
        selected["audio"] = audio
        output = tmp_path / "muxed.mp4"
        output.write_bytes(b"MUXED")
        return output

    monkeypatch.setattr(native_engine, "_mux_hls_tracks", fake_mux)
    with mock_client({
        "/master.m3u8": master,
        "/high.m3u8": media,
        "/audio-en.m3u8": media,
        "/segment.ts": b"SEGMENT",
    }) as client:
        output = native_engine._download_hls(
            client, "https://cdn.example/master.m3u8", tmp_path, "hls-job",
            lambda **kwargs: None, quality="720"
        )

    assert output.read_bytes() == b"MUXED"
    assert selected["video"].name.endswith("-video.ts")
    assert selected["audio"].name.endswith("-audio.ts")
    assert selected["video"].read_bytes() == b"SEGMENT"


def test_jsonld_video_object_resolves_content_url(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    html = b"""<html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "https://schema.org/VideoObject",
        "name": "Structured clip",
        "author": {"@type": "Person", "name": "NOVA creator"},
        "thumbnailUrl": "https://site.example/poster.jpg",
        "contentUrl": "https://cdn.example/structured.mp4",
        "encodingFormat": "video/mp4"
      }
      </script>
    </head></html>"""
    client = mock_client({"/watch/json": html})
    monkeypatch.setattr(native_engine.httpx, "Client", lambda **kwargs: client)

    result = native_engine.resolve_html5_media("https://site.example/watch/json")

    assert result is not None
    assert result["media_url"] == "https://cdn.example/structured.mp4"
    assert result["title"] == "Structured clip"
    assert result["creator"] == "NOVA creator"
    assert result["thumbnail"] == "https://site.example/poster.jpg"
