"""Look before accepting: named crops, a side-by-side strip, and how much each region moved.

Regions are boxes, in pixels or as fractions of the picture. Boxes given once are kept
next to the picture as <name>.regions.json and used again on the next review.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from brahma.errors import BrahmaError

SHEET_REGIONS = {
    "head": [0.0, 0.0, 1.0, 0.25],
    "hands": [0.0, 0.35, 1.0, 0.65],
    "feet": [0.0, 0.75, 1.0, 1.0],
}
SHOT_REGIONS = {
    "sky": [0.0, 0.0, 1.0, 0.34],
    "middle": [0.0, 0.34, 1.0, 0.7],
    "ground": [0.0, 0.7, 1.0, 1.0],
    "left": [0.0, 0.0, 0.5, 1.0],
    "right": [0.5, 0.0, 1.0, 1.0],
}
STRIP_HEIGHT = 768


def regions_path(target: Path) -> Path:
    return target.with_suffix(".regions.json")


def load_regions(target: Path) -> dict[str, list[float]]:
    path = regions_path(target)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    with Image.open(target) as image:
        width, height = image.size
    return dict(SHOT_REGIONS if width > height * 1.2 else SHEET_REGIONS)


def save_regions(target: Path, regions: dict[str, list[float]]) -> Path:
    for name, box in regions.items():
        if len(box) != 4:
            raise BrahmaError(f"region {name} is not [x0, y0, x1, y1]")
    path = regions_path(target)
    path.write_text(json.dumps(regions, indent=2) + "\n", encoding="utf-8")
    return path


def _pixels(box, size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    values = [float(value) for value in box]
    if all(0 <= value <= 1 for value in values):
        values = [values[0] * width, values[1] * height, values[2] * width, values[3] * height]
    x0, y0, x1, y1 = (int(round(value)) for value in values)
    x0, x1 = max(0, min(x0, x1)), min(width, max(x0, x1, x0 + 1))
    y0, y1 = max(0, min(y0, y1)), min(height, max(y0, y1, y0 + 1))
    return x0, y0, x1, y1


def review_picture(
    root: Path,
    target: Path,
    *,
    regions: dict[str, list[float]] | None = None,
    compare: Path | None = None,
    label: str | None = None,
) -> dict:
    """Write crops of each region, a strip beside the compared picture, and a change score per region."""

    target = Path(target)
    if not target.is_file():
        raise BrahmaError(f"no picture at {target}")
    if regions:
        save_regions(target, regions)
    boxes = regions or load_regions(target)
    name = label or "-".join(target.resolve().relative_to(root.resolve()).with_suffix("").parts)
    folder = root / "review" / name
    folder.mkdir(parents=True, exist_ok=True)
    with Image.open(target) as image:
        current = image.convert("RGB")
    before = None
    if compare is not None:
        compare = Path(compare)
        if not compare.is_file():
            raise BrahmaError(f"no picture at {compare} to compare")
        with Image.open(compare) as image:
            before = image.convert("RGB").resize(current.size, Image.Resampling.LANCZOS)
    crops: dict[str, str] = {}
    changed: dict[str, float] = {}
    for region, box in boxes.items():
        pixel_box = _pixels(box, current.size)
        crop = current.crop(pixel_box)
        if before is not None:
            old = before.crop(pixel_box)
            pair = Image.new("RGB", (crop.width * 2 + 8, crop.height), (255, 255, 255))
            pair.paste(old, (0, 0))
            pair.paste(crop, (crop.width + 8, 0))
            crop = pair
            diff = ImageStat.Stat(ImageChops.difference(old, current.crop(pixel_box))).mean
            changed[region] = round(sum(diff) / len(diff) / 255 * 100, 1)
        path = folder / f"{region}.png"
        crop.save(path, format="PNG")
        crops[region] = str(path)
    report = {"target": str(target), "crops": crops, "regions": boxes}
    if before is not None:
        strip = _strip([before, current])
        strip_path = folder / "compare.png"
        strip.save(strip_path, format="PNG")
        report["compare"] = str(compare)
        report["strip"] = str(strip_path)
        report["changed_percent"] = changed
    return report


SAME_POSE = 0.94
_FIGURE_SIZE = (24, 48)


def _figure_vector(image: Image.Image, box, mirror: bool = False) -> list[float]:
    inside = [min(1.0, max(0.0, float(value))) for value in box]
    crop = image.crop(_pixels(inside, image.size)).convert("L").resize(_FIGURE_SIZE, Image.Resampling.LANCZOS)
    crop = ImageOps.autocontrast(crop.filter(ImageFilter.GaussianBlur(1)))
    if mirror:
        crop = ImageOps.mirror(crop)
    values = [float(value) for value in crop.tobytes()]
    mean = sum(values) / len(values)
    return [value - mean for value in values]


def _correlation(first: list[float], second: list[float]) -> float:
    top = sum(a * b for a, b in zip(first, second))
    norm = (sum(a * a for a in first) * sum(b * b for b in second)) ** 0.5
    return top / norm if norm else 0.0


def duplicate_figures(target: Path, boxes: dict[str, list[float] | tuple[float, ...]], threshold: float = SAME_POSE) -> list[dict]:
    """Pairs of figures whose crops look alike, mirrored or not. A warning to look, not a verdict."""

    with Image.open(target) as image:
        picture = image.convert("RGB")
    names = list(boxes)
    flagged = []
    for first in range(len(names)):
        for second in range(first + 1, len(names)):
            a = _figure_vector(picture, boxes[names[first]])
            b = _figure_vector(picture, boxes[names[second]])
            b_mirror = _figure_vector(picture, boxes[names[second]], mirror=True)
            score = max(_correlation(a, b), _correlation(a, b_mirror))
            if score >= threshold:
                flagged.append({"figures": [names[first], names[second]], "score": round(score, 3)})
    return flagged


def _strip(images: list[Image.Image]) -> Image.Image:
    scaled = []
    for image in images:
        width = max(1, round(image.width * STRIP_HEIGHT / image.height))
        scaled.append(image.resize((width, STRIP_HEIGHT), Image.Resampling.LANCZOS))
    gap = 16
    out = Image.new("RGB", (sum(item.width for item in scaled) + gap * (len(scaled) - 1), STRIP_HEIGHT), (255, 255, 255))
    x = 0
    for item in scaled:
        out.paste(item, (x, 0))
        x += item.width + gap
    return out
