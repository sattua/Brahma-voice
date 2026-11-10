"""What the Cursor agent calls. Planning and pages stay here; pixels come back as files."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from brahma.config import AppConfig, image_model, sampler_lock, script_model
from brahma.consistency import ImageRecipe
from brahma.errors import BrahmaError
from brahma.intake import import_images, uploaded_paths
from brahma.models import DialogueLine
from brahma.settings import load_settings, look_from_style, overlay_look, save_settings, settings_from_look
from brahma.vision import read_drawing
from brahma.pipeline import (
    assemble_pages,
    draw_panels,
    generate_references,
    panel_slots,
    plan_story,
    reference_slots,
)
from brahma.store import load_project, save_project

_ASPECTS = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9, "9:16": 9 / 16}


def health(config: AppConfig) -> dict:
    script = script_model(config)
    images = image_model(config)
    script_ok, script_detail = script.health()
    image_health = images.health()
    if config.image.backend == "cursor":
        identity = "character sheet passed as reference_image_paths"
    elif "ipadapter" in config.image.workflow:
        identity = "one IP-Adapter reference"
    else:
        identity = "locked prompt"
    ready = script_ok and image_health.available
    return {
        "status": "ok" if ready else "blocked",
        "script": {"backend": script.name, "model": config.script.model, "ok": script_ok, "detail": script_detail},
        "image": {"backend": images.name, "ok": image_health.available, "detail": image_health.detail},
        "identity": identity,
        "vision": config.script.vision_model or "hand drawings are read in the chat and saved into settings",
    }


def plan(story: str, config: AppConfig, setup: dict | None = None, images: list[dict] | None = None) -> dict:
    project, root = plan_story(
        story,
        script_model(config),
        sampler_lock(config),
        config.workspace_dir,
        setup,
        images,
        _reader(config),
    )
    return _after_look(root, project, config, "plan")


def configure(
    project: str | Path,
    config: AppConfig,
    setup: dict | None = None,
    images: list[dict] | None = None,
) -> dict:
    """Add graphic references, or fold a hand drawing's reading into the project settings."""

    root = _root(project)
    current = load_settings(root)
    imported = import_images(root, images, current.references, _reader(config))
    data = look_from_style(current.as_style())
    references = list(current.references)
    for ref, reading in imported:
        if reading:
            data = overlay_look(data, reading)
            if reading.get("notes"):
                ref.note = reading["notes"]
        references.append(ref)
    if setup:
        data = overlay_look(data, setup)
        if any(str(setup.get(key) or "").strip() for key in ("art", "palette", "line", "rendering", "lighting", "notes")):
            for ref in references:
                if ref.role == "sketch" and not ref.applied:
                    ref.applied = True
                    if not ref.note and setup.get("notes"):
                        ref.note = str(setup["notes"]).strip()[:400]
    settings = settings_from_look(data, references)
    save_settings(root, settings)
    stored = load_project(root)
    stored.plan.style = settings.as_style()
    save_project(root, stored)
    return _after_look(root, stored, config, "settings")


def refs(project: str | Path, config: AppConfig, images: list[dict] | None = None, force: bool = False) -> dict:
    root = _root(project)
    sampler = sampler_lock(config)
    if config.image.backend != "cursor":
        generate_references(root, image_model(config), sampler, force=force, log=lambda *_args: None)
        slots = reference_slots(root, sampler)
        return {
            "status": "ok",
            "operation": "refs",
            "project": str(root),
            "files": [str(dest) for _recipe, dest in slots],
            "next_actions": ["novel_draw"],
        }
    if force:
        _drop(root, reference_slots(root, sampler))
    installed = _install(reference_slots(root, sampler), images)
    pending = _jobs(reference_slots(root, sampler), missing_only=True, root=root)
    if pending:
        return {
            "status": "needs_images",
            "operation": "refs",
            "project": str(root),
            "installed": installed,
            "jobs": pending,
            "next_actions": [_DRAW_REFS],
        }
    slots = reference_slots(root, sampler)
    return {
        "status": "ok",
        "operation": "refs",
        "project": str(root),
        "files": [str(dest) for _recipe, dest in slots],
        "next_actions": [
            "Open every file and check the face, the clothes and the place. "
            "If one is wrong, call novel_refs with force true. If they hold, call novel_draw."
        ],
    }


