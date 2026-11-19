"""Change one region of an accepted picture and keep every other pixel.

The image tool redraws the whole frame even when asked for a hand. So the request
carries the picture with the region painted magenta, and the answer is pasted back
only inside that region. Outside it, the result is the old picture, pixel for pixel.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from brahma.errors import BrahmaError
from brahma.takes import record_take

MAGENTA = (255, 0, 255)
# The seam fades over this many pixels, inside the region only.
FEATHER = 6


def edit_folder(root: Path) -> Path:
    return root / "edit"


def _stem(root: Path, target: Path) -> str:
    relative = target.resolve().relative_to(root.resolve())
    return "-".join(relative.with_suffix("").parts)


def _boxes(regions, size: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Regions are pixel boxes [x0, y0, x1, y1], or fractions when every value is at most 1."""

    if not regions:
        raise BrahmaError("an edit needs at least one region [x0, y0, x1, y1]")
    width, height = size
    boxes = []
    for region in regions:
        if len(region) != 4:
            raise BrahmaError(f"region {region} is not [x0, y0, x1, y1]")
        values = [float(value) for value in region]
        if all(0 <= value <= 1 for value in values):
            values = [values[0] * width, values[1] * height, values[2] * width, values[3] * height]
        x0, y0, x1, y1 = (int(round(value)) for value in values)
        x0, x1 = max(0, min(x0, x1)), min(width, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(height, max(y0, y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise BrahmaError(f"region {region} is empty inside a {width}x{height} picture")
        boxes.append((x0, y0, x1, y1))
    return boxes


def region_mask(size: tuple[int, int], boxes: list[tuple[int, int, int, int]], feather: int = FEATHER) -> Image.Image:
    """White inside the boxes with a soft inner edge. Zero everywhere outside, always."""

    hard = Image.new("L", size, 0)
    draw = ImageDraw.Draw(hard)
    for box in boxes:
        draw.rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=255)
    if feather <= 0:
        return hard
    shrunk = hard.filter(ImageFilter.MinFilter(2 * feather + 1))
    soft = shrunk.filter(ImageFilter.GaussianBlur(feather / 2))
    return ImageChops.multiply(soft, hard)


def start_edit(root: Path, target: Path, regions, change: str) -> dict:
    """Write the magenta request and remember the region. Returns what the image job needs."""

    target = Path(target)
    if not target.is_file():
        raise BrahmaError(f"no picture at {target}")
    change = change.strip()
    if len(change) < 3:
        raise BrahmaError("say what changes inside the region")
    with Image.open(target) as image:
        base = image.convert("RGB")
    boxes = _boxes(regions, base.size)
    painted = base.copy()
    draw = ImageDraw.Draw(painted)
    for box in boxes:
        draw.rectangle([box[0], box[1], box[2] - 1, box[3] - 1], fill=MAGENTA)
    folder = edit_folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    stem = _stem(root, target)
    mask_path = folder / f"{stem}-mask.png"
    painted.save(mask_path, format="PNG")
    pending = {
        "target": str(target),
        "regions": [list(box) for box in boxes],
        "size": list(base.size),
        "change": change,
    }
    (folder / f"{stem}.json").write_text(json.dumps(pending, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"mask": mask_path, "target": target, "regions": pending["regions"], "size": base.size}


def finish_edit(root: Path, target: Path, generated: Path, note: str = "") -> dict:
    """Paste the generated picture back inside the region only, and keep both in takes."""

    target = Path(target)
    folder = edit_folder(root)
    stem = _stem(root, target)
    pending_path = folder / f"{stem}.json"
    if not pending_path.is_file():
        raise BrahmaError(f"no edit was started for {target}")
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    generated = Path(generated)
    if not generated.is_file():
        raise BrahmaError(f"image not found: {generated}")
    with Image.open(target) as image:
        original = image.convert("RGBA")
    with Image.open(generated) as image:
        fresh = image.convert("RGBA").resize(original.size, Image.Resampling.LANCZOS)
    boxes = [tuple(box) for box in pending["regions"]]
    mask = region_mask(original.size, boxes)
    result = Image.composite(fresh, original, mask)
    key = f"edit:{stem}"
    record_take(root, key, generated, dest=None, status="raw", note=f"model output for: {pending['change']}")
    record_take(root, key, target, dest=None, status="replaced", note="before the edit")
    staged = folder / f"{stem}-result.png"
    result.save(staged, format="PNG")
    take = record_take(root, key, staged, dest=target, status="accepted", note=note or pending["change"])
    pending_path.unlink()
    return {"target": str(target), "take": str(take), "regions": pending["regions"]}
