"""A sequence is one continuous shot. The look stays in settings.json; the action lives here.

Every panel is staged: the empty place first (the plate), then the blocking the person
approves (where each figure stands and how tall it is), then each figure in its own box,
back to front, then an optional filter in a separate file. With a static camera the plate
is shared by every panel, so the place does not move between panels. Panel n still waits
for panel n-1 to be accepted, and the crop of each figure in panel n-1 is the reference
for the same figure in panel n. A panel that was replaced takes back the acceptance of
every panel after it, because those were built from a picture that no longer exists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brahma.consistency import negative_prompt, scene_style
from brahma.cutout import GREEN, CastShadow
from brahma.effects import effects_in, without_effects
from brahma.errors import BrahmaError
from brahma.figures import box_crop
from brahma.models import normalize_view
from brahma.settings import SETTINGS_NAME, load_settings
from brahma.stage import (
    Placement,
    Stage,
    check_box,
    default_boxes,
    distinct_poses,
    figure_description,
    filter_description,
    in_frame,
    normalize_pose,
    plate_description,
    still_beat,
)

SEQUENCE_NAME = "sequence.json"
MAX_REFERENCES = 4
CAMERAS = ("static", "moving")


class FigureSpec(BaseModel):
    """One figure that stays in the shot. name is unique in the sequence."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    character: str = Field(default="", max_length=40)
    pose: str = Field(min_length=3, max_length=240)


class BlockSpec(BaseModel):
    """Where one figure stands in one panel and, if they change, its pose and the side it shows there."""

    model_config = ConfigDict(extra="forbid")

    figure: str = Field(min_length=1, max_length=40)
    box: list[float]
    pose: str = Field(default="", max_length=240)
    view: str = ""

    @field_validator("box")
    @classmethod
    def _box(cls, value: list[float]) -> list[float]:
        return list(check_box(value))

    @field_validator("view", mode="before")
    @classmethod
    def _view(cls, value: object) -> str:
        return normalize_view(value) if value else ""


class ShadowSpec(BaseModel):
    """Where the figures' shadows fall in this shot. See CastShadow."""

    model_config = ConfigDict(extra="forbid")

    back: float = Field(default=0.3, ge=-1, le=1)
    lean: float = Field(default=0.0, ge=-1, le=1)
    strength: float = Field(default=0.55, ge=0, le=1)

    def cast(self) -> CastShadow:
        return CastShadow(back=self.back, lean=self.lean, strength=self.strength)


