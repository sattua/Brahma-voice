"""How a picture stays the same character, place, and drawing style as the last one.

Three locks:

1. Style lock. One clause from settings.json and one sampler. Every image uses them verbatim.
2. Identity lock. Each character has one sheet, one turnaround, and one pose sheet; each
   place has one plate. A picture may look at those images. It may not invent a new description.
3. Shot lock. Camera and angle come from a closed list, so the framing can change
   without rewriting the character.

Every panel is built in order: the empty place, then each figure in its box, then a filter
only if asked (brahma.stage). Effects never enter a base prompt: they leave the look, the
places, and the actions (brahma.effects) and stay in the negative. No two figures share a pose.

Which picture a new image may look at:

- A page panel: its plate looks at the place plate; each figure looks at its sheet, the
  turnaround view that matches how it faces the camera, and its pose sheet. Never at
  another page panel.
- A sequence is one continuous shot. A static camera shares one plate; each figure looks at
  the same figure in panel n-1, and only once n-1 has been accepted. See brahma.sequence.

Sheets, turnarounds, and plates are plain PNG files. Replacing one and redrawing is how a
person corrects the model without retraining it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from brahma.effects import EFFECTS_NEGATIVE, effects_in, fragments_without, without_effects, without_shot_words
from brahma.models import CharacterSpec, LocationSpec, Mark, PanelSpec, StyleSpec

# One conditioner. A second IP-Adapter on a 16 GB GPU fights the first and runs out of memory.
# 0.85 keeps the face and the coat. The prompt still owns the pose.
CHARACTER_WEIGHT = 0.85
# An empty panel has no face to protect, so the place plate can sit a little lower.
LOCATION_WEIGHT = 0.55

NO_LETTERING = (
    "no text, no letters, no captions, no speech balloons, no logos, no watermark, no signature, no panel border"
)

# Only what no book wants. Anatomy and medium belong to settings.negative, because a
# six-limbed cyborg or a photographic book needs the opposite of a generic default.
SHARED_NEGATIVE = (
    "text, letters, watermark, logo, signature, speech bubble, caption, panel border, "
    "duplicate face, mutated face, blurry, cropped head"
)
PANEL_NEGATIVE = "different outfit, costume change, different face, different body"
SHEET_NEGATIVE = "crowd, scenery"

_GENERIC_WORDS = {
    "the", "and", "with", "on", "of", "a", "an", "in", "to", "art", "style", "light", "lights",
    "color", "colors", "colour", "image", "picture", "detail", "details", "soft", "dark",
}


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
    effects: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "kind": self.kind,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "effects": list(self.effects),
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
    parts = [without_effects(style.medium), without_effects(style.line), without_effects(style.rendering)]
    clause = ", ".join(part for part in parts if part)
    palette = without_effects(style.palette)
    lighting = without_effects(style.lighting)
    if palette:
        clause += f", palette: {palette}"
    if lighting:
        clause += f", lighting: {lighting}"
    notes = without_effects(style.notes.strip())
    if notes:
        clause += f". Reference drawing: {notes}"
    return clause


def scene_style(style: StyleSpec) -> StyleSpec:
    """The look inside a place: no shot size (the shot owns the camera) and no sheet light."""

    return style.model_copy(
        update={
            "medium": without_shot_words(style.medium) or style.medium,
            "lighting": fragments_without(style.lighting, r"\bsheets?\b|\bhojas?\b"),
        }
    )


def sheet_style(style: StyleSpec) -> StyleSpec:
    """The look on a white sheet: no light meant for scenes."""

    return style.model_copy(
        update={"lighting": fragments_without(style.lighting, r"\bscenes?\b|\bpanels?\b|\bescenas?\b")}
    )


def scene_effects(*texts: str) -> tuple[str, ...]:
    """Effects named by the text of a place or an action. They leave the base prompt."""

    return tuple(effects_in(*texts))


def character_clause(character: CharacterSpec) -> str:
    """The same string on the sheet and on every panel. Marks are said in the wearer's terms."""

    clause = f"{character.appearance}."
    if character.body.strip():
        clause += f" Body: {character.body.strip()}."
    clause += f" Wardrobe: {character.wardrobe}. Identity anchors: {character.distinguishing}."
    for mark in character.marks:
        clause += f" {_wearer_mark(mark)}."
    return clause


