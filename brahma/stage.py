"""Place first, figures after, filter at the end.

Every panel, on a page or in a sequence, is built in three steps:

1. Plate. The place with nobody in it: the shot (camera, lens, horizon), the place, the
   time of day, and the light. No characters and no effects. It is approved empty.
2. Blocking. Where each figure stands and how tall it is: a stick figure over the plate,
   feet on its spot, the horizon, and each height. The person approves it before any figure is drawn.
3. Figures. A copy of the plate becomes the base. Each figure is drawn in its own job,
   alone and full body on flat green, with its own pose. It is keyed, scaled into its box
   with its feet on the bottom edge, and set down back to front, so the nearer one covers
   the farther one. Outside the figure and its shadow the plate stays pixel for pixel.
4. Filter. Optional. Effects (sparks, glow, fog, rain...) are painted over the accepted
   panel into a separate file. A filtered picture is never a reference.

Two figures never share a pose: the blocking is refused when two poses are the same.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from brahma.cutout import CastShadow, cover_with_foreground, place_figure, save_cutout
from brahma.errors import BrahmaError
from brahma.figures import box_crop
from brahma.models import BOTTOM_OUT
from brahma.takes import record_take

# How tall a standing figure is, as a share of the frame height, for each shot size.
FIGURE_HEIGHT = {
    "extreme-close": 1.0,
    "close": 0.98,
    "medium": 0.95,
    "medium-wide": 0.85,
    "wide": 0.55,
    "establishing": 0.32,
}
BLOCKING_COLORS = [(255, 64, 64), (64, 200, 255), (255, 210, 0), (120, 255, 120), (255, 120, 255), (255, 160, 64)]
# How far a standing figure may be from the others' scale, at its spot, before it is flagged.
SCALE_TOLERANCE = 0.15
_STILL = re.compile(r"\b(still|freezes?|frozen|motionless|quiet|quiet[oa]s?|inm[oó]vil(es)?|detenid[oa]s?|parad[oa]s?)\b", re.I)
_STRIDE = re.compile(r"\b(walk\w*|step\w*|stride|camina\w*|pasos?|zancada)\b", re.I)
_NOT_STANDING = re.compile(
    r"\b(crouch\w*|kneel\w*|knelt|squat\w*|sits?|sitting|seated|lying|lies|prone|jump\w*|leap\w*|"
    r"agach\w*|arrodill\w*|rodillas|cuclillas|sentad[oa]s?|sienta|tumbad[oa]s?|acostad[oa]s?|tendid[oa]s?|"
    r"salta\w*|brinca\w*)\b",
    re.I,
)


@dataclass(frozen=True)
class Placement:
    """One figure in one panel: who, doing what, where."""

    name: str
    character: str
    pose: str
    box: tuple[float, float, float, float]


def normalize_pose(text: str) -> str:
    return re.sub(r"[^\w]+", " ", str(text).lower()).strip()


def check_box(box) -> tuple[float, float, float, float]:
    """[left, top, right, bottom] as fractions. The bottom may pass the frame edge, up to
    BOTTOM_OUT: a figure walking toward the camera leaves the frame feet first."""

    values = [float(value) for value in box]
    if len(values) != 4 or not (0 <= values[0] < values[2] <= 1 and 0 <= values[1] < min(1.0, values[3]) and values[3] <= BOTTOM_OUT):
        raise BrahmaError(
            f"box {box} is not [left, top, right, bottom] as fractions inside 0..1 (the bottom may reach {BOTTOM_OUT})"
        )
    if values[2] - values[0] < 0.02 or values[3] - values[1] < 0.02:
        raise BrahmaError(f"box {box} is too small for a figure")
    return values[0], values[1], values[2], values[3]


def in_frame(box) -> tuple[float, float, float, float]:
    """The part of a box inside the picture."""

    left, top, right, bottom = (float(value) for value in box)
    return max(0.0, left), max(0.0, top), min(1.0, right), min(1.0, bottom)


def distinct_poses(placements: list[Placement]) -> None:
    seen: dict[str, str] = {}
    for placement in placements:
        key = normalize_pose(placement.pose)
        if key in seen:
            raise BrahmaError(
                f"{placement.name} and {seen[key]} have the same pose. Two figures in one panel never share a pose."
            )
        seen[key] = placement.name


def default_boxes(count: int, shot: str = "wide") -> list[tuple[float, float, float, float]]:
    """Figures side by side, feet near the bottom, sized for the shot."""

    if count <= 0:
        return []
    height = FIGURE_HEIGHT.get(shot, FIGURE_HEIGHT["wide"])
    bottom = 0.97 if height >= 0.9 else 0.92
    top = max(0.0, bottom - height)
    span = 0.9 / count
    width = min(span, 0.45)
    boxes = []
    for index in range(count):
        center = 0.05 + span * (index + 0.5)
        boxes.append((round(center - width / 2, 4), round(top, 4), round(center + width / 2, 4), bottom))
    return boxes


def back_to_front(placements: list[Placement]) -> list[Placement]:
    """Farther figures first. A box whose bottom is higher in the frame is farther away."""

    return sorted(placements, key=lambda item: (item.box[3], item.box[1]))


def standing(pose: str) -> bool:
    return not _NOT_STANDING.search(pose or "")


def camera_ratio(box, horizon: float) -> float | None:
    """The camera height in body heights, read from a standing figure whose feet are on flat ground.

    None when the feet are on or above the horizon: there the ground is not the ground the camera stands on."""

    top, bottom = float(box[1]), float(box[3])
    if bottom <= horizon or bottom <= top:
        return None
    return (bottom - horizon) / (bottom - top)


def ground_ratio(placements: list[Placement], horizon: float | None) -> float | None:
    """The camera ratio of standing figures already on this ground. None when that ground has not been accepted yet."""

    if horizon is None:
        return None
    ratios = [camera_ratio(item.box, horizon) for item in placements if standing(item.pose)]
    ratios = [ratio for ratio in ratios if ratio is not None]
    if not ratios:
        return None
    return statistics.median(ratios)


def scale_check(placements: list[Placement], horizon: float | None, ground: float | None = None) -> list[dict]:
    """Standing figures of one size, on one ground, share one camera ratio.

    Without an accepted ground, that ratio is the median of this panel, and a panel of one figure is not flagged.
    With one, every standing figure is measured against the ground, even when the new figures all agree with each other."""

    if horizon is None:
        return []
    ratios = {}
    for item in placements:
        ratio = camera_ratio(item.box, horizon) if standing(item.pose) else None
        if ratio is not None:
            ratios[item.name] = ratio
    if not ratios or (ground is None and len(ratios) < 2):
        return []
    expected = statistics.median(ratios.values()) if ground is None else ground
    return [
        {
            "figure": name,
            "ratio": round(ratio, 3),
            "expected": round(expected, 3),
            "size": "too big for its spot" if ratio < expected else "too small for its spot",
        }
        for name, ratio in ratios.items()
        if abs(ratio - expected) > SCALE_TOLERANCE * expected
    ]


def blocking_report(placements: list[Placement], horizon: float | None, ground: float | None = None) -> dict:
    """What the blocking preview shows, as data: order, boxes, heights as a share of the frame, horizon, scale_off.

    ground is the camera ratio of the accepted ground. None on the first panel and on a moving camera."""

    return {
        "order": [item.name for item in back_to_front(placements)],
        "boxes": {item.name: list(item.box) for item in placements},
        "heights": {item.name: round(item.box[3] - item.box[1], 3) for item in placements},
        "poses": {item.name: item.pose for item in placements},
        "horizon": horizon,
        "ground_ratio": None if ground is None else round(ground, 3),
        "scale_off": scale_check(placements, horizon, ground),
    }


def _label_font(height: int):
    try:
        return ImageFont.load_default(size=max(12, height // 45))
    except TypeError:
        return ImageFont.load_default()


def _dashed(draw: ImageDraw.ImageDraw, y: int, width: int, color, thickness: int) -> None:
    dash = max(8, width // 80)
    for x in range(0, width, dash * 2):
        draw.line([(x, y), (min(width - 1, x + dash), y)], fill=color, width=thickness)


def _stick(draw: ImageDraw.ImageDraw, box, color, thickness: int, stride: bool) -> tuple[float, float]:
    """One stick figure filling the box: the head at the top, the feet on the bottom edge. Returns the head center."""

    x0, y0, x1, y1 = box
    span_x, span_y = x1 - x0, y1 - y0
    center = (x0 + x1) / 2
    radius = max(thickness * 2, min(span_y * 0.09, span_x * 0.42))
    head = (center, y0 + radius)
    draw.ellipse([head[0] - radius, head[1] - radius, head[0] + radius, head[1] + radius], outline=color, width=thickness)
    shoulder = y0 + span_y * 0.32
    hip = y0 + span_y * 0.56
    draw.line([(center, head[1] + radius), (center, hip)], fill=color, width=thickness)
    reach = max(radius, span_x * 0.34)
    if stride:
        draw.line([(center, shoulder), (center + reach, shoulder + span_y * 0.08)], fill=color, width=thickness)
        draw.line([(center, shoulder), (center - reach * 0.55, shoulder + span_y * 0.16)], fill=color, width=thickness)
        draw.line([(center, hip), (x0 + span_x * 0.22, y1)], fill=color, width=thickness)
        draw.line([(center, hip), (x1 - span_x * 0.18, y1)], fill=color, width=thickness)
    else:
        draw.line([(center, shoulder), (center - reach, shoulder + span_y * 0.16)], fill=color, width=thickness)
        draw.line([(center, shoulder), (center + reach, shoulder + span_y * 0.16)], fill=color, width=thickness)
        draw.line([(center, hip), (center - span_x * 0.22, y1)], fill=color, width=thickness)
        draw.line([(center, hip), (center + span_x * 0.22, y1)], fill=color, width=thickness)
    return head


def blocking_preview(
    plate: Path, placements: list[Placement], dest: Path, horizon: float | None = None, ground: float | None = None
) -> Path:
    """The plate with a stick figure where each one stands, its height, and the horizon, for a person to approve."""

    with Image.open(plate) as image:
        canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    font = _label_font(height)
    thickness = max(2, width // 300)
    if horizon is not None:
        y = round(horizon * height)
        _dashed(draw, y, width, (255, 255, 255), thickness)
        label = "horizonte"
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        draw.rectangle([4, y - (bottom - top) - 10, 12 + right - left, y - 2], fill=(0, 0, 0))
        draw.text((8, y - (bottom - top) - 6 - top), label, fill=(255, 255, 255), font=font)
    flagged = {item["figure"] for item in scale_check(placements, horizon, ground)}
    for order, placement in enumerate(back_to_front(placements), start=1):
        color = BLOCKING_COLORS[(order - 1) % len(BLOCKING_COLORS)]
        x0, y0, x1, y1 = placement.box
        box = [x0 * width, y0 * height, x1 * width, y1 * height]
        head = _stick(draw, box, color, thickness, bool(_STRIDE.search(placement.pose)))
        label = f"{order}. {placement.name}  alto {round((y1 - y0) * 100)}%" + (" !" if placement.name in flagged else "")
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        text_x = min(max(4, head[0] + (box[2] - box[0]) * 0.5), width - (right - left) - 8)
        text_y = max(2, head[1] - (bottom - top) - 8)
        draw.rectangle([text_x - 4, text_y - 2, text_x + right - left + 4, text_y + bottom - top + 2], fill=(0, 0, 0))
        draw.text((text_x, text_y - top), label, fill=color, font=font)
        if y1 > 1:
            note = "sale por abajo"
            left, top, right, bottom = draw.textbbox((0, 0), note, font=font)
            foot = box[0]
            y = height - (bottom - top) - 8
            draw.rectangle([foot, y - 2, foot + 8 + right - left, height - 1], fill=(0, 0, 0))
            draw.text((foot + 4, y - top), note, fill=color, font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, format="PNG")
    return dest


def plate_description(
    look: list[str],
    *,
    place: str,
    time: str = "",
    shot: str = "",
    camera: str = "",
    from_reference: str = "",
) -> str:
    lines = [
        "An empty place, before any figure is added: no people, no creatures, no animals, no figures.",
        f"Shot: {shot}" if shot else "",
        place,
        time,
        camera,
        from_reference,
        "No effects: no particles, no sparks, no glow, no fog, no rain, no light rays.",
        *look,
    ]
    return "\n".join(line.strip() for line in lines if line and line.strip())


def figure_description(
    look: list[str],
    *,
    who: str,
    pose: str,
    light: str,
    references: str,
    continuity: str = "",
) -> str:
    lines = [
        "One person alone, full body from the top of the head to the soles of the boots, "
        "on a flat pure green background (#00FF00) that fills the whole picture.",
        "Nothing else: no ground, no floor, no shadow on the background, no scenery, no props apart from what the person carries, "
        "no second person, no text. The person does not touch the edges of the picture.",
        f"The person: {who}",
        f"Pose: {pose}",
        f"Light on the person: {light.strip().rstrip('.')}. Keep the green flat and evenly lit; no green light on the person.",
        references,
    ]
    if continuity:
        lines.append(continuity)
    lines.extend(line for line in look if not line.startswith("Avoid:"))
    shared = next((line.removeprefix("Avoid:").strip() for line in look if line.startswith("Avoid:")), "")
    lines.append(
        "Avoid: green on the person, cropped feet, cropped head, a second figure, ground, horizon, scenery"
        + (f", {shared}" if shared else "")
    )
    return "\n".join(line.strip() for line in lines if line and line.strip())


def filter_description(effects: list[str], look: list[str]) -> str:
    if not effects:
        raise BrahmaError("a filter needs at least one effect")
    lines = [
        "The same picture as the reference: same camera, framing, figures, poses, place, colors, and light.",
        "Add only these effects over it: " + "; ".join(effects) + ".",
        "Do not move, add, or remove anything else. No text.",
        *[line for line in look if not line.startswith("Avoid:")],
    ]
    return "\n".join(line.strip() for line in lines if line and line.strip())


def still_beat(text: str) -> bool:
    return bool(_STILL.search(text or ""))


class Stage:
    """The base picture of one panel and which figures are already on it."""

    def __init__(
        self,
        root: Path,
        plate: Path,
        base: Path,
        final: Path,
        shadow: CastShadow | None = None,
        horizon: float | None = None,
    ):
        self.root = root
        self.plate = plate
        self.base = base
        self.final = final
        self.shadow = shadow
        self.horizon = horizon
        self.state_path = base.with_suffix(".stage.json")
        self.before = base.with_name(base.stem + "-before.png")

    def state(self) -> dict:
        if self.state_path.is_file():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"placed": [], "pending": ""}

    def _save(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def placed(self) -> list[str]:
        return list(self.state()["placed"])

    def pending(self) -> str:
        return self.state().get("pending", "")

    def reset(self) -> None:
        for path in (self.base, self.state_path, self.before, *self.base.parent.glob(f"{self.base.stem}-*.png")):
            if path.is_file():
                path.unlink()

    def open(self) -> Path:
        """Start from the plate. A base already in progress is kept."""

        if not self.plate.is_file():
            raise BrahmaError(f"no plate at {self.plate}. The empty place comes first.")
        if not self.base.is_file():
            self.base.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(self.plate) as image:
                image.convert("RGB").save(self.base, format="PNG")
            self._save({"placed": [], "pending": ""})
        return self.base

    def next(self, placements: list[Placement]) -> Placement | None:
        done = set(self.placed())
        if self.pending():
            raise BrahmaError(f"figure {self.pending()} is placed but not accepted. Review it, then accept or reject it.")
        for placement in back_to_front(placements):
            if placement.name not in done:
                return placement
        return None

    def request(self, placement: Placement) -> None:
        """The next job is this figure, alone on green."""

        self.open()
        state = self.state()
        state["requested"] = placement.name
        self._save(state)

    def aspect(self) -> str:
        return "3:4"

    def cutout_path(self, name: str) -> Path:
        return self.base.with_name(f"{self.base.stem}-{name}-cutout.png")

    def put(
        self, placement: Placement, generated: Path, *, key: str, verdict: str, note: str = "", match: Path | None = None
    ) -> dict:
        """Key one figure off green and set it into its box. pending keeps the old base until accept or reject.

        match is the same figure in the previous panel; the cutout takes its color."""

        if verdict not in {"pending", "accepted", "rejected"}:
            raise BrahmaError("verdict is pending, accepted, or rejected")
        state = self.state()
        if verdict == "rejected":
            take = record_take(self.root, key, generated, dest=None, status="rejected", note=note)
            if state.get("pending") == placement.name and self.before.is_file():
                shutil.copyfile(self.before, self.base)
                self.before.unlink()
                state["pending"] = ""
                self._save(state)
            return {"take": str(take), "placed": False}
        if state.get("pending"):
            raise BrahmaError(f"figure {state['pending']} is still pending. Accept or reject it first.")
        if placement.name in state["placed"]:
            raise BrahmaError(f"figure {placement.name} is already on the base. Start the panel again to redraw it.")
        self.open()
        shutil.copyfile(self.base, self.before)
        with Image.open(self.base) as image, Image.open(generated) as drawn:
            cast = None if self.horizon is not None and placement.box[3] < self.horizon else self.shadow
            if match is not None and match.is_file():
                with Image.open(match) as previous:
                    composed, figure = place_figure(
                        image, drawn, placement.box, match=previous.convert("RGB"), shadow=cast
                    )
            else:
                composed, figure = place_figure(image, drawn, placement.box, shadow=cast)
            if self.horizon is not None and placement.box[3] < self.horizon and self.plate.is_file():
                with Image.open(self.plate) as plate:
                    composed = cover_with_foreground(plate, composed)
        composed.save(self.base, format="PNG")
        cutout = save_cutout(figure, self.cutout_path(placement.name))
        take = record_take(self.root, key, generated, dest=None, status="raw", note=note or placement.pose)
        if verdict == "accepted":
            state["placed"].append(placement.name)
            self.before.unlink()
        else:
            state["pending"] = placement.name
        self._save(state)
        return {"take": str(take), "placed": True, "accepted": verdict == "accepted", "cutout": str(cutout)}

    def accept(self, name: str) -> None:
        state = self.state()
        if state.get("pending") != name:
            raise BrahmaError(f"figure {name} is not pending")
        state["placed"].append(name)
        state["pending"] = ""
        if self.before.is_file():
            self.before.unlink()
        self._save(state)

    def complete(self, placements: list[Placement]) -> bool:
        return not self.pending() and {item.name for item in placements} <= set(self.placed())


def crop_figure(picture: Path, box, dest: Path) -> Path:
    return box_crop(picture, dest, list(in_frame(box)))
