from pathlib import Path
from zipfile import ZipFile

from server.archives import package_subtitles


def test_subtitles_are_bundled_with_media(tmp_path: Path):
    job = tmp_path / "job"
    job.mkdir()
    media = job / "sample.mp4"
    subtitle = job / "sample.en.srt"
    media.write_bytes(b"fake-media")
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    result = package_subtitles(media, job)
    assert result.suffix == ".zip"
    with ZipFile(result) as archive:
        assert set(archive.namelist()) == {"sample.mp4", "sample.en.srt"}


def test_no_subtitle_keeps_original_media(tmp_path: Path):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"fake-media")
    assert package_subtitles(media, tmp_path) == media