def marks_clause(character: CharacterSpec, view: str) -> str:
    """Where each one-sided mark lands in the picture for this view."""

    lines = [_viewed_mark(mark, view) for mark in character.marks if mark.side != "center"]
    if not lines:
        return ""
    return f"In this {view} view: " + "; ".join(lines) + "."


def _wearer_mark(mark: Mark) -> str:
    if mark.side == "center":
        return f"{mark.part}: {mark.text}"
    side = "left" if mark.side == "wearer_left" else "right"
    return f"{mark.part} on the wearer's {side} side: {mark.text}"


def _viewed_mark(mark: Mark, view: str) -> str:
    left = mark.side == "wearer_left"
    if view == "front":
        where = "the right side of the picture" if left else "the left side of the picture"
    elif view == "back":
        where = "the left side of the picture" if left else "the right side of the picture"
    else:
        where = "the wearer's left side" if left else "the wearer's right side"
    return f"the {mark.part} is on {where}"


def negative_prompt(style: StyleSpec, *, sheet: bool = False) -> str:
    banned = [part.strip() for part in style.negative.split(",") if part.strip()]
    if sheet:
        banned = [part for part in banned if part.lower() != "white void"]
    parts = [SHARED_NEGATIVE, SHEET_NEGATIVE if sheet else PANEL_NEGATIVE, EFFECTS_NEGATIVE, *banned]
    terms = [term.strip() for part in parts for term in part.split(",") if term.strip()]
    wanted = _wanted_words(style)
    kept: list[str] = []
    for term in terms:
        if _contradicts(term, wanted) or term in kept:
            continue
        kept.append(term)
    return ", ".join(kept)


def _wanted_words(style: StyleSpec) -> set[str]:
    """Words the look asks for. A negation such as "no emissive lights" asks for nothing."""

    text = ", ".join([style.medium, style.rendering])
    words: set[str] = set()
    for chunk in re.split(r"[,.;]", text):
        chunk = chunk.strip().lower()
        if not chunk or re.match(r"^(no|not|without|never)\b", chunk):
            continue
        words.update(word for word in re.findall(r"[a-z0-9]+", chunk) if len(word) >= 2)
    return words - _GENERIC_WORDS


def _contradicts(term: str, wanted: set[str]) -> bool:
    words = {word for word in re.findall(r"[a-z0-9]+", term.lower()) if len(word) >= 2} - _GENERIC_WORDS
    return bool(words & wanted)


_SHEET_ONLY = re.compile(r"\b(sheets?|white background|plain background|turnaround|both views)\b", re.I)


def figure_clause(character: CharacterSpec, view: str) -> str:
    """The character as a figure in a scene: the sheet's own layout and background stay on the sheet."""

    sentences = re.split(r"(?<=\.)\s+", character_clause(character))
    kept = " ".join(sentence for sentence in sentences if not _SHEET_ONLY.search(sentence))
    text = f"{character.name}. {without_effects(kept)} {marks_clause(character, view)}"
    return re.sub(r"\.{2,}", ".", text).strip()


def place_text(location: LocationSpec) -> str:
    """The place as a base picture sees it: no weather, no glow, no particles."""

    parts = [without_effects(location.appearance), without_effects(location.lighting)]
    return ", ".join(part for part in parts if part)


def style_anchor_prompt(style: StyleSpec, location: LocationSpec | None = None) -> str:
    if location is None:
        scene = "an empty scene with no figures"
    else:
        scene = f"{place_text(location)}, no characters and no figures"
    return f"style sample, {scene}, {style_clause(scene_style(style))}, single illustration, {NO_LETTERING}"


