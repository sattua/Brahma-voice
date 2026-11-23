"""What the Cursor agent calls. Planning and pages stay here; pixels come back as files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from brahma import page_stage
from brahma.config import AppConfig, image_model, sampler_lock, script_model
from brahma.consistency import (
    ImageRecipe,
    figure_clause,
    marks_clause,
    negative_prompt,
    place_text,
    scene_style,
    style_clause,
)
from brahma.edit import finish_edit, start_edit
from brahma.errors import BrahmaError
from brahma.intake import import_images, uploaded_paths
from brahma.models import MAX_CHARACTERS, CharacterSpec, DialogueLine, PanelFigure, Project
from brahma.review import SHOT_REGIONS, duplicate_figures, review_picture
from brahma.sequence import (
    MAX_REFERENCES,
    SequenceFrame,
    SequenceSettings,
    accept_panel,
    blocking_path,
    existing_panels,
    figure_frame,
    filter_frame,
    fx_path,
    load_sequence,
    panel_path,
    plate_frame,
    plate_path,
    previous_figure,
    release_after,
    save_sequence,
    stage_of,
    under_cursor_assets,
    unify_frame,
)
from brahma.stage import back_to_front, blocking_preview, blocking_report, ground_ratio
from brahma.settings import load_settings, look_from_style, overlay_look, save_settings, settings_from_look
from brahma.takes import list_takes, record_take, retire
from brahma.vision import read_drawing
from brahma.pipeline import (
    assemble_pages,
    draw_panels,
    generate_references,
    panel_slots,
    plan_story,
    figure_view,
    pose_reference,
    reference_slots,
    view_reference,
)
from brahma.store import load_project, save_project

_ASPECTS = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9, "9:16": 9 / 16}
CHARACTER_FIELDS = {"name", "role", "appearance", "wardrobe", "distinguishing", "body", "marks", "poses", "visual"}


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
    settings = settings_from_look(data, references, current.effects)
    save_settings(root, settings)
    stored = load_project(root)
    stored.plan.style = settings.as_style()
    save_project(root, stored)
    return _after_look(root, stored, config, "settings")


def refs(
    project: str | Path,
    config: AppConfig,
    images: list[dict] | None = None,
    force: bool = False,
    key: str | None = None,
) -> dict:
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
        chosen = [slot for slot in reference_slots(root, sampler) if key is None or slot[0].key == key]
        if key is not None and not chosen:
            raise BrahmaError(f"unknown reference key {key}")
        _drop(root, chosen)
    result = _install(root, reference_slots(root, sampler), images)
    pending = _jobs(reference_slots(root, sampler), missing_only=True, root=root)
    if pending:
        return {
            "status": "needs_images",
            "operation": "refs",
            "project": str(root),
            **result,
            "jobs": pending,
            "next_actions": [_DRAW_REFS],
        }
    slots = reference_slots(root, sampler)
    return {
        "status": "ok",
        "operation": "refs",
        "project": str(root),
        **result,
        "files": [str(dest) for _recipe, dest in slots],
        "next_actions": [
            "Open every file and call novel_review on each sheet before drawing. "
            "To fix one region, call novel_edit. To redraw one picture, call novel_refs with force true and key. "
            "If they hold, call novel_draw."
        ],
    }


def draw(
    project: str | Path,
    config: AppConfig,
    images: list[dict] | None = None,
    panel: str | None = None,
    force: bool = False,
    blocking: dict | None = None,
    effects: list[str] | None = None,
    approve_blocking: list[str] | None = None,
) -> dict:
    """Page panels, staged: the empty place, then the blocking the person approves, then each character in its box,
    then an optional filter."""

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
    early = sorted({str(key) for key in blocking or {}} & {str(key) for key in approve_blocking or []})
    if early:
        raise BrahmaError(
            f"the blocking of {', '.join(early)} changes in this same call. Show the new preview first, then approve it."
        )
    changed = _apply_blocking(root, blocking) if blocking else []
    stored = load_project(root)
    chosen = page_stage.page_panels(stored, panel)
    if force or (panel and not images and not effects and not blocking and not approve_blocking):
        for item in chosen:
            page_stage.restart(root, item.key)
            item.spec.blocked = False
        save_project(root, stored)
    if approve_blocking:
        _approve_blocking(root, stored, approve_blocking)
    installed: list[str] = []
    rejected: list[str] = []
    takes: list[str] = []
    for item in images or []:
        key, accepted, take = page_stage.install(root, stored, item)
        (installed if accepted else rejected).append(key)
        takes.append(take)
    if images:
        save_project(root, stored)
    jobs = []
    waiting = {}
    for item in chosen:
        if effects:
            jobs.append(page_stage.fx_job(root, stored, item, list(effects)))
            continue
        job = page_stage.next_job(root, stored, item)
        if job is None:
            continue
        if job.get("step") == "blocking":
            waiting[item.key] = job
        else:
            jobs.append(job)
    result = {"operation": "draw", "project": str(root), "installed": installed, "rejected": rejected, "takes": takes}
    if changed:
        result["blocking_to_approve"] = changed
    if waiting:
        result["blocking"] = waiting
    if jobs and not (effects and images):
        actions = [_DRAW_PANELS] + ([_APPROVE_BLOCKING] if waiting else [])
        return {**result, "status": "needs_images", "jobs": jobs, "next_actions": actions}
    if waiting:
        return {**result, "status": "needs_approval", "next_actions": [_APPROVE_BLOCKING]}
    ready = all(page_stage.final_path(root, item.key).is_file() for item in page_stage.page_panels(stored))
    return {
        **result,
        "status": "ok",
        "files": [str(page_stage.final_path(root, item.key)) for item in chosen],
        "next_actions": ["novel_assemble"] if ready else ["novel_draw"],
    }


def _apply_blocking(root: Path, blocking: dict) -> list[str]:
    """Boxes, poses, and horizon for page panels: {panel: [{character, box, pose}]} or {panel: {horizon, figures}}.

    A moved box or pose starts the panel's figures again; a new horizon alone asks again for the yes on an
    unfinished panel. Either way the changed panel's blocking waits for approval. Returns the changed panels."""

    stored = load_project(root)
    changed: list[str] = []
    for key, value in blocking.items():
        key = str(key)
        try:
            _page, spec = stored.plan.panel(key)
        except KeyError:
            raise BrahmaError(f"unknown panel {key}") from None
        figures = value.get("figures") if isinstance(value, dict) else value
        before = page_stage.placements(stored, spec)
        horizon = spec.horizon
        try:
            if figures is not None:
                spec.figures = [PanelFigure.model_validate(item) for item in figures or []]
            if isinstance(value, dict) and "horizon" in value:
                spec.horizon = None if value["horizon"] is None else float(value["horizon"])
                if spec.horizon is not None and not 0 <= spec.horizon <= 1:
                    raise ValueError("horizon is a share of the frame height, 0..1 from the top")
        except Exception as exc:
            raise BrahmaError(f"blocking for {key} is not usable: {exc}") from exc
        unknown = [item.character for item in spec.figures if item.character not in spec.character_ids]
        if unknown:
            raise BrahmaError(f"{key} does not show {', '.join(unknown)}")
        moved = page_stage.placements(stored, spec) != before
        if moved:
            page_stage.stage_for(root, key).reset()
            retire(root, key, page_stage.final_path(root, key), "blocking changed")
        if moved or (spec.horizon != horizon and not page_stage.final_path(root, key).is_file()):
            spec.blocked = False
            page_stage.blocking_file(root, key).unlink(missing_ok=True)
            changed.append(key)
    save_project(root, stored)
    return changed