class SequenceSettings(BaseModel):
    """The action lock for one run of panels. Every panel in the folder has to follow it."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=3, max_length=400)
    place: str = Field(default="", max_length=200)
    time: str = Field(default="", max_length=200)
    continuity: str = Field(default="", max_length=400)
    characters: list[str] = Field(default_factory=list)
    anchors: str = Field(default="", max_length=400)
    camera: str = "static"
    shot: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=40)
    figures: list[FigureSpec] = Field(default_factory=list, max_length=6)
    blocking: dict[str, list[BlockSpec]] = Field(default_factory=dict)
    beats: list[Annotated[str, Field(min_length=3, max_length=400)]] = Field(default_factory=list, max_length=48)
    accepted: list[int] = Field(default_factory=list)
    plates: list[int] = Field(default_factory=list)
    blocked: list[int] = Field(default_factory=list)
    horizon: float | None = Field(default=None, ge=0, le=1)
    view: str = "front"
    aspect_ratio: str = "16:9"
    effects: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(default_factory=list, max_length=8)
    shadow: ShadowSpec | None = None
    brush_source: str = ""
    brush_box: list[float] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accepted_were_blocked(cls, data: object) -> object:
        """An older sequence.json has no blocked list. A panel accepted then was seen and approved whole."""

        if not isinstance(data, dict) or "blocked" in data:
            return data
        return {**data, "blocked": list(data.get("accepted") or [])}

    @model_validator(mode="before")
    @classmethod
    def _poses_become_figures(cls, data: object) -> object:
        """An older sequence.json listed poses as sentences. Each becomes a figure."""

        if not isinstance(data, dict) or "poses" not in data:
            return data
        data = dict(data)
        poses = data.pop("poses") or []
        if poses and not data.get("figures"):
            cast = list(data.get("characters") or [])
            figures = []
            for number, pose in enumerate(poses, start=1):
                if len(cast) == 1:
                    who = cast[0]
                else:
                    who = cast[number - 1] if number <= len(cast) else ""
                figures.append({"name": f"figure-{number}", "character": who, "pose": pose})
            data["figures"] = figures
        return data

    @field_validator("view", mode="before")
    @classmethod
    def _view(cls, value: object) -> str:
        return normalize_view(value or "front")

    @field_validator("camera")
    @classmethod
    def _camera(cls, value: str) -> str:
        if value not in CAMERAS:
            raise ValueError("camera is static or moving")
        return value

    @field_validator("aspect_ratio")
    @classmethod
    def _aspect(cls, value: str) -> str:
        if value not in {"1:1", "4:3", "3:4", "16:9", "9:16"}:
            raise ValueError("aspect_ratio is one of 1:1, 4:3, 3:4, 16:9, 9:16")
        return value

    @field_validator("brush_box")
    @classmethod
    def _box(cls, value: list[float]) -> list[float]:
        if value and (len(value) != 4 or not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1)):
            raise ValueError("brush_box is [left, top, right, bottom] as fractions inside 0..1")
        return value

    @field_validator("accepted", "plates", "blocked")
    @classmethod
    def _numbers(cls, value: list[int]) -> list[int]:
        return sorted({number for number in value if number >= 1})

    @model_validator(mode="after")
    def _figures_hold(self) -> SequenceSettings:
        names = [figure.name for figure in self.figures]
        if len(names) != len(set(names)):
            raise ValueError("figure names must be unique")
        for key, blocks in self.blocking.items():
            if not str(key).isdigit() or int(key) < 1:
                raise ValueError(f"blocking key {key} is not a panel number")
            for block in blocks:
                if block.figure not in names:
                    raise ValueError(f"blocking for panel {key} names unknown figure {block.figure}")
        panels = {1, *(int(key) for key in self.blocking)}
        for index in panels:
            try:
                distinct_poses(self.placements(index))
            except BrahmaError as exc:
                raise ValueError(f"panel {index}: {exc}") from None
        return self

    def beat(self, index: int) -> str:
        """The action for panel index. Past the last beat the shot keeps going with the main action."""

        if 1 <= index <= len(self.beats):
            return self.beats[index - 1]
        if index == 1:
            return self.action
        return f"The same shot a few seconds later. {self.continuity or self.action}".strip()

    def plate_index(self, index: int) -> int:
        return 1 if self.camera == "static" else index

    def pose_of(self, index: int, name: str) -> str:
        for block in self.blocking.get(str(index), []):
            if block.figure == name and block.pose.strip():
                return block.pose
        for figure in self.figures:
            if figure.name == name:
                return figure.pose
        raise BrahmaError(f"no figure named {name}")

    def view_of(self, index: int, name: str) -> str:
        """The side of the figure the camera sees in panel index: its own if the blocking sets one, else the shot's."""

        for block in self.blocking.get(str(index), []):
            if block.figure == name and block.view:
                return block.view
        return self.view

    def box_of(self, index: int, name: str) -> tuple[float, float, float, float] | None:
        """The box from the latest panel up to index that set one: the shot goes on, the figure stays put."""

        for number in range(index, 0, -1):
            for block in self.blocking.get(str(number), []):
                if block.figure == name:
                    return tuple(block.box)
        return None

    def proposed(self, index: int) -> list[str]:
        """Figures with no box set up to panel index: the preview shows them in a box the tools proposed."""

        return [figure.name for figure in self.figures if self.box_of(index, figure.name) is None]

    def placements(self, index: int) -> list[Placement]:
        """Every figure in panel index, with its box and its pose there."""

        fallback = default_boxes(len(self.figures), "wide")
        out = []
        for number, figure in enumerate(self.figures):
            box = self.box_of(index, figure.name) or fallback[number]
            out.append(Placement(figure.name, figure.character, self.pose_of(index, figure.name), box))
        return out


