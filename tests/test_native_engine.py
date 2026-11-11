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


def test_hls_byte_ranges_support_explicit_and_implicit_offsets(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    manifest = b"""#EXTM3U
#EXTINF:1,
#EXT-X-BYTERANGE:4@0
blob.bin
#EXTINF:1,
#EXT-X-BYTERANGE:3
blob.bin
#EXT-X-ENDLIST
"""
    source = b"AAAABBBCC"

    def handler(request):
        if request.url.path == "/list.m3u8":
            return httpx.Response(200, content=manifest, request=request)
        value = request.headers.get("range", "")
        match = __import__("re").fullmatch(r"bytes=(\d+)-(\d+)", value)
        if not match:
            return httpx.Response(400, request=request)
        start, end = int(match.group(1)), int(match.group(2))
        return httpx.Response(
            206,
            content=source[start:end + 1],
            headers={"Content-Range": f"bytes {start}-{end}/{len(source)}"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        output = native_engine._download_hls(
            client, "https://cdn.example/list.m3u8", tmp_path, "range-job",
            lambda **kwargs: None
        )

    assert output.read_bytes() == b"AAAABBB"


def test_hls_byte_range_refuses_server_that_ignores_range(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    manifest = b"#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:3@0\nblob.bin\n#EXT-X-ENDLIST\n"

    def handler(request):
        if request.url.path == "/list.m3u8":
            return httpx.Response(200, content=manifest, request=request)
        return httpx.Response(200, content=b"WHOLE RESOURCE", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(native_engine.NativeEngineError, match="ignored the HLS byte-range"):
            native_engine._download_hls(
                client, "https://cdn.example/list.m3u8", tmp_path, "range-ignore",
                lambda **kwargs: None
            )


def test_dash_separate_video_and_audio_adaptations_are_muxed(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    mpd = b"""<MPD type="static" mediaPresentationDuration="PT4S">
      <Period duration="PT4S">
        <AdaptationSet contentType="video" mimeType="video/mp4" codecs="avc1.64001f">
          <BaseURL>https://cdn.example/video/</BaseURL>
          <SegmentTemplate timescale="1" duration="2"
             initialization="init-$RepresentationID$.mp4"
             media="seg-$RepresentationID$-$Number$.m4s"/>
          <Representation id="v360" bandwidth="400000" height="360"/>
          <Representation id="v720" bandwidth="900000" height="720"/>
          <Representation id="v1080" bandwidth="1800000" height="1080"/>
        </AdaptationSet>
        <AdaptationSet contentType="audio" mimeType="audio/mp4" codecs="mp4a.40.2">
          <BaseURL>https://cdn.example/audio/</BaseURL>
          <SegmentTemplate timescale="1" duration="2"
             initialization="init-$RepresentationID$.mp4"
             media="seg-$RepresentationID$-$Number$.m4s"/>
          <Representation id="a128" bandwidth="128000"/>
        </AdaptationSet>
      </Period>
    </MPD>"""
    routes = {
        "/manifest.mpd": mpd,
        "/video/init-v720.mp4": b"VIDEO-INIT",
        "/video/seg-v720-1.m4s": b"VIDEO-ONE",
        "/video/seg-v720-2.m4s": b"VIDEO-TWO",
        "/audio/init-a128.mp4": b"AUDIO-INIT",
        "/audio/seg-a128-1.m4s": b"AUDIO-ONE",
        "/audio/seg-a128-2.m4s": b"AUDIO-TWO",
    }
    tracks = {}

    def fake_mux(video, audio, job_id):
        tracks["video"] = video.read_bytes()
        tracks["audio"] = audio.read_bytes()
        output = tmp_path / f"{job_id}-muxed.mp4"
        output.write_bytes(b"MUXED-AUDIO-VIDEO")
        return output

    monkeypatch.setattr(native_engine, "_mux_dash_tracks", fake_mux)
    with mock_client(routes) as client:
        output = native_engine._download_dash(
            client, "https://cdn.example/manifest.mpd", tmp_path, "multi-track",
            lambda **kwargs: None, quality="720"
        )

    assert output.read_bytes() == b"MUXED-AUDIO-VIDEO"
    assert tracks["video"] == b"VIDEO-INITVIDEO-ONEVIDEO-TWO"
    assert tracks["audio"] == b"AUDIO-INITAUDIO-ONEAUDIO-TWO"


def test_dash_video_adaptation_is_classified_before_mixed_codec_tokens():
    import xml.etree.ElementTree as ET

    video = ET.fromstring(
        '<AdaptationSet contentType="video" mimeType="video/mp4" codecs="avc1.640028,mp4a.40.2"/>'
    )
    audio = ET.fromstring(
        '<AdaptationSet contentType="audio" mimeType="audio/mp4" codecs="mp4a.40.2"/>'
    )
    subtitles = ET.fromstring(
        '<AdaptationSet mimeType="application/ttml+xml"/>'
    )

    assert native_engine._dash_adaptation_kind(video) == "video"
    assert native_engine._dash_adaptation_kind(audio) == "audio"
    assert native_engine._dash_adaptation_kind(subtitles) == "text"


def test_manifest_request_retries_transient_server_error(monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    monkeypatch.setattr(native_engine.time, "sleep", lambda seconds: None)
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=b"manifest-ok", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        content, final_url = native_engine._read_manifest(
            client, "https://cdn.example/manifest.mpd"
        )

    assert content == "manifest-ok"
    assert final_url == "https://cdn.example/manifest.mpd"
    assert calls["count"] == 2


def test_hls_initialization_map_supports_byte_range(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    manifest = b"""#EXTM3U
#EXT-X-MAP:URI="container.mp4",BYTERANGE="4@0"
#EXTINF:1,
#EXT-X-BYTERANGE:3@4
container.mp4
#EXT-X-ENDLIST
"""
    source = b"INITMEDIATA"

    def handler(request):
        if request.url.path == "/list.m3u8":
            return httpx.Response(200, content=manifest, request=request)
        match = __import__("re").fullmatch(r"bytes=(\d+)-(\d+)", request.headers.get("range", ""))
        if not match:
            return httpx.Response(400, request=request)
        start, end = int(match.group(1)), int(match.group(2))
        return httpx.Response(
            206, content=source[start:end + 1],
            headers={"Content-Range": f"bytes {start}-{end}/{len(source)}"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        output = native_engine._download_hls(
            client, "https://cdn.example/list.m3u8", tmp_path, "map-range",
            lambda **kwargs: None
        )

    assert output.read_bytes() == b"INITMED"


def test_hls_implicit_range_without_previous_same_resource_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    manifest = b"#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:4\ncontainer.bin\n#EXT-X-ENDLIST\n"
    with mock_client({"/list.m3u8": manifest}) as client:
        with pytest.raises(native_engine.NativeEngineError, match="omitted its offset"):
            native_engine._download_hls(
                client, "https://cdn.example/list.m3u8", tmp_path, "implicit-invalid",
                lambda **kwargs: None
            )