def _approve_blocking(root: Path, stored: Project, keys: list[str]) -> None:
    """The person said yes to these previews. Their figures may now be drawn."""

    for key in keys:
        try:
            _page, spec = stored.plan.panel(str(key))
        except KeyError:
            raise BrahmaError(f"unknown panel {key}") from None
        if not page_stage.plate_file(root, str(key)).is_file():
            raise BrahmaError(f"no plate for {key} yet. The empty place comes first.")
        if not page_stage.blocking_file(root, str(key)).is_file():
            raise BrahmaError(f"no blocking preview for {key}. Call novel_draw without panel and show the preview it returns.")
        spec.blocked = True
    save_project(root, stored)


def character(project: str | Path, config: AppConfig, character_id: str, fields: dict) -> dict:
    """Change one character in project.json through the model, so a bad value is refused, not saved."""

    root = _root(project)
    unknown = sorted(set(fields or {}) - CHARACTER_FIELDS)
    if unknown:
        raise BrahmaError("unknown character fields: " + ", ".join(unknown))
    stored = load_project(root)
    current = next((item for item in stored.plan.characters if item.id == character_id), None)
    if current is None:
        if len(stored.plan.characters) >= MAX_CHARACTERS:
            raise BrahmaError(f"a story has at most {MAX_CHARACTERS} characters")
        data = {"id": character_id}
    else:
        data = current.model_dump()
    data.update(fields or {})
    try:
        updated = CharacterSpec.model_validate(data)
    except Exception as exc:
        raise BrahmaError(f"character is not usable: {exc}") from exc
    if current is None:
        stored.plan.characters.append(updated)
    else:
        stored.plan.characters[stored.plan.characters.index(current)] = updated
    save_project(root, stored)
    sampler = sampler_lock(config)
    slots = [slot for slot in reference_slots(root, sampler) if slot[0].key.endswith(f":{updated.id}")]
    _jobs(slots, root=root)
    return {
        "status": "ok",
        "operation": "character",
        "project": str(root),
        "character": updated.model_dump(),
        "front": marks_clause(updated, "front"),
        "back": marks_clause(updated, "back"),
        "next_actions": [
            "The text is saved. A picture that must show the change needs novel_edit on that region, "
            "or novel_refs with force true and key for that sheet."
        ],
    }