def draw(
    project: str | Path,
    config: AppConfig,
    images: list[dict] | None = None,
    panel: str | None = None,
    force: bool = False,
) -> dict:
    root = _root(project)
    sampler = sampler_lock(config)
    if config.image.backend != "cursor":
        draw_panels(root, image_model(config), sampler, panel=panel, force=force, log=lambda *_args: None)
        slots = panel_slots(root, sampler, panel)
        return {
            "status": "ok",
            "operation": "draw",
            "project": str(root),
            "files": [str(dest) for _recipe, dest in slots],
            "next_actions": ["novel_assemble"],
        }
    missing = _missing_refs(root)
    if missing:
        return {
            "status": "blocked",
            "operation": "draw",
            "reason": "references_missing",
            "missing": missing,
            "project": str(root),
            "next_actions": ["novel_refs"],
        }
    if force or (panel and not images):
        slots = panel_slots(root, sampler, panel)
        if force or any(dest.is_file() for _recipe, dest in slots):
            _drop(root, slots)
    installed = _install(panel_slots(root, sampler), images)
    pending = _jobs(panel_slots(root, sampler, panel), missing_only=True, root=root)
    if pending:
        return {
            "status": "needs_images",
            "operation": "draw",
            "project": str(root),
            "installed": installed,
            "jobs": pending,
            "next_actions": [_DRAW_PANELS],
        }
    ready = all(dest.is_file() for _recipe, dest in panel_slots(root, sampler))
    return {
        "status": "ok",
        "operation": "draw",
        "project": str(root),
        "files": [str(dest) for _recipe, dest in panel_slots(root, sampler, panel)],
        "next_actions": ["novel_assemble"] if ready else ["novel_draw"],
    }


def assemble(project: str | Path, dialogue: list[dict] | None = None) -> dict:
    root = _root(project)
    if dialogue:
        _apply_dialogue(root, dialogue)
    pages = assemble_pages(root, log=lambda *_args: None)
    return {
        "status": "ok",
        "operation": "assemble",
        "project": str(root),
        "pages": [str(path) for path in pages],
        "previews": [str(path.with_suffix(".png")) for path in pages],
    }


_READ_SKETCH = (
    "Open each sketch file and read the drawing. Call novel_settings with setup fields "
    "art, palette, line, rendering, lighting, and notes, in English, taken from what you see. "
    "Do not invent colors the drawing does not use."
)
_DRAW_REFS = (
    "For each job, generate an image. Copy description verbatim, set aspect_ratio, "
    "and pass reference_image_paths unchanged. Do not use another panel as a reference. "
    "Then call novel_refs with images [{key, path}]."
)
_DRAW_PANELS = (
    "For each job, generate an image. Copy description verbatim, set aspect_ratio, "
    "and pass reference_image_paths unchanged. The reference is the identity lock. "
    "Do not pass the previous panel. Then call novel_draw with images [{key, path}]."
)


def _root(project: str | Path) -> Path:
    root = Path(project)
    if not (root / "project.json").is_file():
        raise BrahmaError(f"no story at {root}")
    return root


def _after_look(root: Path, project, config: AppConfig, operation: str) -> dict:
    settings = load_settings(root)
    pending = [
        {"file": str((root / ref.file).resolve()), "role": ref.role}
        for ref in settings.references
        if ref.role == "sketch" and not ref.applied
    ]
    result = {
        "status": "planned" if operation == "plan" else "ok",
        "operation": operation,
        "project": str(root),
        "folder": root.name,
        "title": project.plan.title,
        "language": project.plan.language,
        "pages": len(project.plan.pages),
        "characters": [character.name for character in project.plan.characters],
        "locations": [location.name for location in project.plan.locations],
        "settings": settings.model_dump(),
        "settings_path": str(root / "settings.json"),
        "next_actions": ["novel_refs"],
    }
    if pending:
        result["status"] = "needs_reading"
        result["pending"] = pending
        result["next_actions"] = [_READ_SKETCH]
        return result
    if config.image.backend == "cursor":
        jobs = _jobs(reference_slots(root, sampler_lock(config)), missing_only=True, root=root)
        if jobs:
            result["status"] = "needs_images"
            result["jobs"] = jobs
            result["next_actions"] = [_DRAW_REFS]
    return result


