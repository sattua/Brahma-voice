"""The story contract the script model must fill.

Visual fields are English because the image model follows English.
Names and dialogue stay in the language of the story.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_PAGES = 8
MAX_PANELS = 6
MAX_DIALOGUE = 3
MAX_CHARACTERS = 4
MAX_LOCATIONS = 4

SHOTS = ("extreme-close", "close", "medium", "medium-wide", "wide", "establishing")
ANGLES = ("eye-level", "low", "high", "over-shoulder", "dutch")
# Turnaround columns, left to right. A panel picks the column that matches how the figure faces the camera.
VIEWS = ("front", "three-quarter", "side", "back")
SIDES = ("wearer_left", "wearer_right", "center")
# How far below the frame the bottom of a figure's box may go.
BOTTOM_OUT = 1.5

_SIDE_ALIASES = {
    "left": "wearer_left",
    "izquierda": "wearer_left",
    "wearer-left": "wearer_left",
    "right": "wearer_right",
    "derecha": "wearer_right",
    "wearer-right": "wearer_right",
    "centre": "center",
    "middle": "center",
    "centro": "center",
}
_VIEW_ALIASES = {
    "frontal": "front",
    "frente": "front",
    "3/4": "three-quarter",
    "three-quarters": "three-quarter",
    "tres-cuartos": "three-quarter",
    "profile": "side",
    "perfil": "side",
    "lado": "side",
    "rear": "back",
    "behind": "back",
    "espalda": "back",
}


def normalize_view(value: object) -> str:
    key = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    key = _VIEW_ALIASES.get(key, key)
    if key not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}")
    return key

_SHOT_ALIASES = {
    "close-up": "close",
    "closeup": "close",
    "extreme-close-up": "extreme-close",
    "extreme close": "extreme-close",
    "full": "wide",
    "full-shot": "wide",
    "establishing-shot": "establishing",
    "medium-shot": "medium",
    "wide-shot": "wide",
    "medium-close": "medium",
    "cowboy": "medium-wide",
}
_ANGLE_ALIASES = {
    "eye": "eye-level",
    "eyelevel": "eye-level",
    "straight": "eye-level",
    "worms-eye": "low",
    "worm": "low",
    "birds-eye": "high",
    "bird": "high",
    "overhead": "high",
    "ots": "over-shoulder",
    "over-the-shoulder": "over-shoulder",
    "dutch-angle": "dutch",
    "canted": "dutch",
}


def slug_id(value: object) -> str:
    text = str(value).strip().lower().replace("-", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if not text or not text[0].isalpha():
        text = f"id_{text}".strip("_")
    return text[:32]


class StyleSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    medium: str = Field(min_length=3, max_length=120)
    line: str = Field(min_length=3, max_length=160)
    rendering: str = Field(min_length=3, max_length=160)
    palette: str = Field(min_length=3, max_length=160)
    lighting: str = Field(min_length=3, max_length=160)
    negative: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=400)


class Mark(BaseModel):
    """Something that sits on one side of the body and must stay on that side in every picture."""

    model_config = ConfigDict(extra="forbid")

    part: str = Field(min_length=1, max_length=80)
    side: str = "center"
    text: str = Field(min_length=3, max_length=200)

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: object) -> str:
        key = str(value or "center").strip().lower().replace(" ", "_")
        key = _SIDE_ALIASES.get(key, key)
        if key not in SIDES:
            raise ValueError(f"side must be one of {', '.join(SIDES)}")
        return key


class CharacterSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    appearance: str = Field(min_length=8, max_length=300)
    wardrobe: str = Field(min_length=3, max_length=200)
    distinguishing: str = Field(min_length=3, max_length=200)
    body: str = Field(default="", max_length=160)
    marks: list[Mark] = Field(default_factory=list, max_length=6)
    poses: list[Annotated[str, Field(min_length=3, max_length=120)]] = Field(default_factory=list, max_length=6)
    visual: bool = True

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return slug_id(value)


class LocationSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = Field(min_length=1, max_length=80)
    appearance: str = Field(min_length=8, max_length=300)
    lighting: str = Field(min_length=3, max_length=160)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return slug_id(value)


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    speaker: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=240)


class PanelFigure(BaseModel):
    """Where one character stands in a page panel, as fractions of the frame, and what pose it holds."""

    model_config = ConfigDict(extra="forbid")

    character: str
    box: list[float] = Field(default_factory=list)
    pose: str = Field(default="", max_length=240)

    @field_validator("character", mode="before")
    @classmethod
    def _character(cls, value: object) -> str:
        return slug_id(value)

    @field_validator("box")
    @classmethod
    def _box(cls, value: list[float]) -> list[float]:
        if value and (
            len(value) != 4
            or not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < min(1.0, value[3]) and value[3] <= BOTTOM_OUT)
        ):
            raise ValueError(f"box is [left, top, right, bottom] as fractions inside 0..1 (the bottom may reach {BOTTOM_OUT})")
        return value


class PanelSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    shot: str
    angle: str
    location_id: str
    character_ids: list[str] = Field(default_factory=list, max_length=3)
    action: str = Field(min_length=3, max_length=400)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=MAX_DIALOGUE)
    continuity: str = Field(default="", max_length=240)
    facing: str = ""
    figures: list[PanelFigure] = Field(default_factory=list, max_length=3)
    horizon: float | None = Field(default=None, ge=0, le=1)
    blocked: bool = False

    @field_validator("facing", mode="before")
    @classmethod
    def _facing(cls, value: object) -> str:
        if value is None or not str(value).strip():
            return ""
        return normalize_view(value)

    def view(self) -> str:
        if self.facing:
            return self.facing
        return "back" if self.angle == "over-shoulder" else "front"

    @field_validator("shot", mode="before")
    @classmethod
    def _shot(cls, value: object) -> str:
        key = str(value).strip().lower().replace("_", "-")
        key = _SHOT_ALIASES.get(key, key)
        if key not in SHOTS:
            raise ValueError(f"shot must be one of {', '.join(SHOTS)}")
        return key

    @field_validator("angle", mode="before")
    @classmethod
    def _angle(cls, value: object) -> str:
        key = str(value).strip().lower().replace("_", "-")
        key = _ANGLE_ALIASES.get(key, key)
        if key not in ANGLES:
            raise ValueError(f"angle must be one of {', '.join(ANGLES)}")
        return key

    @field_validator("location_id", mode="before")
    @classmethod
    def _location(cls, value: object) -> str:
        return slug_id(value)

    @field_validator("character_ids", mode="before")
    @classmethod
    def _cast(cls, value: object) -> list[str]:
        if not value:
            return []
        return [slug_id(item) for item in value]


class PageSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int = Field(ge=1, le=MAX_PAGES)
    panels: list[PanelSpec] = Field(min_length=1, max_length=MAX_PANELS)


class StoryPlan(BaseModel):
    """Validated script. Image prompts are assembled from this, never invented later."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(min_length=1, max_length=300)
    language: str = Field(min_length=2, max_length=12)
    style: StyleSpec
    characters: list[CharacterSpec] = Field(default_factory=list, max_length=MAX_CHARACTERS)
    locations: list[LocationSpec] = Field(min_length=1, max_length=MAX_LOCATIONS)
    pages: list[PageSpec] = Field(min_length=1, max_length=MAX_PAGES)

    @model_validator(mode="after")
    def _references_resolve(self) -> StoryPlan:
        character_ids = [character.id for character in self.characters]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("character ids must be unique")
        location_ids = [location.id for location in self.locations]
        if len(location_ids) != len(set(location_ids)):
            raise ValueError("location ids must be unique")
        known_characters = set(character_ids)
        known_locations = set(location_ids)
        by_name = {character.name.strip().lower(): character.id for character in self.characters}
        numbers = [page.number for page in self.pages]
        if numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("page numbers must be 1..N with no gaps")
        for page in self.pages:
            for panel in page.panels:
                if panel.location_id not in known_locations:
                    raise ValueError(f"unknown location_id {panel.location_id}")
                for character_id in panel.character_ids:
                    if character_id not in known_characters:
                        raise ValueError(f"unknown character_id {character_id}")
                for line in panel.dialogue:
                    speaker = line.speaker.strip().lower()
                    if speaker in {"narrator", "caption", "narración"}:
                        line.speaker = "narrator"
                    elif speaker in by_name:
                        line.speaker = by_name[speaker]
                    elif speaker in known_characters:
                        line.speaker = speaker
                    else:
                        raise ValueError(f"dialogue speaker {line.speaker} is not a character or narrator")
        return self

    def character(self, character_id: str) -> CharacterSpec:
        for character in self.characters:
            if character.id == character_id:
                return character
        raise KeyError(character_id)

    def location(self, location_id: str) -> LocationSpec:
        for location in self.locations:
            if location.id == location_id:
                return location
        raise KeyError(location_id)

    def panel(self, panel_id: str) -> tuple[PageSpec, PanelSpec]:
        for page in self.pages:
            for index, panel in enumerate(page.panels, start=1):
                if panel_key(page.number, index) == panel_id:
                    return page, panel
        raise KeyError(panel_id)


def panel_key(page_number: int, index: int) -> str:
    return f"p{page_number:02d}_{index:02d}"


class Project(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    story: str
    plan: StoryPlan
    workflow: str
    revisions: dict[str, int] = Field(default_factory=dict)

    def revision(self, key: str) -> int:
        return self.revisions.get(key, 1)

    def bump(self, key: str) -> int:
        value = self.revisions.get(key, 1) + 1
        self.revisions[key] = value
        return value


BackendName = Literal["mock", "comfyui"]
ScriptBackendName = Literal["mock", "openai"]