def sequence(
    project: str | Path,
    config: AppConfig,
    name: str,
    op: str,
    *,
    index: int | None = None,
    settings: dict | None = None,
    image: str | None = None,
    verdict: str = "pending",
    note: str = "",
    step: str = "panel",
    figure: str | None = None,
    effects: list[str] | None = None,
) -> dict:
    """One continuous shot in panels/<name>, built place first, figures after, filter at the end."""

    root = _root(project)
    slug = _slug(name)
    folder = root / "panels" / slug
    base = {"operation": "sequence", "op": op, "project": str(root), "sequence": str(folder)}
    if op in {"create", "set"}:
        if not settings:
            raise BrahmaError("create and set need settings (action, place, time, shot, figures, blocking, beats, ...)")
        if op == "create" and (folder / "sequence.json").is_file():
            raise BrahmaError(f"sequence {name} exists; use op set to change it")
        before = load_sequence(folder) if op == "set" else None
        data = before.model_dump() if before is not None else {}
        data.update(settings)
        try:
            locked = SequenceSettings.model_validate(data)
        except Exception as exc:
            raise BrahmaError(f"sequence settings are not usable: {exc}") from exc
        unblocked: list[int] = []
        released: list[int] = []
        if before is not None:
            unblocked, released = _blocking_moved(folder, before, locked)
        save_sequence(folder, locked)
        result = {**base, "status": "ok", "settings": locked.model_dump(), "next_actions": ["novel_sequence op frame index 1"]}
        if unblocked:
            result["blocking_to_approve"] = unblocked
            result["next_actions"] = [f"novel_sequence op frame index {unblocked[0]}: show the new blocking and wait for a yes"]
        if released:
            result["released"] = released
        return result
    current = load_sequence(folder)
    if op == "status":
        return {**base, "status": "ok", "settings": current.model_dump(), "panels": existing_panels(folder), "stages": _stages(folder, current)}
    if op == "unify":
        number = _index(index)
        cast = _sequence_cast(root, current)
        frame = unify_frame(folder, number, views=cast["views"], poses=cast["poses"])
        later = [item for item in existing_panels(folder) if item > number]
        return {
            **base,
            "status": "needs_images",
            "jobs": [_frame_job(f"sequence:{slug}:{number:02d}", frame, current.aspect_ratio)],
            "remaining": later,
            "next_actions": [
                _DRAW_SEQUENCE,
                "Place it with verdict accepted only after review. Then unify the next panel in remaining.",
            ],
        }
    number = _index(index if index is not None or op != "plate" else 1)
    if op == "frame":
        return _next_step(root, folder, slug, current, number, base)
    if op == "plate":
        frame = plate_frame(folder, number, **_place_refs(root, current))
        return _step_job(base, _step_key(slug, current, number, "plate"), frame, current.aspect_ratio)
    if op == "stage":
        if not plate_path(folder, current, number).is_file():
            raise BrahmaError("no plate yet. The empty place comes first: op plate")
        return {**base, **_blocking_step(folder, current, number)}
    if op == "figure":
        _require_blocking(folder, current, number)
        frame = _figure_frame(root, folder, current, number, figure)
        return _step_job(base, _step_key(slug, current, number, "figure", frame.figure), frame, current.aspect_ratio)
    if op == "filter":
        frame = filter_frame(folder, number, effects)
        return _step_job(base, _step_key(slug, current, number, "filter"), frame, current.aspect_ratio)
    if op == "place":
        if not image:
            raise BrahmaError("place needs image, the file the image tool wrote")
        if verdict not in {"pending", "accepted", "rejected"}:
            raise BrahmaError("verdict is pending, accepted, or rejected")
        if step == "plate":
            return {**base, **_place_plate(root, folder, slug, current, number, _source(image), verdict, note)}
        if step == "figure":
            if verdict != "rejected":
                _require_blocking(folder, current, number)
            return {**base, **_place_figure(root, folder, slug, current, number, figure, _source(image), verdict, note)}
        if step == "filter":
            key = _step_key(slug, current, number, "filter")
            dest = fx_path(folder, number)
            if verdict != "rejected" and dest.is_file():
                retire(root, key, dest, "replaced by a new filter")
            take = record_take(
                root, key, _source(image), dest=None if verdict == "rejected" else dest,
                status="rejected" if verdict == "rejected" else "accepted", note=note,
            )
            return {**base, "status": "ok", "step": "filter", "take": str(take), "dest": str(dest)}
        if step != "panel":
            raise BrahmaError("step is plate, figure, filter, or panel")
        key = f"sequence:{slug}:{number:02d}"
        dest = panel_path(folder, number)
        if verdict == "rejected":
            take = record_take(root, key, _source(image), dest=None, status="rejected", note=note)
            return {**base, "status": "ok", "take": str(take), "dest": str(dest), "placed": False}
        if dest.is_file():
            retire(root, key, dest, "replaced by a new take")
        status = "accepted" if verdict == "accepted" else "placed"
        take = record_take(root, key, _source(image), dest=dest, status=status, note=note)
        released = release_after(folder, number)
        if verdict == "accepted":
            accept_panel(folder, number)
        return {
            **base,
            "status": "ok",
            "take": str(take),
            "dest": str(dest),
            "accepted": verdict == "accepted",
            "released": [item for item in released if item != number],
            "next_actions": [] if verdict == "accepted" else [f"novel_sequence op review index {number}, then op accept"],
        }
    if op == "accept":
        if step == "plate":
            _accept_plate(folder, current, number)
            return {**base, "status": "ok", "plates": load_sequence(folder).plates,
                    "next_actions": [f"novel_sequence op frame index {number}: it returns the blocking to show"]}
        if step == "blocking":
            locked = _accept_blocking(folder, current, number)
            return {**base, "status": "ok", "blocked": locked.blocked, "next_actions": [f"novel_sequence op frame index {number}"]}
        if step == "figure":
            if not figure:
                raise BrahmaError("accept step figure needs figure, the figure name")
            stage = stage_of(folder, current, number)
            stage.accept(figure)
            return {**base, "status": "ok", "placed": stage.placed(), **_compose(root, folder, slug, current, number)}
        locked = accept_panel(folder, number)
        return {**base, "status": "ok", "accepted": locked.accepted, "dest": str(panel_path(folder, number))}
    if op == "review":
        target = panel_path(folder, number)
        previous = panel_path(folder, number - 1) if number > 1 else None
        report = review_picture(
            root,
            target,
            regions=None if (target.with_suffix(".regions.json")).is_file() else SHOT_REGIONS,
            compare=previous if previous is not None and previous.is_file() else None,
            label=f"{slug}-panel-{number:02d}",
        )
        placements = current.placements(number)
        if len(placements) > 1:
            report["duplicate_pose"] = duplicate_figures(target, {item.name: item.box for item in placements})
        return {
            **base,
            "status": "ok",
            **report,
            "next_actions": [
                "Open the strip and the crops. Say what moved in the camera, the path, the sky, the poses, "
                "the side of each mark, and the head scale. If duplicate_pose lists a pair, look at those two "
                "figures: two figures never share a pose. Accept only when it follows the previous panel."
            ],
        }
    raise BrahmaError("op is create, set, status, frame, plate, stage, figure, filter, place, accept, review, or unify")


