"""Plan, lock references, draw panels, export an editable page.

Text edits reassemble the page and do not touch the GPU. A new face means a new
sheet, then the panels that use it. A page panel never looks at another page panel;
sequences, which are one continuous shot, live in brahma.sequence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw

from brahma.consistency import (
    CHARACTER_WEIGHT,
    ImageRecipe,
    Reference,
    SamplerLock,
    character_sheet_prompt,
    location_plate_prompt,
    negative_prompt,
    panel_prompt,
    panel_references,
    pose_sheet_prompt,
    scene_effects,
    stable_seed,
    style_anchor_prompt,
    text_sampler,
    turnaround_prompt,
)
from brahma.errors import BrahmaError
from brahma.figures import view_crop
from brahma.images.base import ImageGenerator
from brahma.layout import PAGE_HEIGHT, PAGE_WIDTH, PAPER, Frame, art_box, frames_for, generation_size
from brahma.lettering import render_lettering
from brahma.models import VIEWS, DialogueLine, PageSpec, Project, StoryPlan, panel_key
from brahma.openraster import OraLayer, composite, write_openraster
from brahma.intake import import_images
from brahma.script import ScriptModel
from brahma.settings import load_settings, look_from_style, overlay_look, save_settings, settings_from_look, style_of
from brahma.store import allocate_project, load_project, save_project
from brahma.takes import retire

SHEET = (1024, 1024)
PLATE = (1216, 832)
TURNAROUND = (1536, 768)
POSES = (1536, 1152)


def plan_story(
    story: str,
    script: ScriptModel,
    sampler: SamplerLock,
    workspace: Path,
    setup: dict | None = None,
    images: list[dict] | None = None,
    read_image=None,
) -> tuple[Project, Path]:
    text = story.strip()
    if not text:
        raise BrahmaError("the story is empty")
    plan = script.plan(text, setup)
    project_id, root = allocate_project(workspace, plan.title)
    imported = import_images(root, images, read_image=read_image)
    data = look_from_style(plan.style)
    references = []
    for ref, reading in imported:
        if reading:
            data = overlay_look(data, reading)
        references.append(ref)
    data = overlay_look(data, setup)
    settings = settings_from_look(data, references)
    plan.style = settings.as_style()
    project = Project(id=project_id, story=text, plan=plan, workflow=sampler.workflow)
    save_project(root, project)
    save_settings(root, settings)
    return project, root


def generate_references(
    root: Path,
    images: ImageGenerator,
    sampler: SamplerLock,
    *,
    force: bool = False,
    log=print,
) -> Project:
    project = load_project(root)
    jobs = [
        _style_recipe(root, project, sampler),
        *_character_recipes(root, project, sampler),
        *_location_recipes(root, project, sampler),
    ]
    for recipe, path in jobs:
        if path.is_file() and not force:
            log(f"keep {path.relative_to(root)}")
            continue
        if path.is_file() and force:
            project.bump(recipe.key)
            recipe = _with_revision(project, recipe)
            retire(root, recipe.key, path, "drawn again with force")
        _render(images, recipe, path, log)
    for recipe, path in [*_turnaround_recipes(root, project, sampler), *_pose_recipes(root, project, sampler)]:
        if path.is_file() and not force:
            log(f"keep {path.relative_to(root)}")
            continue
        if path.is_file():
            project.bump(recipe.key)
            recipe = _with_revision(project, recipe)
            retire(root, recipe.key, path, "drawn again with force")
        _render(images, recipe, path, log)
    save_project(root, project)
    return project


def draw_panels(
    root: Path,
    images: ImageGenerator,
    sampler: SamplerLock,
    *,
    panel: str | None = None,
    force: bool = False,
    log=print,
) -> Project:
    project = load_project(root)
    _require_references(root, project.plan)
    if panel:
        try:
            project.plan.panel(panel)
        except KeyError:
            known = [
                panel_key(page.number, index)
                for page in project.plan.pages
                for index, _panel in enumerate(page.panels, start=1)
            ]
            raise BrahmaError(f"unknown panel {panel}. Known panels: {', '.join(known)}") from None
    sheets, plates = _reference_paths(root, project.plan)
    project.workflow = sampler.workflow
    for page in project.plan.pages:
        page_frames = frames_for(len(page.panels))
        for index, (spec, frame) in enumerate(zip(page.panels, page_frames), start=1):
            key = panel_key(page.number, index)
            if panel and key != panel:
                continue
            width, height = generation_size(frame)
            recipe = _panel_recipe(root, project, spec, key, width, height, sheets, plates, sampler)
            path = root / "panels" / f"{key}.png"
            if path.is_file() and not force and panel is None:
                log(f"keep {path.relative_to(root)}")
                continue
            if path.is_file():
                project.bump(key)
                recipe = _with_revision(project, recipe)
                retire(root, key, path, "panel drawn again")
            _render(images, recipe, path, log)
    save_project(root, project)
    return project


def assemble_pages(root: Path, *, log=print) -> list[Path]:
    project = load_project(root)
    written: list[Path] = []
    for page in project.plan.pages:
        layers = _page_layers(root, project, page)
        merged = composite(PAGE_WIDTH, PAGE_HEIGHT, layers)
        ora = root / "pages" / f"page-{page.number:02d}.ora"
        png = root / "pages" / f"page-{page.number:02d}.png"
        write_openraster(ora, PAGE_WIDTH, PAGE_HEIGHT, layers)
        png.parent.mkdir(parents=True, exist_ok=True)
        merged.convert("RGB").save(png, format="PNG")
        log(f"page {ora.relative_to(root)}")
        written.append(ora)
    return written


def make_story(
    story: str,
    script: ScriptModel,
    images: ImageGenerator,
    sampler: SamplerLock,
    workspace: Path,
    *,
    log=print,
) -> Path:
    _project, root = plan_story(story, script, sampler, workspace)
    log(f"planned {root}")
    generate_references(root, images, sampler, log=log)
    draw_panels(root, images, sampler, log=log)
    assemble_pages(root, log=log)
    return root


def _render(images: ImageGenerator, recipe: ImageRecipe, path: Path, log) -> None:
    images.generate(recipe, path)
    path.with_suffix(".json").write_text(json.dumps(recipe.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"drew {path.name} seed={recipe.seed} refs={','.join(item.role for item in recipe.references) or 'none'}")


def _with_revision(project: Project, recipe: ImageRecipe) -> ImageRecipe:
    return ImageRecipe(
        key=recipe.key,
        kind=recipe.kind,
        prompt=recipe.prompt,
        negative_prompt=recipe.negative_prompt,
        width=recipe.width,
        height=recipe.height,
        seed=_seed(project, recipe.key),
        references=recipe.references,
        sampler=recipe.sampler,
        effects=recipe.effects,
    )


def _seed(project: Project, key: str) -> int:
    return stable_seed(project.id, key, str(project.revision(key)))


def _place_look(root: Path, project: Project):
    """The look for a picture with nobody in it.

    When the notes are the reading of one character's drawing, they describe that
    character, so they stay out of the style sample and the place plates.
    """

    look = style_of(root, project)
    settings_path = root / "settings.json"
    if not settings_path.is_file():
        return look
    if any(ref.role == "sketch" and ref.subject for ref in load_settings(root).references):
        return look.model_copy(update={"notes": ""})
    return look


def _style_recipe(root: Path, project: Project, sampler: SamplerLock) -> tuple[ImageRecipe, Path]:
    key = "style"
    place = project.plan.locations[0] if project.plan.locations else None
    look = _place_look(root, project)
    recipe = ImageRecipe(
        key=key,
        kind="style",
        prompt=style_anchor_prompt(look, place),
        negative_prompt=negative_prompt(look),
        width=SHEET[0],
        height=SHEET[1],
        seed=_seed(project, key),
        references=(),
        sampler=text_sampler(sampler),
        effects=scene_effects(place.appearance, place.lighting) if place else (),
    )
    return recipe, root / "refs" / "style.png"


def _character_recipes(root: Path, project: Project, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    recipes = []
    for character in project.plan.characters:
        if not character.visual:
            continue
        key = f"character:{character.id}"
        recipe = ImageRecipe(
            key=key,
            kind="character",
            prompt=character_sheet_prompt(style_of(root, project), character),
            negative_prompt=negative_prompt(style_of(root, project), sheet=True),
            width=SHEET[0],
            height=SHEET[1],
            seed=_seed(project, key),
            references=(),
            sampler=text_sampler(sampler),
        )
        recipes.append((recipe, root / "refs" / "characters" / f"{character.id}.png"))
    return recipes


def _turnaround_recipes(root: Path, project: Project, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    """Four views from the accepted sheet. The sheet is the reference, so it must exist first."""

    recipes = []
    look = style_of(root, project)
    for character in project.plan.characters:
        if not character.visual:
            continue
        sheet = character_sheet_path(root, character.id)
        if not sheet.is_file():
            continue
        key = f"turnaround:{character.id}"
        recipe = ImageRecipe(
            key=key,
            kind="turnaround",
            prompt=turnaround_prompt(look, character),
            negative_prompt=negative_prompt(look, sheet=True),
            width=TURNAROUND[0],
            height=TURNAROUND[1],
            seed=_seed(project, key),
            references=(Reference("character", character.id, sheet, CHARACTER_WEIGHT),),
            sampler=sampler,
        )
        recipes.append((recipe, turnaround_path(root, character.id)))
    return recipes


def _pose_recipes(root: Path, project: Project, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    """Face close-ups and action poses, drawn from the accepted sheet and turnaround."""

    recipes = []
    look = style_of(root, project)
    for character in project.plan.characters:
        if not character.visual:
            continue
        sheet = character_sheet_path(root, character.id)
        strip = turnaround_path(root, character.id)
        if not (sheet.is_file() and strip.is_file()):
            continue
        key = f"poses:{character.id}"
        recipe = ImageRecipe(
            key=key,
            kind="poses",
            prompt=pose_sheet_prompt(look, character),
            negative_prompt=negative_prompt(look, sheet=True),
            width=POSES[0],
            height=POSES[1],
            seed=_seed(project, key),
            references=(
                Reference("character", character.id, sheet, CHARACTER_WEIGHT),
                Reference("view", character.id, strip, CHARACTER_WEIGHT),
            ),
            sampler=sampler,
        )
        recipes.append((recipe, pose_sheet_path(root, character.id)))
    return recipes


def character_sheet_path(root: Path, character_id: str) -> Path:
    return root / "refs" / "characters" / f"{character_id}.png"


def turnaround_path(root: Path, character_id: str) -> Path:
    return root / "refs" / "characters" / f"{character_id}-360.png"


def pose_sheet_path(root: Path, character_id: str) -> Path:
    return root / "refs" / "characters" / f"{character_id}-poses.png"


def pose_reference(root: Path, character_id: str) -> Path | None:
    path = pose_sheet_path(root, character_id)
    return path if path.is_file() else None


def turnaround_views(root: Path, character_id: str) -> tuple[str, ...]:
    """Column order of a turnaround. A strip drawn outside the job can name its own order."""

    sidecar = turnaround_path(root, character_id).with_suffix(".views.json")
    if sidecar.is_file():
        views = json.loads(sidecar.read_text(encoding="utf-8")).get("views") or []
        if views and all(view in VIEWS for view in views):
            return tuple(views)
    return VIEWS


_FROM_BELOW = re.compile(r"desde el suelo|desde abajo|from below|underside|belly|vientre", re.I)


def below_reference(root: Path, character_id: str) -> Path | None:
    """The drone as seen from the ground, looking up. None until that sheet exists."""

    path = root / "refs" / "characters" / f"{character_id}-below.png"
    return path if path.is_file() else None


def figure_view(root: Path, character_id: str, view: str, pose: str) -> Path | None:
    """The side the camera sees. A pose from below uses the underside sheet when it exists."""

    if _FROM_BELOW.search(pose or ""):
        below = below_reference(root, character_id)
        if below is not None:
            return below
    return view_reference(root, character_id, view)


def view_reference(root: Path, character_id: str, view: str) -> Path | None:
    """The turnaround column for this view, cut once and cut again when the strip changes."""

    strip = turnaround_path(root, character_id)
    if not strip.is_file():
        return None
    order = turnaround_views(root, character_id)
    if view not in order:
        view = "front" if "front" in order else order[0]
    crop = root / "refs" / "characters" / "views" / f"{character_id}-{view}.png"
    if not crop.is_file() or crop.stat().st_mtime < strip.stat().st_mtime:
        view_crop(strip, view, crop, order)
    return crop


def _location_recipes(root: Path, project: Project, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    recipes = []
    look = _place_look(root, project)
    for location in project.plan.locations:
        key = f"location:{location.id}"
        recipe = ImageRecipe(
            key=key,
            kind="location",
            prompt=location_plate_prompt(look, location),
            negative_prompt=negative_prompt(look),
            width=PLATE[0],
            height=PLATE[1],
            seed=_seed(project, key),
            references=(),
            sampler=text_sampler(sampler),
            effects=scene_effects(location.appearance, location.lighting),
        )
        recipes.append((recipe, root / "refs" / "locations" / f"{location.id}.png"))
    return recipes


def _panel_recipe(
    root: Path,
    project: Project,
    panel,
    key: str,
    width: int,
    height: int,
    sheets: dict[str, Path],
    plates: dict[str, Path],
    sampler: SamplerLock,
) -> ImageRecipe:
    characters = [project.plan.character(character_id) for character_id in panel.character_ids]
    location = project.plan.location(panel.location_id)
    look = style_of(root, project)
    views = {}
    poses = {}
    for character_id in panel.character_ids:
        if character_id in sheets:
            found = view_reference(root, character_id, panel.view())
            if found is not None:
                views[character_id] = found
            posed = pose_reference(root, character_id)
            if posed is not None:
                poses[character_id] = posed
    return ImageRecipe(
        key=key,
        kind="panel",
        prompt=panel_prompt(look, location, characters, panel),
        negative_prompt=negative_prompt(look),
        width=width,
        height=height,
        seed=_seed(project, key),
        references=panel_references(panel, sheets, plates, views, poses),
        sampler=sampler,
        effects=scene_effects(location.appearance, location.lighting, panel.action),
    )


def _reference_paths(root: Path, plan: StoryPlan) -> tuple[dict[str, Path], dict[str, Path]]:
    sheets = {character.id: character_sheet_path(root, character.id) for character in plan.characters if character.visual}
    plates = {location.id: root / "refs" / "locations" / f"{location.id}.png" for location in plan.locations}
    return sheets, plates


def _require_references(root: Path, plan: StoryPlan) -> None:
    missing = []
    if not (root / "refs" / "style.png").is_file():
        missing.append("style")
    sheets, plates = _reference_paths(root, plan)
    missing += [f"character {key}" for key, path in sheets.items() if not path.is_file()]
    missing += [f"location {key}" for key, path in plates.items() if not path.is_file()]
    if missing:
        raise BrahmaError("missing reference images (" + ", ".join(missing) + "). Run `brahma refs` first.")


def _page_layers(root: Path, project: Project, page: PageSpec) -> list[OraLayer]:
    page_frames = frames_for(len(page.panels))
    layers: list[OraLayer] = []
    for index, (panel, frame) in enumerate(zip(page.panels, page_frames), start=1):
        key = panel_key(page.number, index)
        source = root / "panels" / f"{key}.png"
        if not source.is_file():
            raise BrahmaError(f"missing panel art {key}. Run `brahma draw` first.")
        box = art_box(frame)
        art = Image.open(source).convert("RGBA").resize((box.width, box.height), Image.Resampling.LANCZOS)
        lines = [_named(project, line) for line in panel.dialogue]
        lettering = render_lettering(frame.width, frame.height, lines)
        group = f"panel-{index:02d}"
        layers.append(OraLayer("lettering", lettering, frame.x, frame.y, group))
        fx = root / "panels" / f"{key}-fx.png"
        if fx.is_file():
            effect = Image.open(fx).convert("RGBA").resize((box.width, box.height), Image.Resampling.LANCZOS)
            layers.append(OraLayer("filter", effect, box.x, box.y, group))
        layers.append(OraLayer("art", art, box.x, box.y, group))
    layers.append(OraLayer("frames", _frames(page_frames), 0, 0))
    layers.append(OraLayer("paper", Image.new("RGBA", (PAGE_WIDTH, PAGE_HEIGHT), PAPER), 0, 0))
    return layers


def _named(project: Project, line: DialogueLine) -> DialogueLine:
    if line.speaker == "narrator":
        return line
    return DialogueLine(speaker=project.plan.character(line.speaker).name, text=line.text)


def reference_slots(root: Path, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    project = load_project(root)
    return [
        _style_recipe(root, project, sampler),
        *_character_recipes(root, project, sampler),
        *_location_recipes(root, project, sampler),
        *_turnaround_recipes(root, project, sampler),
        *_pose_recipes(root, project, sampler),
    ]


def panel_slots(root: Path, sampler: SamplerLock, panel: str | None = None) -> list[tuple[ImageRecipe, Path]]:
    project = load_project(root)
    if panel:
        try:
            project.plan.panel(panel)
        except KeyError:
            known = [
                panel_key(page.number, index)
                for page in project.plan.pages
                for index, _spec in enumerate(page.panels, start=1)
            ]
            raise BrahmaError(f"unknown panel {panel}. Known panels: {', '.join(known)}") from None
    _require_references(root, project.plan)
    sheets, plates = _reference_paths(root, project.plan)
    slots: list[tuple[ImageRecipe, Path]] = []
    for page in project.plan.pages:
        page_frames = frames_for(len(page.panels))
        for index, (spec, frame) in enumerate(zip(page.panels, page_frames), start=1):
            key = panel_key(page.number, index)
            if panel and key != panel:
                continue
            width, height = generation_size(frame)
            recipe = _panel_recipe(root, project, spec, key, width, height, sheets, plates, sampler)
            slots.append((recipe, root / "panels" / f"{key}.png"))
    return slots


def _frames(page_frames: list[Frame]) -> Image.Image:
    image = Image.new("RGBA", (PAGE_WIDTH, PAGE_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for frame in page_frames:
        draw.rectangle(
            [frame.x, frame.y, frame.x + frame.width - 1, frame.y + frame.height - 1],
            outline=(25, 25, 25, 255),
            width=8,
        )
    return image
