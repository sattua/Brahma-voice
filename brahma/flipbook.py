"""A flipbook from the accepted panels of one sequence.

The panels are the keyframes. ffmpeg's minterpolate estimates the frames between
each pair. Those frames are a motion guess, not a new drawing, and they are not
takes and not references. A hard cut warps.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from brahma.errors import BrahmaError
from brahma.sequence import load_sequence, panel_path

MINIMUM = 3
DEFAULT_BETWEEN = 5
DEFAULT_FPS = 8


def build_flipbook(folder: Path, *, between: int | None = None, fps: int | None = None) -> dict:
    """Write flipbook/frame-001.png ... and flipbook.mp4 from the accepted panels, in order."""

    steps = _bound("between", between, 1, 23, DEFAULT_BETWEEN)
    rate = _bound("fps", fps, 1, 24, DEFAULT_FPS)
    sequence = load_sequence(folder)
    keys = _keys(folder, sequence.accepted)
    with Image.open(keys[0]) as first:
        width, height = _even(first.size)
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        fitted = [_fit(path, work / f"key-{index:03d}.png", (width, height)) for index, path in enumerate(keys, start=1)]
        listing = work / "keys.txt"
        _concat(fitted, listing)
        frames = folder / "flipbook"
        frames.mkdir(parents=True, exist_ok=True)
        for old in frames.glob("frame-*.png"):
            old.unlink()
        _ffmpeg(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-vf", f"minterpolate=fps={steps + 1}:mi_mode=mci",
                "-start_number", "1",
                str(frames / "frame-%03d.png"),
            ]
        )
        kept = _keep(frames, len(keys), steps)
        for index, source in enumerate(fitted):
            _paste(source, frames / f"frame-{index * (steps + 1) + 1:03d}.png")
    video = folder / "flipbook.mp4"
    _ffmpeg(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(rate), "-i", str(frames / "frame-%03d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(video),
        ]
    )
    return {
        "mp4": str(video),
        "frames": str(frames),
        "frame_count": kept,
        "panels": list(sequence.accepted),
        "between": steps,
        "fps": rate,
        "note": "Frames between the panels are a motion estimate. A hard cut warps.",
    }


def _bound(name: str, value: int | None, low: int, high: int, default: int) -> int:
    if value is None:
        return default
    number = int(value)
    if number < low or number > high:
        raise BrahmaError(f"{name} is from {low} to {high}")
    return number


def _keys(folder: Path, accepted: list[int]) -> list[Path]:
    found = []
    for index in accepted:
        path = panel_path(folder, index)
        if not path.is_file():
            raise BrahmaError(f"accepted panel {index} has no picture")
        found.append(path)
    if len(found) < MINIMUM:
        raise BrahmaError(f"a flipbook needs at least {MINIMUM} accepted panels; this sequence has {len(found)}")
    return found


def _even(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    width -= width % 2
    height -= height % 2
    if width < 2 or height < 2:
        raise BrahmaError("a flipbook panel is too small")
    return width, height


def _fit(source: Path, dest: Path, size: tuple[int, int]) -> Path:
    with Image.open(source) as image:
        frame = image.convert("RGB")
        if frame.size != size:
            frame = frame.resize(size, Image.Resampling.LANCZOS)
        frame.save(dest, format="PNG")
    return dest


def _concat(keys: list[Path], dest: Path) -> None:
    """Each key lasts one second. The last file is repeated so the demuxer reaches it."""

    lines = ["ffconcat version 1.0"]
    for path in keys:
        lines.append(f"file '{_quote(path)}'")
        lines.append("duration 1")
    lines.append(f"file '{_quote(keys[-1])}'")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def _keep(frames: Path, keys: int, between: int) -> int:
    """Drop the hold after the last key. The last kept frame is that key."""

    wanted = (keys - 1) * (between + 1) + 1
    found = sorted(frames.glob("frame-*.png"))
    if len(found) < wanted:
        raise BrahmaError(f"ffmpeg wrote {len(found)} frames, and the flipbook needs {wanted}")
    for extra in found[wanted:]:
        extra.unlink()
    return wanted


def _paste(source: Path, dest: Path) -> None:
    with Image.open(source) as image:
        image.convert("RGB").save(dest, format="PNG")


def _ffmpeg(args: list[str]) -> None:
    if shutil.which("ffmpeg") is None:
        raise BrahmaError("ffmpeg is not installed")
    done = subprocess.run(args, check=False, capture_output=True, text=True)
    if done.returncode != 0:
        tail = " ".join((done.stderr or "").strip().splitlines()[-6:])
        raise BrahmaError(f"ffmpeg failed: {tail or 'no output'}")