def _step_key(slug: str, current: SequenceSettings, number: int, step: str, figure: str = "") -> str:
    if step == "plate":
        if current.camera == "static":
            return f"sequence:{slug}:plate"
        return f"sequence:{slug}:plate:{current.plate_index(number):02d}"
    if step == "figure":
        return f"sequence:{slug}:{number:02d}:figure:{figure}"
    if step == "filter":
        return f"sequence:{slug}:{number:02d}:fx"
    return f"sequence:{slug}:{number:02d}"


def _step_job(base: dict, key: str, frame: SequenceFrame, aspect: str) -> dict:
    job = _frame_job(key, frame, frame.aspect_ratio or aspect)
    job["step"] = frame.step
    if frame.figure:
        job["figure"] = frame.figure
    if frame.effects:
        job["effects_moved" if frame.step != "filter" else "effects"] = list(frame.effects)
    result = {**base, "status": "needs_images", "step": frame.step, "jobs": [job]}
    if frame.warnings:
        result["warnings"] = list(frame.warnings)
    place = f"novel_sequence op place step {frame.step}" + (f" figure {frame.figure}" if frame.figure else "")
    result["next_actions"] = [
        "Generate this one job. Copy description verbatim, set aspect_ratio, and pass reference_image_paths unchanged. "
        f"Then call {place} with the same index and image set to the generated file."
    ]
    return result


def _place_refs(root: Path, current: SequenceSettings) -> dict:
    refs: dict = {}
    if current.location:
        plan = load_project(root).plan
        found = next((item for item in plan.locations if item.id == current.location), None)
        if found is None:
            raise BrahmaError(f"unknown location {current.location}")
        refs["place_plate"] = root / "refs" / "locations" / f"{found.id}.png"
        refs["place_text"] = place_text(found)
    return refs


def _figure_frame(root: Path, folder: Path, current: SequenceSettings, number: int, name: str | None) -> SequenceFrame:
    placements = current.placements(number)
    if name is None:
        stage = stage_of(folder, current, number)
        stage.open()
        chosen = stage.next(placements)
        if chosen is None:
            raise BrahmaError(f"every figure of panel {number} is placed")
        name = chosen.name
    placement = next((item for item in placements if item.name == name), None)
    if placement is None:
        raise BrahmaError(f"no figure {name} in this sequence")
    plan = load_project(root).plan
    found = next((item for item in plan.characters if item.id == placement.character), None)
    if found is None:
        raise BrahmaError(f"figure {name} has no known character; set figures[].character")
    view = current.view_of(number, name)
    who = figure_clause(found, view)
    return figure_frame(
        folder,
        number,
        name,
        who=who,
        view=figure_view(root, found.id, view, placement.pose),
        pose_sheet=pose_reference(root, found.id),
    )


def _affected(folder: Path, current: SequenceSettings, number: int) -> list[int]:
    """Panels built on the plate of panel number."""

    panels = sorted({*existing_panels(folder), *range(1, number + 1)})
    if current.camera == "static":
        return panels
    return [item for item in panels if item >= number]


