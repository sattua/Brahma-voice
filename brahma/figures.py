"""Crops that serve as references.

A full-body picture is a stance. The image tool copies that stance and ignores a sentence
that asks for another one. A crop gives the part that must hold (a head, a sky, a brush
stroke, one view of a turnaround) and leaves the rest out.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from brahma.errors import BrahmaError
from brahma.models import VIEWS

# A column whose pixels are all lighter than this is the white gap between two views.
_GAP_LUMA = 236


def top_band(source: Path, dest: Path, fraction: float) -> Path:
    """The top of a picture: a head on a full-body sheet, the sky on a wide panel."""

    if not 0 < fraction <= 1:
        raise BrahmaError(f"band fraction {fraction} is not between 0 and 1")
    with Image.open(source) as image:
        width, height = image.size
        band = image.crop((0, 0, width, max(1, int(height * fraction))))
        return _save(band, dest)


def box_crop(source: Path, dest: Path, box: list[float] | tuple[float, ...]) -> Path:
    """A crop given as fractions [left, top, right, bottom] of the picture."""

    left, top, right, bottom = _fractions(box)
    with Image.open(source) as image:
        width, height = image.size
        crop = image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))
        return _save(crop, dest)


def view_columns(source: Path, count: int = len(VIEWS)) -> list[tuple[int, int]]:
    """Left and right pixel edges of each figure in a turnaround strip.

    Views are found by the white gaps between them. A strip with no clean gaps is split
    into equal columns.
    """

    with Image.open(source) as image:
        gray = image.convert("L")
        width, height = gray.size
        pixels = gray.load()
        step = max(1, height // 200)
        filled = [any(pixels[x, y] < _GAP_LUMA for y in range(0, height, step)) for x in range(width)]
    runs: list[list[int]] = []
    for x, busy in enumerate(filled):
        if not busy:
            continue
        if runs and x - runs[-1][1] <= 2:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    runs = [run for run in runs if run[1] - run[0] >= width // (count * 8)]
    while len(runs) > count:
        gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
        i = gaps.index(min(gaps))
        runs[i] = [runs[i][0], runs[i + 1][1]]
        del runs[i + 1]
    if len(runs) != count:
        edge = width / count
        return [(int(i * edge), int((i + 1) * edge)) for i in range(count)]
    return [(left, right + 1) for left, right in runs]


def view_crop(turnaround: Path, view: str, dest: Path, order: tuple[str, ...] | list[str] = VIEWS) -> Path:
    """One figure from a turnaround strip, by view name."""

    order = tuple(order)
    if view not in order:
        raise BrahmaError(f"turnaround has no {view} view; it has {', '.join(order)}")
    left, right = view_columns(turnaround, len(order))[order.index(view)]
    with Image.open(turnaround) as image:
        return _save(image.crop((left, 0, right, image.size[1])), dest)


def _fractions(box) -> tuple[float, float, float, float]:
    if len(box) != 4:
        raise BrahmaError("a crop box is [left, top, right, bottom]")
    left, top, right, bottom = (float(value) for value in box)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise BrahmaError(f"crop box {list(box)} is not inside 0..1")
    return left, top, right, bottom


def _save(image: Image.Image, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="PNG")
    return dest