def character_sheet_prompt(style: StyleSpec, character: CharacterSpec) -> str:
    body = f"the exact body plan of {character.body.strip()}" if character.body.strip() else "the same body"
    return (
        "character model sheet of one single character shown twice, front view and three-quarter view, "
        f"identical face, {body}, identical outfit and marks in both views, full body, relaxed neutral stance, "
        "plain flat white background, even studio light, "
        f"{character_clause(character)} {marks_clause(character, 'front')} {style_clause(sheet_style(style))}, {NO_LETTERING}"
    )


def turnaround_prompt(style: StyleSpec, character: CharacterSpec) -> str:
    per_view = " ".join(clause for clause in (marks_clause(character, view) for view in ("front", "back")) if clause)
    return (
        "character turnaround of the same single character as the reference sheet, four full-body views in one row, "
        "left to right: front, three-quarter, side profile, back. Identical character in every view, same scale, "
        "same relaxed neutral stance, plain flat white background, even studio light, "
        f"{character_clause(character)} {per_view} {style_clause(sheet_style(style))}, {NO_LETTERING}"
    )


DEFAULT_POSES = (
    "walking forward",
    "running",
    "kneeling",
    "crouching low",
    "turning to look back over the shoulder",
)


def pose_sheet_prompt(style: StyleSpec, character: CharacterSpec) -> str:
    poses = character.poses or list(DEFAULT_POSES)
    listed = "; ".join(f"{number}. {pose}" for number, pose in enumerate(poses, start=1))
    per_view = " ".join(clause for clause in (marks_clause(character, view) for view in ("front", "back")) if clause)
    return (
        "character pose sheet of the same single character as the reference sheet, plain flat white background, "
        "even studio light, two rows separated by clear white space. "
        "Top row: three head-and-shoulders close-ups of the face, left to right: front, three-quarter, side profile. "
        f"Bottom row: {len(poses)} full-body action poses, left to right: {listed}. "
        "Identical character, outfit, gear, and marks in every drawing, same scale inside each row, "
        "every pose different from the others, figures do not overlap, "
        f"{character_clause(character)} {per_view} {style_clause(sheet_style(style))}, {NO_LETTERING}"
    )


def location_plate_prompt(style: StyleSpec, location: LocationSpec) -> str:
    return (
        "establishing illustration of a place with no people and no creatures, "
        f"{place_text(location)}, {style_clause(scene_style(style))}, {NO_LETTERING}"
    )


DISTINCT_POSES = "Each figure has its own pose; no two figures share a pose or a stride."


def panel_prompt(style: StyleSpec, location: LocationSpec, characters: list[CharacterSpec], panel: PanelSpec) -> str:
    shown = [character for character in characters if character.visual]
    if shown:
        view = panel.view()
        who = " ".join(
            f"{character.name}: {character_clause(character)} {marks_clause(character, view)}".strip()
            for character in shown
        )
        if len(shown) > 1:
            who += f" {DISTINCT_POSES}"
    else:
        who = "no people in frame"
    return (
        f"single graphic novel panel, {style_clause(scene_style(style))}, "
        f"setting: {place_text(location)}, "
        f"{panel.shot} shot, {panel.angle} camera, "
        f"{who} "
        f"action: {without_effects(panel.action)}. "
        f"{NO_LETTERING}"
    )


def panel_references(
    panel: PanelSpec,
    sheets: dict[str, Path],
    plates: dict[str, Path],
    views: dict[str, Path] | None = None,
    poses: dict[str, Path] | None = None,
) -> tuple[Reference, ...]:
    """The images a page panel may condition on.

    The first drawn character's sheet, then its turnaround view for this panel, then its
    pose sheet. A panel with nobody drawn gets the place plate. Another page panel is
    never a reference.
    """

    for character_id in panel.character_ids:
        if character_id not in sheets:
            continue
        refs = [Reference("character", character_id, sheets[character_id], CHARACTER_WEIGHT)]
        view = (views or {}).get(character_id)
        if view is not None:
            refs.append(Reference("view", character_id, view, CHARACTER_WEIGHT))
        posed = (poses or {}).get(character_id)
        if posed is not None:
            refs.append(Reference("poses", character_id, posed, CHARACTER_WEIGHT))
        return tuple(refs)
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
