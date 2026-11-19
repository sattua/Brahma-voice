"""Page panels, staged: the empty place, then the blocking the person approves, then each
character in its box, then an optional filter.

Keys: pXX_YY:plate, pXX_YY:figure:<character>, pXX_YY:fx. Files sit next to the panel:
panels/pXX_YY-plate.png, -base.png, -blocking.png, -fx.png, and the panel itself,
panels/pXX_YY.png, once every character is on it. A page panel never looks at another
page panel: its figures look at their sheet, turnaround view, and pose sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from brahma.consistency import (
    figure_clause,
    negative_prompt,
    place_text,
    scene_effects,
    scene_style,
    style_clause,
)
from brahma.effects import without_effects
from brahma.errors import BrahmaError
from brahma.layout import frames_for, generation_size
from brahma.models import PanelSpec, Project, panel_key
from brahma.pipeline import character_sheet_path, pose_reference, view_reference
from brahma.settings import style_of
from brahma.stage import (
    Placement,
    Stage,
    blocking_preview,
    blocking_report,
    default_boxes,
    distinct_poses,
    figure_description,
    filter_description,
    plate_description,
)
from brahma.takes import record_take, retire

MAX_REFERENCES = 4


@dataclass(frozen=True)
class PagePanel:
    key: str
    spec: PanelSpec
    width: int
    height: int


def page_panels(project: Project, only: str | None = None) -> list[PagePanel]:
    out = []
    for page in project.plan.pages:
        for index, (spec, frame) in enumerate(zip(page.panels, frames_for(len(page.panels))), start=1):
            key = panel_key(page.number, index)
            if only and key != only:
                continue
            width, height = generation_size(frame)
            out.append(PagePanel(key, spec, width, height))
    if only and not out:
        raise BrahmaError(f"unknown panel {only}")
    return out


def final_path(root: Path, key: str) -> Path:
    return root / "panels" / f"{key}.png"


def plate_file(root: Path, key: str) -> Path:
    return root / "panels" / f"{key}-plate.png"


def fx_file(root: Path, key: str) -> Path:
    return root / "panels" / f"{key}-fx.png"


def stage_for(root: Path, key: str) -> Stage:
    return Stage(root, plate_file(root, key), root / "panels" / f"{key}-base.png", final_path(root, key))


def placements(project: Project, spec: PanelSpec) -> list[Placement]:
    """Each drawn character of the panel, with its box and its own pose."""

    shown = [cid for cid in spec.character_ids if project.plan.character(cid).visual]
    given = {item.character: item for item in spec.figures}
    boxes = default_boxes(len(shown), spec.shot)
    out = []
    for number, cid in enumerate(shown):
        character = project.plan.character(cid)
        chosen = given.get(cid)
        box = tuple(chosen.box) if chosen and chosen.box else boxes[number]
        pose = chosen.pose if chosen and chosen.pose.strip() else f"what {character.name} does in this moment: {spec.action}"
        out.append(Placement(cid, cid, pose, box))
    distinct_poses(out)
    return out


def _look(root: Path, project: Project) -> list[str]:
    look = style_of(root, project)
    return [style_clause(scene_style(look)), f"Avoid: {negative_prompt(look)}"]


def _aspect(width: int, height: int) -> str:
    ratios = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9, "9:16": 9 / 16}
    return min(ratios, key=lambda name: abs(ratios[name] - width / height))


def plate_job(root: Path, project: Project, panel: PagePanel) -> dict:
    location = project.plan.location(panel.spec.location_id)
    plate = root / "refs" / "locations" / f"{location.id}.png"
    references = [plate] if plate.is_file() else []
    description = plate_description(
        _look(root, project),
        place=place_text(location),
        shot=f"{panel.spec.shot} shot, {panel.spec.angle} camera",
        from_reference=(
            "The reference is this place: keep its forms and materials; the camera follows the shot."
            if plate.is_file()
            else ""
        ),
    )
    job = {
        "key": f"{panel.key}:plate",
        "kind": "plate",
        "description": description,
        "aspect_ratio": _aspect(panel.width, panel.height),
        "reference_image_paths": [str(path) for path in references[:MAX_REFERENCES]],
        "dest": str(plate_file(root, panel.key)),
    }
    moved = scene_effects(location.appearance, location.lighting, panel.spec.action)
    if moved:
        job["effects_moved"] = list(moved)
    return job


def figure_job(root: Path, project: Project, panel: PagePanel) -> dict | None:
    stage = stage_for(root, panel.key)
    chosen = stage.next(placements(project, panel.spec))
    if chosen is None:
        return None
    character = project.plan.character(chosen.character)
    view = panel.spec.view()
    stage.request(chosen)
    location = project.plan.location(panel.spec.location_id)
    description = figure_description(
        _look(root, project),
        who=figure_clause(character, view),
        pose=chosen.pose,
        light=without_effects(location.lighting) or "the light of the place",
        references=(
            "The references show this same character (sheet, turnaround view, pose sheet): take identity, "
            "outfit, gear, and marks from them, not their white background, their layout, or their number of figures."
        ),
    )
    paths = [character_sheet_path(root, character.id), view_reference(root, character.id, view), pose_reference(root, character.id)]
    kept = [str(path) for path in paths if path is not None and Path(path).is_file()]
    return {
        "key": f"{panel.key}:figure:{chosen.name}",
        "kind": "figure",
        "figure": chosen.name,
        "description": description,
        "aspect_ratio": stage.aspect() or _aspect(panel.width, panel.height),
        "reference_image_paths": kept[:MAX_REFERENCES],
        "dest": str(stage.base),
    }


def fx_job(root: Path, project: Project, panel: PagePanel, effects: list[str]) -> dict:
    final = final_path(root, panel.key)
    if not final.is_file():
        raise BrahmaError(f"panel {panel.key} is not finished. The filter goes over a finished panel.")
    return {
        "key": f"{panel.key}:fx",
        "kind": "filter",
        "description": filter_description(effects, _look(root, project)),
        "aspect_ratio": _aspect(panel.width, panel.height),
        "reference_image_paths": [str(final)],
        "dest": str(fx_file(root, panel.key)),
        "effects": list(effects),
    }


def proposed(project: Project, spec: PanelSpec) -> list[str]:
    """Characters with no box of their own: the preview shows them in a box the tools proposed."""

    given = {item.character: item for item in spec.figures}
    return [item.name for item in placements(project, spec) if not (given.get(item.name) and given[item.name].box)]


def blocking_step(root: Path, project: Project, panel: PagePanel) -> dict:
    """Where each character stands and how tall it is, over the plate, waiting for the person's yes."""

    shown = placements(project, panel.spec)
    step = {
        "key": panel.key,
        "step": "blocking",
        "approved": panel.spec.blocked,
        "preview": str(preview(root, project, panel.key)),
        **blocking_report(shown, panel.spec.horizon),
    }
    unboxed = proposed(project, panel.spec)
    if unboxed:
        step["proposed"] = unboxed
    return step