def _place_plate(
    root: Path, folder: Path, slug: str, current: SequenceSettings, number: int, source: Path, verdict: str, note: str
) -> dict:
    key = _step_key(slug, current, number, "plate")
    dest = plate_path(folder, current, number)
    if verdict == "rejected":
        take = record_take(root, key, source, dest=None, status="rejected", note=note)
        return {"status": "ok", "step": "plate", "take": str(take), "dest": str(dest), "placed": False}
    replaced = dest.is_file()
    if replaced:
        retire(root, key, dest, "replaced by a new plate")
    take = record_take(root, key, source, dest=dest, status="accepted" if verdict == "accepted" else "placed", note=note)
    affected = _affected(folder, current, current.plate_index(number))
    for item in affected:
        stage_of(folder, current, item).reset()
    released = release_after(folder, affected[0]) if replaced and affected else []
    locked = load_sequence(folder)
    locked.plates = [item for item in locked.plates if item < current.plate_index(number)]
    locked.blocked = [
        item for item in locked.blocked if current.camera != "static" and item < current.plate_index(number)
    ]
    if verdict == "accepted":
        locked.plates = sorted({*locked.plates, current.plate_index(number)})
    save_sequence(folder, locked)
    return {
        "status": "ok",
        "step": "plate",
        "take": str(take),
        "dest": str(dest),
        "accepted": verdict == "accepted",
        "released": released,
        "next_actions": [
            f"novel_sequence op frame index {number}: it returns the blocking to show"
            if verdict == "accepted"
            else "novel_review the plate, then novel_sequence op accept step plate"
        ],
    }


def _accept_plate(folder: Path, current: SequenceSettings, number: int) -> None:
    if not plate_path(folder, current, number).is_file():
        raise BrahmaError("no plate to accept")
    locked = load_sequence(folder)
    locked.plates = sorted({*locked.plates, current.plate_index(number)})
    save_sequence(folder, locked)


def _place_figure(
    root: Path,
    folder: Path,
    slug: str,
    current: SequenceSettings,
    number: int,
    name: str | None,
    source: Path,
    verdict: str,
    note: str,
) -> dict:
    if not name:
        raise BrahmaError("place step figure needs figure, the figure name the job named")
    placement = next((item for item in current.placements(number) if item.name == name), None)
    if placement is None:
        raise BrahmaError(f"no figure {name} in this sequence")
    stage = stage_of(folder, current, number)
    match = previous_figure(folder, current, number, name) if number > 1 and verdict != "rejected" else None
    done = stage.put(
        placement, source, key=_step_key(slug, current, number, "figure", name), verdict=verdict, note=note, match=match
    )
    result = {"status": "ok", "step": "figure", "figure": name, **done, "base": str(stage.base), "placed_figures": stage.placed()}
    if verdict == "pending" and done.get("placed"):
        result["next_actions"] = [
            f"novel_review {stage.base.relative_to(root)}, then op accept step figure figure {name}, "
            f"or op place step figure verdict rejected to take it back"
        ]
        return result
    result.update(_compose(root, folder, slug, current, number))
    return result


def _compose(root: Path, folder: Path, slug: str, current: SequenceSettings, number: int) -> dict:
    """When every figure is on the base, the base becomes the panel, waiting for review."""

    placements = current.placements(number)
    stage = stage_of(folder, current, number)
    if not stage.complete(placements):
        remaining = [item.name for item in back_to_front(placements) if item.name not in stage.placed()]
        return {"remaining": remaining, "next_actions": [f"novel_sequence op figure index {number}"]}
    key = _step_key(slug, current, number, "panel")
    dest = panel_path(folder, number)
    if dest.is_file():
        retire(root, key, dest, "replaced by a staged panel")
    take = record_take(root, key, stage.base, dest=dest, status="placed", note="place first, then every figure")
    released = release_after(folder, number)
    return {
        "panel": str(dest),
        "panel_take": str(take),
        "released": [item for item in released if item != number],
        "next_actions": [f"novel_sequence op review index {number}, then op accept index {number}"],
    }


def _accepted_ground(current: SequenceSettings, number: int) -> float | None:
    """On a static camera, the standing figures of the other accepted panels fix the ground. A moving camera has none yet."""

    if current.camera != "static" or current.horizon is None:
        return None
    standing_on = []
    for index in current.accepted:
        if index == number:
            continue
        standing_on.extend(current.placements(index))
    return ground_ratio(standing_on, current.horizon)


def _blocking_step(folder: Path, current: SequenceSettings, number: int) -> dict:
    """The preview of where each figure stands and how tall it is, waiting for the person's yes."""

    placements = current.placements(number)
    ground = _accepted_ground(current, number)
    preview = blocking_preview(
        plate_path(folder, current, number),
        placements,
        blocking_path(folder, number),
        horizon=current.horizon,
        ground=ground,
    )
    approved = number in current.blocked
    result = {
        "status": "ok" if approved else "needs_approval",
        "step": "blocking",
        "approved": approved,
        "preview": str(preview),
        **blocking_report(placements, current.horizon, ground),
    }
    proposed = current.proposed(number)
    if proposed:
        result["proposed"] = proposed
    actions = [
        "Show the preview to the person: a stick figure where each one stands and how tall it is (alto = share of the frame), "
        "numbered back to front, feet on its spot, the dashed line is the horizon. A paper drawing is read the same way: "
        "each figure from the top of the head to the feet, as fractions of the frame drawn around them. "
        "Change boxes, poses, or the horizon with op set "
        "(settings.blocking {panel: [{figure, box, pose, view}]}, settings.horizon)."
    ]
    if current.horizon is None:
        actions.append("No horizon yet: look at the plate and set settings.horizon (0..1 from the top) so the scale can be checked.")
    if result["scale_off"]:
        where = "against the ground of the accepted panels" if result["ground_ratio"] is not None else "against the other figures in this panel"
        actions.append(f"scale_off: those figures are too big or too small for where their feet are, {where}. Say so and propose a box.")
    if not approved:
        actions.append(f"Only after the person says yes: novel_sequence op accept step blocking index {number}.")
    result["next_actions"] = actions
    return result


