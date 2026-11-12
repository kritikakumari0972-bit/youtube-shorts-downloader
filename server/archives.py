from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


SUBTITLE_SUFFIXES = {".srt", ".vtt", ".ass", ".ssa", ".lrc", ".ttml"}


def package_subtitles(output: Path, job_dir: Path) -> Path:
    """Bundle subtitle sidecars alongside the downloaded media file."""
    subtitles = [
        item for item in job_dir.rglob("*")
        if item.is_file() and item.suffix.lower() in SUBTITLE_SUFFIXES and item.stat().st_size
    ]
    if not subtitles:
        return output

    if output.suffix.lower() == ".zip":
        archive = output
        mode = "a"
    else:
        archive = job_dir / f"{output.stem}-with-subtitles.zip"
        mode = "w"

    with ZipFile(archive, mode, compression=ZIP_DEFLATED, compresslevel=3) as bundle:
        existing = set(bundle.namelist())
        if mode == "w":
            bundle.write(output, arcname=output.name)
        for item in subtitles:
            name = item.name
            if name not in existing and item.resolve() != archive.resolve():
                bundle.write(item, arcname=name)
                existing.add(name)
    return archive
