"""Per-project look. Every sheet and panel reads this file, not a fresh guess."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from brahma.effects import split_effects
from brahma.errors import BrahmaError
from brahma.models import Project, StoryPlan, StyleSpec

SETTINGS_NAME = "settings.json"
SETUP_KEYS = ("art", "palette", "line", "rendering", "lighting", "negative")
READING_KEYS = SETUP_KEYS + ("notes",)
_LIMITS = {"art": 120, "palette": 160, "line": 160, "rendering": 160, "lighting": 160, "negative": 200, "notes": 400}
ROLES = ("style", "sketch", "character", "location")


class ImageRef(BaseModel):
    """A picture kept with the project. `file` is relative to the project folder."""

    model_config = ConfigDict(extra="ignore")

    role: str
    file: str
    note: str = ""
    subject: str = ""
    applied: bool = True


class ProjectSettings(BaseModel):
    """Global setup for one story. `art` is the medium; the palette is shared by every image.

    `effects` holds what the look named that is a filter (sparks, glow, fog, rain...). The
    base pictures never see it; only a filter step reads it.
    """

    model_config = ConfigDict(extra="forbid")

    art: str = Field(min_length=3, max_length=120)
    palette: str = Field(min_length=3, max_length=160)
    line: str = Field(min_length=3, max_length=160)
    rendering: str = Field(min_length=3, max_length=160)
    lighting: str = Field(min_length=3, max_length=160)
    negative: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=400)
    effects: list[str] = Field(default_factory=list, max_length=16)
    references: list[ImageRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _effects_out_of_the_look(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        effects = [str(item).strip() for item in data.get("effects") or [] if str(item).strip()]
        for key in ("art", "palette", "line", "rendering", "lighting", "notes"):
            value = data.get(key)
            if not isinstance(value, str):
                continue
            clean, moved = split_effects(value)
            if not moved:
                continue
            if len(clean) >= 3 or key == "notes":
                data[key] = clean
            effects.extend(item for item in moved if item not in effects)
        data["effects"] = effects[:16]
        return data

    def as_style(self) -> StyleSpec:
        return StyleSpec(
            medium=self.art,
            palette=self.palette,
            line=self.line,
            rendering=self.rendering,
            lighting=self.lighting,
            negative=self.negative,
            notes=self.notes,
        )


def look_from_style(style: StyleSpec) -> dict:
    return {
        "art": style.medium,
        "palette": style.palette,
        "line": style.line,
        "rendering": style.rendering,
        "lighting": style.lighting,
        "negative": style.negative,
        "notes": style.notes,
    }


def clean_reading(raw: dict) -> dict:
    unknown = sorted(set(raw) - set(READING_KEYS))
    if unknown:
        raise BrahmaError("unknown reading keys: " + ", ".join(unknown))
    cleaned = {}
    for key in READING_KEYS:
        value = raw.get(key)
        if value is None or not str(value).strip():
            continue
        cleaned[key] = str(value).strip()[: _LIMITS[key]]
    return cleaned


def overlay_look(base: dict, layer: dict | None) -> dict:
    """Non-empty values in `layer` replace the same keys. Text the person wrote wins when applied last."""

    if not layer:
        return dict(base)
    data = dict(base)
    allowed = set(READING_KEYS)
    unknown = sorted(set(layer) - allowed)
    if unknown:
        raise BrahmaError("unknown setup keys: " + ", ".join(unknown))
    for key in READING_KEYS:
        value = layer.get(key)
        if value is None or not str(value).strip():
            continue
        data[key] = str(value).strip()[: _LIMITS[key]]
    return data


def settings_from_look(data: dict, references: list[ImageRef], effects: list[str] | None = None) -> ProjectSettings:
    payload = {key: data[key] for key in READING_KEYS}
    payload["effects"] = list(effects or [])
    payload["references"] = [item.model_dump() for item in references]
    try:
        return ProjectSettings.model_validate(payload)
    except Exception as exc:
        raise BrahmaError(f"setup is not usable: {exc}") from exc


def resolve_settings(plan: StoryPlan, setup: dict | None) -> ProjectSettings:
    """Caller values win. Anything left unset is taken once from the script and then locked."""

    return settings_from_look(overlay_look(look_from_style(plan.style), setup), [])


def save_settings(root: Path, settings: ProjectSettings) -> Path:
    path = root / SETTINGS_NAME
    path.write_text(json.dumps(settings.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_settings(root: Path) -> ProjectSettings:
    path = root / SETTINGS_NAME
    if not path.is_file():
        raise BrahmaError(f"no settings at {path}")
    try:
        return ProjectSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BrahmaError(f"settings are not usable: {exc}") from exc


def style_of(root: Path, project: Project) -> StyleSpec:
    path = root / SETTINGS_NAME
    if path.is_file():
        return load_settings(root).as_style()
    return project.plan.style


def setup_note(setup: dict | None) -> str:
    if not setup:
        return ""
    labels = {
        "art": "style.medium",
        "palette": "style.palette",
        "line": "style.line",
        "rendering": "style.rendering",
        "lighting": "style.lighting",
        "negative": "style.negative",
    }
    lines = []
    for key in SETUP_KEYS:
        value = setup.get(key)
        if value is None or not str(value).strip():
            continue
        lines.append(f"{labels[key]} = {str(value).strip()}")
    if not lines:
        return ""
    return (
        "\n\nProject setup is locked. Copy these strings exactly into style and do not invent another look:\n"
        + "\n".join(lines)
    )
