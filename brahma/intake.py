"""Copy uploaded pictures into the project and turn a hand drawing into look fields."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from brahma.consistency import ImageRecipe
from brahma.errors import BrahmaError
from brahma.models import slug_id
from brahma.settings import ROLES, ImageRef, clean_reading

_ROLE_ALIASES = {
    "reference": "style",
    "referencia": "style",
    "grafica": "style",
    "graphic": "style",
    "estilo": "style",
    "dibujo": "sketch",
    "drawing": "sketch",
    "boceto": "sketch",
    "mano": "sketch",
    "personaje": "character",
    "lugar": "location",
}


def normalize_role(value: object) -> str:
    if value is None or not str(value).strip():
        return "style"
    key = _ROLE_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
    if key not in ROLES:
        raise BrahmaError(f"image role must be one of {', '.join(ROLES)}")
    return key


def import_images(
    root: Path,
    images: list[dict] | None,
    existing: list[ImageRef] | None = None,
    read_image=None,
) -> list[tuple[ImageRef, dict]]:
    """Store each upload as PNG. A sketch reading, from the caller or a vision model, is returned beside it."""

    if not images:
        return []
    counts: dict[str, int] = {}
    for ref in existing or []:
        counts[ref.role] = counts.get(ref.role, 0) + 1
    stored: list[tuple[ImageRef, dict]] = []
    destination = root / "references"
    destination.mkdir(parents=True, exist_ok=True)
    for item in images:
        if not isinstance(item, dict) or not item.get("path"):
            raise BrahmaError("each reference image needs a path")
        source = Path(str(item["path"]))
        if not source.is_file():
            raise BrahmaError(f"image not found: {source}")
        role = normalize_role(item.get("role"))
        counts[role] = counts.get(role, 0) + 1
        relative = f"references/{role}-{counts[role]:02d}.png"
        _copy_png(source, root / relative)
        reading = _reading_for(item, root / relative, role, read_image)
        note = str(item.get("note") or reading.get("notes") or "").strip()[:400]
        subject = slug_id(item["id"]) if item.get("id") else ""
        ref = ImageRef(
            role=role,
            file=relative,
            note=note,
            subject=subject,
            applied=role != "sketch" or bool(reading),
        )
        stored.append((ref, reading))
    return stored


def uploaded_paths(root: Path, references: list[ImageRef], recipe: ImageRecipe) -> list[str]:
    """Style and sketch pictures guide every image. A character or place picture guides its own sheets and panels."""

    subjects = {item.key for item in recipe.references if item.role == "character"}
    places = {item.key for item in recipe.references if item.role == "location"}
    if recipe.kind == "character" and ":" in recipe.key:
        subjects.add(recipe.key.split(":", 1)[1])
    if recipe.kind == "location" and ":" in recipe.key:
        places.add(recipe.key.split(":", 1)[1])
    chosen: list[str] = []
    for ref in references:
        full = root / ref.file
        if not full.is_file():
            continue
        if ref.role in {"style", "sketch"}:
            chosen.append(str(full))
        elif ref.role == "character" and recipe.kind in {"character", "panel"} and (not ref.subject or ref.subject in subjects):
            chosen.append(str(full))
        elif ref.role == "location" and recipe.kind in {"location", "panel"} and (not ref.subject or ref.subject in places):
            chosen.append(str(full))
    return chosen


def _reading_for(item: dict, stored: Path, role: str, read_image) -> dict:
    if isinstance(item.get("reading"), dict):
        return clean_reading(item["reading"])
    if role != "sketch" or read_image is None:
        return {}
    try:
        got = read_image(stored)
    except Exception:
        return {}
    if not isinstance(got, dict):
        return {}
    return clean_reading(got)


def _copy_png(source: Path, dest: Path) -> None:
    try:
        with Image.open(source) as incoming:
            incoming.load()
            frame = incoming.convert("RGBA")
    except Exception as exc:
        raise BrahmaError(f"could not read image {source}: {exc}") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.save(dest, format="PNG")
