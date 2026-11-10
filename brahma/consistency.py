"""How a panel stays the same person, place, and drawing style as the last one.

Three locks, and one thing we refuse to do:

1. Style lock. One clause, one checkpoint, one sampler. Every image uses them verbatim.
2. Identity lock. Each character has a single sheet and each place a single plate.
   A panel may look at one of those images. It may not invent a new description.
3. Shot lock. Camera and angle come from a closed list, so the framing can change
   without rewriting the person.

The previous panel is not a reference. A strong copy of the last frame repeats the
pose and still lets the face drift. Identity comes from the sheet. Place comes from
the plate. Action comes from the script.

Sheets and plates are plain PNG files. Replacing one and redrawing is how a person
corrects the model without retraining it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from brahma.models import CharacterSpec, LocationSpec, PanelSpec, StyleSpec

# One conditioner. A second IP-Adapter on a 16 GB GPU fights the first and runs out of memory.
# 0.85 keeps the face and the coat. The prompt still owns the pose.
CHARACTER_WEIGHT = 0.85
# An empty panel has no face to protect, so the place plate can sit a little lower.
LOCATION_WEIGHT = 0.55

NO_LETTERING = (
    "no text, no letters, no captions, no speech balloons, no logos, no watermark, no signature, no panel border"
)

SHARED_NEGATIVE = (
    "text, letters, watermark, logo, signature, speech bubble, caption, panel border, "
    "extra fingers, malformed hands, extra limbs, duplicate face, mutated face, "
    "different outfit, costume change, different hairstyle, different face, "
    "photorealistic photo, 3d render, multiple art styles, blurry, cropped head"
)


@dataclass(frozen=True)
class SamplerLock:
    """Same numbers on every panel. Changing the sampler is a style change."""

    ckpt: str
    steps: int
    cfg: float
    sampler: str
    scheduler: str
    workflow: str
    ipadapter: str = ""
    clip_vision: str = ""


@dataclass(frozen=True)
class Reference:
    role: str
    key: str
    path: Path
    weight: float


@dataclass(frozen=True)
class ImageRecipe:
    key: str
    kind: str
    prompt: str
    negative_prompt: str
    width: int
    height: int
    seed: int
    references: tuple[Reference, ...]
    sampler: SamplerLock

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "references": [
                {"role": item.role, "key": item.key, "path": str(item.path), "weight": item.weight}
                for item in self.references
            ],
            "sampler": {
                "ckpt": self.sampler.ckpt,
                "steps": self.sampler.steps,
                "cfg": self.sampler.cfg,
                "sampler": self.sampler.sampler,
                "scheduler": self.sampler.scheduler,
                "workflow": self.sampler.workflow,
            },
        }


def stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("\n".join(parts).encode()).digest()
    return int.from_bytes(digest[:4], "big")


def style_clause(style: StyleSpec) -> str:
    clause = f"{style.medium}, {style.line}, {style.rendering}, palette: {style.palette}, lighting: {style.lighting}"
    if style.notes.strip():
        clause += f". Reference drawing: {style.notes.strip()}"
    return clause


def character_clause(character: CharacterSpec) -> str:
    return f"{character.appearance}. Wardrobe: {character.wardrobe}. Identity anchors: {character.distinguishing}."


def negative_prompt(style: StyleSpec, *, sheet: bool = False) -> str:
    extra = ", crowd, scenery, action pose" if sheet else ""
    own = f", {style.negative.strip()}" if style.negative.strip() else ""
    return f"{SHARED_NEGATIVE}{extra}{own}"


def style_anchor_prompt(style: StyleSpec) -> str:
    return (
        "graphic novel style sample, an empty street with no people, "
        f"{style_clause(style)}, single illustration, {NO_LETTERING}"
    )


def character_sheet_prompt(style: StyleSpec, character: CharacterSpec) -> str:
    return (
        "character model sheet of one single person shown twice, front view and three-quarter view, "
        "identical face, identical body, identical outfit, full body, neutral standing pose, "
        "plain flat light-gray background, even studio light, "
        f"{character_clause(character)} {style_clause(style)}, {NO_LETTERING}"
    )


def location_plate_prompt(style: StyleSpec, location: LocationSpec) -> str:
    return (
        "establishing illustration of a place with no people, "
        f"{location.appearance}, {location.lighting}, {style_clause(style)}, {NO_LETTERING}"
    )


def panel_prompt(style: StyleSpec, location: LocationSpec, characters: list[CharacterSpec], panel: PanelSpec) -> str:
    if characters:
        who = " ".join(f"{character.name}: {character_clause(character)}" for character in characters)
    else:
        who = "no people in frame"
    return (
        f"single graphic novel panel, {style_clause(style)}, "
        f"setting: {location.appearance}, {location.lighting}, "
        f"{panel.shot} shot, {panel.angle} camera, "
        f"{who} "
        f"action: {panel.action}. "
        f"{NO_LETTERING}"
    )


def panel_references(
    panel: PanelSpec,
    sheets: dict[str, Path],
    plates: dict[str, Path],
) -> tuple[Reference, ...]:
    """The one image this panel is allowed to condition on.

    Primary character first. If the panel is empty of people, the place plate.
    There is no parameter for the previous panel on purpose.
    """

    if panel.character_ids:
        character_id = panel.character_ids[0]
        return (Reference("character", character_id, sheets[character_id], CHARACTER_WEIGHT),)
    return (Reference("location", panel.location_id, plates[panel.location_id], LOCATION_WEIGHT),)


def text_sampler(sampler: SamplerLock) -> SamplerLock:
    """Sheets are text-to-image. They cannot condition on a sheet that does not exist yet."""

    return SamplerLock(
        ckpt=sampler.ckpt,
        steps=sampler.steps,
        cfg=sampler.cfg,
        sampler=sampler.sampler,
        scheduler=sampler.scheduler,
        workflow="sdxl",
        ipadapter=sampler.ipadapter,
        clip_vision=sampler.clip_vision,
    )
