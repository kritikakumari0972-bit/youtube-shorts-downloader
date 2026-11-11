import httpx
import pytest

from server import native_engine


def client_for(routes):
    def handler(request):
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=payload, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_dash_segment_template_expands_representation_and_number(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    mpd = b"""<MPD type="static" mediaPresentationDuration="PT6S">
      <Period>
        <AdaptationSet mimeType="video/mp4">
          <BaseURL>https://cdn.example/media/</BaseURL>
          <SegmentTemplate timescale="1" startNumber="5"
             initialization="init-$RepresentationID$.mp4"
             media="chunk-$RepresentationID$-$Number%03d$.m4s">
            <SegmentTimeline>
              <S t="0" d="2" r="2"/>
            </SegmentTimeline>
          </SegmentTemplate>
          <Representation id="v360" bandwidth="400000" height="360"/>
          <Representation id="v720" bandwidth="900000" height="720"/>
          <Representation id="v1080" bandwidth="1800000" height="1080"/>
        </AdaptationSet>
      </Period>
    </MPD>"""
    routes = {
        "/manifest.mpd": mpd,
        "/media/init-v720.mp4": b"INIT",
        "/media/chunk-v720-005.m4s": b"ONE",
        "/media/chunk-v720-006.m4s": b"TWO",
        "/media/chunk-v720-007.m4s": b"THREE",
    }
    with client_for(routes) as client:
        output = native_engine._download_dash(
            client, "https://cdn.example/manifest.mpd", tmp_path, "dash-job",
            lambda **kwargs: None, quality="720"
        )
    assert output.read_bytes() == b"INITONETWOTTHREE".replace(b"TTHREE", b"THREE")
    assert output.suffix == ".mp4"


def test_dash_duration_template_generates_expected_segment_count(tmp_path, monkeypatch):
    monkeypatch.setattr(native_engine, "validate_external_url", lambda value: value)
    mpd = b"""<MPD type="static" mediaPresentationDuration="PT5S">
      <Period>
        <AdaptationSet mimeType="video/mp4">
          <BaseURL>https://cdn.example/</BaseURL>
          <SegmentTemplate timescale="1" duration="2" startNumber="1"
             initialization="init.mp4" media="seg-$Number$.m4s"/>
          <Representation id="v1" bandwidth="500000" height="480"/>
        </AdaptationSet>
      </Period>
    </MPD>"""
    routes = {
        "/manifest.mpd": mpd,
        "/init.mp4": b"I",
        "/seg-1.m4s": b"A",
        "/seg-2.m4s": b"B",
        "/seg-3.m4s": b"C",
    }
    with client_for(routes) as client:
        output = native_engine._download_dash(
            client, "https://cdn.example/manifest.mpd", tmp_path, "dash-duration",
            lambda **kwargs: None, quality="best"
        )
    assert output.read_bytes() == b"IABC"


def test_dash_template_rejects_unbounded_negative_repeat():
    timeline = type("Timeline", (), {})()
    import xml.etree.ElementTree as ET
    timeline = ET.fromstring('<SegmentTimeline><S d="2" r="-1"/></SegmentTimeline>')
    template = {"media": "seg-$Number$.m4s", "duration": "2", "startNumber": "1", "timescale": "1"}
    with pytest.raises(native_engine.NativeEngineError, match="needs a period duration"):
        native_engine._dash_template_segments(
            template, timeline, period_duration=0, representation_id="v1", bandwidth=1000
        )
