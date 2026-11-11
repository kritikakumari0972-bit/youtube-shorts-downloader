from server.engines import format_selector


def test_audio_selector_uses_best_audio():
    assert format_selector("audio", "1080") == "bestaudio/best"


def test_best_video_selector_merges_audio_and_video():
    assert format_selector("video", "best") == "bv*+ba/b"


def test_quality_selector_applies_height_cap():
    assert format_selector("video", "720") == "bv*[height<=720]+ba/b[height<=720]/b"