def _require_blocking(folder: Path, current: SequenceSettings, number: int) -> None:
    if not current.figures:
        return
    if not plate_path(folder, current, number).is_file():
        raise BrahmaError(f"no plate for panel {number} yet. The empty place comes first: op plate")
    if number not in current.blocked:
        raise BrahmaError(
            f"the blocking of panel {number} is not approved. Show it (op frame or op stage index {number}) "
            f"and wait for the person's yes: op accept step blocking index {number}."
        )


def _accept_blocking(folder: Path, current: SequenceSettings, number: int) -> SequenceSettings:
    if not current.figures:
        raise BrahmaError("this sequence has no figures to block")
    if current.plate_index(number) not in current.plates:
        raise BrahmaError(f"the plate of panel {number} is not accepted. The empty place comes first.")
    if not blocking_path(folder, number).is_file():
        raise BrahmaError(f"no blocking preview for panel {number}. Show it first: op stage index {number}.")
    locked = load_sequence(folder)
    locked.blocked = sorted({*locked.blocked, number})
    save_sequence(folder, locked)
    return locked


def _blocking_moved(folder: Path, before: SequenceSettings, after: SequenceSettings) -> tuple[list[int], list[int]]:
    """Panels whose boxes, poses, or horizon changed lose the approval of their blocking.

    A moved box or pose also clears the figures already set on that panel, and an accepted panel
    from there on is released. A new horizon alone only asks for the yes again on panels not yet accepted."""

    keys = [int(key) for key in (*before.blocking, *after.blocking)]
    last = max([1, *existing_panels(folder), *before.blocked, *before.accepted, *keys])
    moved = [index for index in range(1, last + 1) if before.placements(index) != after.placements(index)]
    unblocked = set(moved)
    if before.horizon != after.horizon:
        unblocked |= {index for index in range(1, last + 1) if index not in after.accepted}
    for index in moved:
        stage_of(folder, after, index).reset()
    for index in unblocked:
        blocking_path(folder, index).unlink(missing_ok=True)
    released: list[int] = []
    hit = [index for index in moved if index in after.accepted]
    if hit:
        released = [index for index in after.accepted if index >= min(hit)]
        after.accepted = [index for index in after.accepted if index < min(hit)]
    after.blocked = [index for index in after.blocked if index not in unblocked]
    return sorted(index for index in unblocked if index in before.blocked or index in moved), released


def _next_step(root: Path, folder: Path, slug: str, current: SequenceSettings, number: int, base: dict) -> dict:
    """The next job for panel number: plate, then the blocking the person approves, then each figure, then the filter if one was asked for."""

    if number > 1 and number - 1 not in current.accepted:
        raise BrahmaError(f"panel {number - 1} is not accepted. Accept it before building panel {number}.")
    plate = plate_path(folder, current, number)
    stage = stage_of(folder, current, number)
    rebuilding = bool(current.figures) and plate.is_file() and not stage.complete(current.placements(number))
    if number in current.accepted and not rebuilding:
        if current.effects and not fx_path(folder, number).is_file():
            frame = filter_frame(folder, number)
            return _step_job(base, _step_key(slug, current, number, "filter"), frame, current.aspect_ratio)
        return {**base, "status": "ok", "step": "done", "panel": str(panel_path(folder, number)),
                "next_actions": [f"novel_sequence op frame index {number + 1}"]}
    if current.plate_index(number) not in current.plates:
        if plate.is_file():
            return {**base, "status": "ok", "step": "plate", "plate": str(plate),
                    "next_actions": ["novel_review the plate, then novel_sequence op accept step plate"]}
        frame = plate_frame(folder, number, **_place_refs(root, current))
        return _step_job(base, _step_key(slug, current, number, "plate"), frame, current.aspect_ratio)
    if not current.figures:
        if not panel_path(folder, number).is_file():
            record_take(root, _step_key(slug, current, number, "panel"), plate, dest=panel_path(folder, number),
                        status="placed", note="no figures: the panel is the plate")
        return {**base, "status": "ok", "step": "panel", "panel": str(panel_path(folder, number)),
                "next_actions": [f"novel_sequence op review index {number}, then op accept"]}
    if number not in current.blocked:
        return {**base, **_blocking_step(folder, current, number)}
    if stage.pending():
        return {**base, "status": "ok", "step": "figure", "figure": stage.pending(),
                "next_actions": [f"review {stage.base}, then op accept step figure figure {stage.pending()}"]}
    if stage.complete(current.placements(number)) and panel_path(folder, number).is_file():
        return {**base, "status": "ok", "step": "panel", "panel": str(panel_path(folder, number)),
                "next_actions": [f"novel_sequence op review index {number}, then op accept"]}
    frame = _figure_frame(root, folder, current, number, None)
    return _step_job(base, _step_key(slug, current, number, "figure", frame.figure), frame, current.aspect_ratio)