def next_job(root: Path, project: Project, panel: PagePanel) -> dict | None:
    """Plate, then the blocking (a step with no image), then one figure at a time. None when the panel is finished."""

    if final_path(root, panel.key).is_file():
        return None
    if not plate_file(root, panel.key).is_file():
        return plate_job(root, project, panel)
    if not placements(project, panel.spec):
        record_take(root, panel.key, plate_file(root, panel.key), dest=final_path(root, panel.key),
                    status="accepted", note="no figures: the panel is the plate")
        return None
    if not panel.spec.blocked:
        return blocking_step(root, project, panel)
    return figure_job(root, project, panel)


def install(root: Path, project: Project, item: dict) -> tuple[str, bool, str]:
    """One generated file for a staged key. Returns key, accepted, and the take path."""

    key = str(item.get("key", ""))
    raw = item.get("path")
    if not key or not raw:
        raise BrahmaError("each image needs key and path")
    source = Path(str(raw))
    if not source.is_file():
        raise BrahmaError(f"image not found: {source}")
    accept = item.get("accept", True) is not False
    note = str(item.get("note") or "")
    panel_id, _, step = key.partition(":")
    panel = page_panels(project, panel_id)[0]
    if step == "plate":
        dest = plate_file(root, panel.key)
        if accept:
            retire(root, key, dest, "replaced by a new plate")
            retire(root, panel.key, final_path(root, panel.key), "the plate changed")
            stage_for(root, panel.key).reset()
            panel.spec.blocked = False
        take = record_take(root, key, source, dest=dest if accept else None, status="accepted" if accept else "rejected", note=note)
        return key, accept, str(take)
    if step.startswith("figure:"):
        if accept and not panel.spec.blocked:
            raise BrahmaError(
                f"the blocking of {panel.key} is not approved. Show {panel.key}-blocking.png and wait for the "
                f"person's yes: novel_draw approve_blocking [{panel.key}]."
            )
        name = step.split(":", 1)[1]
        chosen = next((item for item in placements(project, panel.spec) if item.name == name), None)
        if chosen is None:
            raise BrahmaError(f"no figure {name} in {panel.key}")
        stage = stage_for(root, panel.key)
        done = stage.put(chosen, source, key=key, verdict="accepted" if accept else "rejected", note=note)
        if accept and stage.complete(placements(project, panel.spec)):
            retire(root, panel.key, final_path(root, panel.key), "replaced by a staged panel")
            record_take(root, panel.key, stage.base, dest=final_path(root, panel.key), status="accepted",
                        note="place first, then every figure")
        return key, accept, done["take"]
    if step == "fx":
        dest = fx_file(root, panel.key)
        if accept:
            retire(root, key, dest, "replaced by a new filter")
        take = record_take(root, key, source, dest=dest if accept else None, status="accepted" if accept else "rejected", note=note)
        return key, accept, str(take)
    raise BrahmaError(f"page panels are staged: use {panel_id}:plate, {panel_id}:figure:<character>, or {panel_id}:fx")


def restart(root: Path, key: str) -> None:
    """Draw a page panel again from the empty place. Old files stay in takes/."""

    retire(root, key, final_path(root, key), "asked to draw again")
    retire(root, f"{key}:plate", plate_file(root, key), "asked to draw again")
    stage_for(root, key).reset()


def figure_boxes(project: Project, key: str) -> dict[str, tuple[float, float, float, float]]:
    panel = page_panels(project, key)[0]
    return {item.name: item.box for item in placements(project, panel.spec)}


def preview(root: Path, project: Project, key: str) -> Path:
    panel = page_panels(project, key)[0]
    plate = plate_file(root, key)
    if not plate.is_file():
        raise BrahmaError(f"no plate for {key} yet")
    return blocking_preview(
        plate, placements(project, panel.spec), blocking_file(root, key), horizon=panel.spec.horizon
    )


def blocking_file(root: Path, key: str) -> Path:
    return root / "panels" / f"{key}-blocking.png"