def save_sequence(folder: Path, settings: SequenceSettings) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SEQUENCE_NAME
    path.write_text(json.dumps(settings.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_sequence(folder: Path) -> SequenceSettings:
    path = folder / SEQUENCE_NAME
    if not path.is_file():
        raise BrahmaError(f"no sequence settings at {path}")
    try:
        return SequenceSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BrahmaError(f"sequence settings are not usable: {exc}") from exc


def panel_path(folder: Path, index: int) -> Path:
    if index < 1:
        raise BrahmaError(f"sequence panel {index} is not a frame")
    return folder / f"panel-{index:02d}.png"


def plate_path(folder: Path, sequence: SequenceSettings, index: int) -> Path:
    if sequence.camera == "static":
        return folder / "plate.png"
    return folder / f"plate-{sequence.plate_index(index):02d}.png"


def base_path(folder: Path, index: int) -> Path:
    return folder / f"panel-{index:02d}-base.png"


def blocking_path(folder: Path, index: int) -> Path:
    return folder / f"panel-{index:02d}-blocking.png"


def fx_path(folder: Path, index: int) -> Path:
    return folder / f"panel-{index:02d}-fx.png"


def stage_of(folder: Path, sequence: SequenceSettings, index: int) -> Stage:
    return Stage(
        project_root(folder),
        plate_path(folder, sequence, index),
        base_path(folder, index),
        panel_path(folder, index),
        shadow=sequence.shadow.cast() if sequence.shadow else None,
    )


@dataclass(frozen=True)
class SequenceFrame:
    """One job. The description is fixed. The file is dest, inside the sequence folder."""

    description: str
    reference: Path | None
    dest: Path
    references: tuple[Path, ...] = field(default_factory=tuple)
    dropped: tuple[Path, ...] = field(default_factory=tuple)
    step: str = "panel"
    figure: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    effects: tuple[str, ...] = field(default_factory=tuple)
    aspect_ratio: str = ""


def look_lines(root: Path) -> list[str]:
    """The look from settings.json, for a scene.

    Notes stay out: they describe the reference drawing, not the shot. Shot sizes and sheet
    light stay out: the shot owns the camera. Effects stay out: they are a filter.
    """

    look = scene_style(load_settings(root).as_style())
    lines = [without_effects(text) for text in (look.medium, look.palette, look.line, look.rendering, look.lighting)]
    lines.append(f"Avoid: {negative_prompt(look)}")
    return lines


def _previous_accepted(sequence: SequenceSettings, folder: Path, index: int) -> None:
    if index > 1:
        previous = panel_path(folder, index - 1)
        if not previous.is_file():
            raise BrahmaError(f"no panel at {previous}")
        if index - 1 not in sequence.accepted:
            raise BrahmaError(f"panel {index - 1} is not accepted. Accept it before building panel {index}.")


def _frame(description: str, references: list[Path | None], dest: Path, **extra) -> SequenceFrame:
    kept = [path for path in references if path is not None]
    return SequenceFrame(
        description=description,
        reference=kept[0] if kept else None,
        dest=dest,
        references=tuple(kept[:MAX_REFERENCES]),
        dropped=tuple(kept[MAX_REFERENCES:]),
        **extra,
    )


def plate_frame(
    folder: Path,
    index: int,
    *,
    place_plate: Path | None = None,
    place_text: str = "",
) -> SequenceFrame:
    """The empty place for panel index. With a static camera it is drawn once, for panel 1.

    The style sample is not a reference here: it is often a portrait, and a plate copies its
    subject, framing, and particles. The look travels as text.
    """

    sequence = load_sequence(folder)
    root = project_root(folder)
    number = sequence.plate_index(index)
    dest = plate_path(folder, sequence, index)
    if sequence.camera == "static" and index > 1:
        raise BrahmaError("the camera is static: every panel shares the plate of panel 1")
    references: list[Path] = []
    camera = ""
    from_reference = ""
    if sequence.camera == "moving" and number > 1:
        previous = plate_path(folder, sequence, number - 1)
        if number - 1 not in sequence.plates or not previous.is_file():
            raise BrahmaError(f"the plate of panel {number - 1} is not accepted")
        references.append(previous)
        camera = f"Camera: {without_effects(sequence.beat(index))}"
        from_reference = "The reference is the same place a moment before: keep it, only the camera moves as written."
    elif place_plate is not None and place_plate.is_file():
        references.append(place_plate)
        from_reference = "The reference is this place: keep its forms and materials; the camera follows the shot."
    description = plate_description(
        look_lines(root),
        place=without_effects(" ".join(part for part in (sequence.place, place_text) if part)),
        time=without_effects(sequence.time),
        shot=without_effects(sequence.shot),
        camera=camera,
        from_reference=from_reference,
    )
    moved = effects_in(sequence.place, place_text, sequence.time, sequence.shot)
    return _frame(description, references, dest, step="plate", effects=tuple(moved))


def figure_frame(
    folder: Path,
    index: int,
    name: str | None = None,
    *,
    who: str,
    view: Path | None = None,
    pose_sheet: Path | None = None,
) -> SequenceFrame:
    """One figure into its box on the base of panel index, back to front."""

    sequence = load_sequence(folder)
    root = project_root(folder)
    _previous_accepted(sequence, folder, index)
    if sequence.plate_index(index) not in sequence.plates:
        raise BrahmaError("the plate is not accepted. The empty place comes first.")
    placements = sequence.placements(index)
    if not placements:
        raise BrahmaError("this sequence has no figures; its panel is the plate")
    stage = stage_of(folder, sequence, index)
    stage.open()
    placement = next((item for item in placements if item.name == name), None) if name else stage.next(placements)
    if placement is None:
        raise BrahmaError(f"no figure {name} in panel {index}" if name else f"every figure of panel {index} is placed")
    stage.request(placement)
    previous_crop = previous_figure(folder, sequence, index, placement.name) if index > 1 else None
    warnings = []
    if index > 1 and normalize_pose(sequence.pose_of(index, placement.name)) == normalize_pose(
        sequence.pose_of(index - 1, placement.name)
    ) and not still_beat(sequence.beat(index)):
        warnings.append(
            f"pose_repeated: {placement.name} has the same pose as in panel {index - 1}, but the action moves on"
        )
    description = figure_description(
        [sequence.anchors, *look_lines(root)],
        who=who,
        pose=placement.pose,
        light=without_effects(sequence.time) or "the light of the place",
        references=(
            "The references show this same character (turnaround view and pose sheet): take identity, outfit, "
            "gear, and marks from them, not their white background, their layout, or their number of figures."
        ),
        continuity=(
            "The last reference is this same figure in the previous panel: keep who it is, the outfit, and the gear. "
            "Do not copy its pose: not its legs, not its arms, not the angle of its rifle or tools. The pose is the one written above."
            if previous_crop is not None
            else ""
        ),
    )
    return _frame(
        description,
        [view, pose_sheet, previous_crop],
        stage.base,
        step="figure",
        figure=placement.name,
        warnings=tuple(warnings),
        effects=tuple(effects_in(sequence.beat(index), sequence.time)),
        aspect_ratio=stage.aspect(),
    )


def filter_frame(folder: Path, index: int, effects: list[str] | None = None) -> SequenceFrame:
    """Effects over an accepted panel, into panel-NN-fx.png. The panel itself does not change."""

    sequence = load_sequence(folder)
    root = project_root(folder)
    if index not in sequence.accepted:
        raise BrahmaError(f"panel {index} is not accepted. The filter goes over an accepted panel.")
    chosen = list(effects or sequence.effects)
    description = filter_description(chosen, look_lines(root))
    return _frame(description, [panel_path(folder, index)], fx_path(folder, index), step="filter", effects=tuple(chosen))


def previous_figure(folder: Path, sequence: SequenceSettings, index: int, name: str) -> Path | None:
    """The same figure in panel index-1: its cutout on green, or the crop of its box."""

    dest = folder / f"panel-{index:02d}-prev-{_file_name(name)}.png"
    cutout = stage_of(folder, sequence, index - 1).cutout_path(name)
    if cutout.is_file():
        with Image.open(cutout) as figure:
            keyed = figure.convert("RGBA")
        canvas = Image.new("RGB", (keyed.width + keyed.width // 4, keyed.height + keyed.height // 8), GREEN)
        canvas.paste(keyed, (keyed.width // 8, keyed.height // 16), keyed)
        canvas.save(dest, format="PNG")
        return dest
    before = {item.name: item for item in sequence.placements(index - 1)}.get(name)
    if before is None:
        return None
    return box_crop(panel_path(folder, index - 1), dest, list(in_frame(before.box)))


def _file_name(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "figure"


def unify_frame(
    folder: Path,
    index: int,
    *,
    views: list[Path] | None = None,
    poses: list[Path] | None = None,
) -> SequenceFrame:
    """Repaint panel index with the paint of accepted panel index-1, keeping its own camera and action."""

    if index < 2:
        raise BrahmaError("unify starts at panel 2; panel 1 is the paint every later panel inherits")
    sequence = load_sequence(folder)
    root = project_root(folder)
    previous = panel_path(folder, index - 1)
    current = panel_path(folder, index)
    if index - 1 not in sequence.accepted:
        raise BrahmaError(f"panel {index - 1} is not accepted, so it cannot carry the paint to panel {index}")
    if not current.is_file():
        raise BrahmaError(f"no panel at {current} to unify")
    lines = [
        *look_lines(root),
        sequence.beat(index),
        "Repaint the second reference. Keep its camera, framing, figures, and action exactly. "
        "Take the brush, palette, and finish from the first reference.",
    ]
    references = [previous, current, *(views or [])]
    brush = brush_reference(folder, sequence, root)
    if brush is not None:
        references.append(brush)
    references.extend(poses or [])
    return SequenceFrame(
        description="\n".join(line.strip() for line in lines if line and line.strip()),
        reference=previous,
        dest=current,
        references=tuple(references[:MAX_REFERENCES]),
        dropped=tuple(references[MAX_REFERENCES:]),
    )


def brush_reference(folder: Path, sequence: SequenceSettings, root: Path) -> Path | None:
    if not sequence.brush_box:
        return None
    source = root / (sequence.brush_source or "refs/style.png")
    if not source.is_file():
        return None
    return box_crop(source, folder / "brush.png", sequence.brush_box)


def accept_panel(folder: Path, index: int) -> SequenceSettings:
    sequence = load_sequence(folder)
    if not panel_path(folder, index).is_file():
        raise BrahmaError(f"no panel {index} to accept")
    if index > 1 and index - 1 not in sequence.accepted:
        raise BrahmaError(f"accept panel {index - 1} first; panel {index} was painted from it")
    sequence.accepted = sorted({*sequence.accepted, index})
    save_sequence(folder, sequence)
    return sequence


def release_after(folder: Path, index: int) -> list[int]:
    """Panel index changed. Later panels were painted from the old one and lose their acceptance."""

    sequence = load_sequence(folder)
    released = [number for number in sequence.accepted if number >= index]
    sequence.accepted = [number for number in sequence.accepted if number < index]
    save_sequence(folder, sequence)
    return released


def existing_panels(folder: Path) -> list[int]:
    numbers = []
    for path in folder.glob("panel-[0-9][0-9].png"):
        numbers.append(int(path.stem.split("-")[1]))
    return sorted(numbers)


def sequence_step(folder: Path, panel: int, step: str) -> Path:
    """One build file for a panel, in the same sequence folder. Not an assets path."""

    if panel < 1:
        raise BrahmaError(f"sequence panel {panel} is not a frame")
    token = step.strip()
    if not re.fullmatch(r"[a-z0-9]+", token):
        raise BrahmaError(f"sequence step {step!r} is not a file name")
    load_sequence(folder)
    return folder / f"panel-{panel:02d}-{token}.png"


def place_image(source: Path, dest: Path) -> Path:
    """Copy a generated file onto dest. dest is the picture. An assets path is not."""

    source = Path(source)
    dest = Path(dest)
    if under_cursor_assets(dest):
        raise BrahmaError(f"images stay in the story folder, not {dest}")
    if not source.is_file():
        raise BrahmaError(f"image not found: {source}")
    root = project_root(dest.parent).resolve()
    if root not in dest.resolve().parents:
        raise BrahmaError(f"not a story image path: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as incoming:
        incoming.load()
        frame = incoming.convert("RGBA")
    frame.save(dest, format="PNG")
    return dest


def under_cursor_assets(path: Path) -> bool:
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part != ".cursor":
            continue
        tail = parts[index + 1 :]
        if len(tail) >= 2 and tail[0] == "projects" and "assets" in tail[1:]:
            return True
    return False


def project_root(folder: Path) -> Path:
    for candidate in (folder, *folder.parents):
        if (candidate / SETTINGS_NAME).is_file():
            return candidate
    raise BrahmaError(f"no settings above {folder}")