def _reader(config: AppConfig):
    if not (config.script.vision_model or "").strip():
        return None
    return lambda path: read_drawing(path, config)


def _jobs(slots: list[tuple[ImageRecipe, Path]], *, missing_only: bool = False, root: Path | None = None) -> list[dict]:
    references = load_settings(root).references if root is not None and (root / "settings.json").is_file() else []
    jobs = []
    for recipe, dest in slots:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.with_suffix(".json").write_text(
            json.dumps(recipe.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if missing_only and dest.is_file():
            continue
        extra = uploaded_paths(root, references, recipe) if root is not None else []
        jobs.append(_job(recipe, dest, extra))
    return jobs


def _job(recipe: ImageRecipe, dest: Path, extra: list[str] | None = None) -> dict:
    ratio = recipe.width / recipe.height
    aspect = min(_ASPECTS, key=lambda name: abs(_ASPECTS[name] - ratio))
    paths: list[str] = []
    for item in [str(ref.path) for ref in recipe.references] + (extra or []):
        if item not in paths:
            paths.append(item)
    return {
        "key": recipe.key,
        "kind": recipe.kind,
        "description": f"{recipe.prompt} Avoid: {recipe.negative_prompt}.",
        "aspect_ratio": aspect,
        "reference_image_paths": paths[:4],
        "dest": str(dest),
    }


def _install(slots: list[tuple[ImageRecipe, Path]], images: list[dict] | None) -> list[str]:
    if not images:
        return []
    by_key = {recipe.key: dest for recipe, dest in slots}
    installed: list[str] = []
    for item in images:
        key = str(item.get("key", ""))
        raw = item.get("path")
        if not key or not raw:
            raise BrahmaError("each image needs key and path")
        if key not in by_key:
            raise BrahmaError(f"unknown image key {key}")
        source = Path(str(raw))
        if not source.is_file():
            raise BrahmaError(f"image not found: {source}")
        with Image.open(source) as incoming:
            incoming.load()
            frame = incoming.convert("RGBA")
        dest = by_key[key]
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.save(dest, format="PNG")
        installed.append(key)
    return installed


def _drop(root: Path, slots: list[tuple[ImageRecipe, Path]]) -> None:
    project = load_project(root)
    changed = False
    for recipe, dest in slots:
        if dest.is_file():
            project.bump(recipe.key)
            dest.unlink()
            changed = True
    if changed:
        save_project(root, project)


def _missing_refs(root: Path) -> list[str]:
    project = load_project(root)
    missing = []
    if not (root / "refs" / "style.png").is_file():
        missing.append("style")
    from brahma.pipeline import _reference_paths

    sheets, plates = _reference_paths(root, project.plan)
    missing += [f"character:{key}" for key, path in sheets.items() if not path.is_file()]
    missing += [f"location:{key}" for key, path in plates.items() if not path.is_file()]
    return missing


def _apply_dialogue(root: Path, dialogue: list[dict]) -> None:
    project = load_project(root)
    for item in dialogue:
        panel_id = str(item.get("panel", ""))
        text = str(item.get("text", "")).strip()
        if not panel_id or not text:
            raise BrahmaError("each dialogue edit needs panel and text")
        try:
            _page, panel = project.plan.panel(panel_id)
        except KeyError:
            raise BrahmaError(f"unknown panel {panel_id}") from None
        if panel.dialogue:
            panel.dialogue[0].text = text[:240]
        else:
            panel.dialogue.append(DialogueLine(speaker="narrator", text=text[:240]))
    save_project(root, project)