def _stages(folder: Path, current: SequenceSettings) -> dict:
    out = {}
    for number in sorted({*existing_panels(folder), 1}):
        stage = stage_of(folder, current, number)
        out[str(number)] = {
            "plate": str(stage.plate) if stage.plate.is_file() else "",
            "plate_accepted": current.plate_index(number) in current.plates,
            "blocking_approved": number in current.blocked,
            "placed": stage.placed(),
            "pending": stage.pending(),
            "accepted": number in current.accepted,
            "fx": str(fx_path(folder, number)) if fx_path(folder, number).is_file() else "",
        }
    return out


def edit(
    project: str | Path,
    config: AppConfig,
    target: str,
    regions: list | None = None,
    change: str | None = None,
    image: str | None = None,
    note: str = "",
) -> dict:
    """Change only a region of an accepted picture. Outside the region the old pixels are kept."""

    root = _root(project)
    path = _inside(root, target)
    if image:
        done = finish_edit(root, path, _source(image), note)
        return {
            "operation": "edit",
            "status": "ok",
            "project": str(root),
            **done,
            "next_actions": ["novel_review on the target with compare set to the replaced take"],
        }
    started = start_edit(root, path, regions or [], change or "")
    look = load_settings(root).as_style()
    width, height = started["size"]
    description = (
        "Edit of an existing picture. The first reference is that picture with a flat magenta area. "
        "Draw the same picture at the same size and framing, and replace only the magenta area with: "
        f"{change.strip()}. Everything outside the magenta area stays exactly as it is. No magenta remains. "
        f"{style_clause(scene_style(look))}."
    )
    description += f" Avoid: {negative_prompt(look)}."
    return {
        "operation": "edit",
        "status": "needs_images",
        "project": str(root),
        "regions": started["regions"],
        "jobs": [
            {
                "key": f"edit:{path.name}",
                "kind": "edit",
                "description": description,
                "aspect_ratio": _aspect(width / height),
                "reference_image_paths": [str(started["mask"]), str(path)],
                "dest": str(path),
            }
        ],
        "next_actions": [
            "Generate the job, then call novel_edit again with the same target and image set to the generated file. "
            "Only the region is pasted back."
        ],
    }


def review(
    project: str | Path,
    target: str,
    regions: dict | None = None,
    compare: str | None = None,
) -> dict:
    """Named crops of a picture and, with compare, how much each region changed."""

    root = _root(project)
    path = _inside(root, target)
    other = _inside(root, compare) if compare else None
    report = review_picture(root, path, regions=regions, compare=other)
    report["takes"] = _takes_for(root, path)
    boxes = _page_figure_boxes(root, path)
    if len(boxes) > 1:
        report["duplicate_pose"] = duplicate_figures(path, boxes)
    return {
        "operation": "review",
        "status": "ok",
        "project": str(root),
        **report,
        "next_actions": ["Open every crop. Accept only what you checked in the crops, not in the thumbnail."],
    }


def _page_figure_boxes(root: Path, path: Path) -> dict:
    if path.parent != root / "panels" or not re.fullmatch(r"p\d{2}_\d{2}(-base)?", path.stem):
        return {}
    try:
        return page_stage.figure_boxes(load_project(root), path.stem.removesuffix("-base"))
    except BrahmaError:
        return {}


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
    "Generate the jobs in parallel when they do not depend on each other. Copy description verbatim, set aspect_ratio, "
    "and pass reference_image_paths unchanged. Then call novel_refs with images [{key, path, accept, note}]. "
    "accept false keeps a rejected take in the story folder with its note. A turnaround job appears once its sheet is accepted, "
    "and a pose sheet job once its turnaround is accepted."
)
_DRAW_PANELS = (
    "One job per panel, one step at a time: first the empty place (key :plate, no people, no effects), "
    "then the blocking the person approves (no image: its preview comes under blocking), "
    "then each character alone on flat green (key :figure:<id>, back to front; the tool keys it and sets it into its box), then an optional filter (:fx). "
    "Generate the jobs in parallel; they are different panels. Copy description verbatim, set aspect_ratio, "
    "and pass reference_image_paths unchanged: a figure job looks at its sheet, turnaround view, and pose sheet. "
    "A page panel never takes another page panel. Then call novel_draw with images [{key, path, accept, note}] "
    "and draw the next job it returns."
)
_APPROVE_BLOCKING = (
    "blocking lists panels whose figures wait for the person's yes. Show each preview: where each character stands "
    "and how tall it is (alto = share of the frame): stick figures, feet on the spot, numbered back to front, the dashed line is the horizon. "
    "Change it with novel_draw blocking {panel: {horizon, figures: [{character, box, pose}]}}. If there is no horizon, "
    "look at the plate and set it. If scale_off lists a character, it is too big or too small for where its feet are: "
    "say so. Only after the person says yes: novel_draw approve_blocking [panel]."
)
_DRAW_SEQUENCE = (
    "Generate this one job. Copy description verbatim, set aspect_ratio, and pass reference_image_paths unchanged. "
    "Then call novel_sequence op place with the same index and image set to the generated file."
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


def _aspect(ratio: float) -> str:
    return min(_ASPECTS, key=lambda name: abs(_ASPECTS[name] - ratio))


def _job(recipe: ImageRecipe, dest: Path, extra: list[str] | None = None) -> dict:
    paths: list[str] = []
    for item in [str(ref.path) for ref in recipe.references] + (extra or []):
        if item not in paths:
            paths.append(item)
    job = {
        "key": recipe.key,
        "kind": recipe.kind,
        "description": f"{recipe.prompt} Avoid: {recipe.negative_prompt}.",
        "aspect_ratio": _aspect(recipe.width / recipe.height),
        "reference_image_paths": paths[:MAX_REFERENCES],
        "dest": str(dest),
    }
    if len(paths) > MAX_REFERENCES:
        job["dropped_references"] = paths[MAX_REFERENCES:]
    if recipe.effects:
        job["effects_moved"] = list(recipe.effects)
    return job


def _frame_job(key: str, frame: SequenceFrame, aspect: str) -> dict:
    job = {
        "key": key,
        "kind": "sequence",
        "description": frame.description,
        "aspect_ratio": aspect,
        "reference_image_paths": [str(path) for path in frame.references],
        "dest": str(frame.dest),
    }
    if frame.dropped:
        job["dropped_references"] = [str(path) for path in frame.dropped]
    return job


def _sequence_cast(root: Path, current: SequenceSettings) -> dict:
    plan = load_project(root).plan
    marks: list[str] = []
    views: list[Path] = []
    poses: list[Path] = []
    for character_id in current.characters:
        found = next((item for item in plan.characters if item.id == character_id), None)
        if found is None or not found.visual:
            continue
        said = marks_clause(found, current.view)
        if said:
            marks.append(f"{found.name}: {said}")
        crop = view_reference(root, found.id, current.view)
        if crop is not None:
            views.append(crop)
        posed = pose_reference(root, found.id)
        if posed is not None:
            poses.append(posed)
    return {"marks": marks, "views": views, "poses": poses}


def _install(root: Path, slots: list[tuple[ImageRecipe, Path]], images: list[dict] | None) -> dict:
    """Every file becomes a take. Accepted ones are copied onto dest; rejected ones stay in takes/ with their note."""

    if not images:
        return {"installed": [], "rejected": [], "takes": []}
    by_key = {recipe.key: (recipe, dest) for recipe, dest in slots}
    installed: list[str] = []
    rejected: list[str] = []
    takes: list[str] = []
    for item in images:
        key = str(item.get("key", ""))
        raw = item.get("path")
        if not key or not raw:
            raise BrahmaError("each image needs key and path")
        if key not in by_key:
            raise BrahmaError(f"unknown image key {key}")
        recipe, dest = by_key[key]
        accept = item.get("accept", True) is not False
        note = str(item.get("note") or "")
        if accept and dest.is_file():
            retire(root, key, dest, "replaced by a new take")
        take = record_take(
            root,
            key,
            _source(str(raw)),
            dest=dest,
            status="accepted" if accept else "rejected",
            note=note,
            recipe=recipe.as_dict(),
        )
        takes.append(str(take))
        (installed if accept else rejected).append(key)
    return {"installed": installed, "rejected": rejected, "takes": takes}


def _drop(root: Path, slots: list[tuple[ImageRecipe, Path]]) -> None:
    """Free dest for a new picture. The old one stays in takes/ as replaced."""

    project = load_project(root)
    changed = False
    for recipe, dest in slots:
        if dest.is_file():
            project.bump(recipe.key)
            retire(root, recipe.key, dest, "asked to draw again")
            changed = True
    if changed:
        save_project(root, project)


def _source(raw: str) -> Path:
    source = Path(str(raw))
    if not source.is_file():
        raise BrahmaError(f"image not found: {source}")
    return source


def _inside(root: Path, raw: str) -> Path:
    """A picture inside the story. Relative paths are read from the story folder."""

    path = Path(str(raw))
    if not path.is_absolute():
        path = root / path
    if under_cursor_assets(path):
        raise BrahmaError(f"images stay in the story folder, not {path}")
    if root.resolve() not in path.resolve().parents:
        raise BrahmaError(f"{path} is not inside the story {root}")
    if not path.is_file():
        raise BrahmaError(f"no picture at {path}")
    return path


def _takes_for(root: Path, path: Path) -> list[dict]:
    """Takes of a sheet or page panel, found through the recipe written next to it."""

    recipe_file = path.with_suffix(".json")
    if not recipe_file.is_file():
        return []
    try:
        key = json.loads(recipe_file.read_text(encoding="utf-8")).get("key")
    except json.JSONDecodeError:
        return []
    return list_takes(root, key) if key else []


def _slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", str(name).strip().lower()).strip("-")
    if not text:
        raise BrahmaError("a sequence needs a name")
    return text


def _index(index: int | None) -> int:
    if index is None:
        raise BrahmaError("this op needs index, the panel number starting at 1")
    number = int(index)
    if number < 1:
        raise BrahmaError("panel numbers start at 1")
    return number


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
