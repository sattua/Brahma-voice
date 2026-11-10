"""Plan, lock references, draw panels, export an editable page.

Text edits reassemble the page and do not touch the GPU. A new face means a new
sheet, then the panels that use it. The previous panel is never an input.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from brahma.consistency import (
    ImageRecipe,
    SamplerLock,
    character_sheet_prompt,
    location_plate_prompt,
    negative_prompt,
    panel_prompt,
    panel_references,
    stable_seed,
    style_anchor_prompt,
    text_sampler,
)
from brahma.errors import BrahmaError
from brahma.images.base import ImageGenerator
from brahma.layout import PAGE_HEIGHT, PAGE_WIDTH, PAPER, Frame, art_box, frames_for, generation_size
from brahma.lettering import render_lettering
from brahma.models import DialogueLine, PageSpec, Project, StoryPlan, panel_key
from brahma.openraster import OraLayer, composite, write_openraster
from brahma.intake import import_images
from brahma.script import ScriptModel
from brahma.settings import look_from_style, overlay_look, save_settings, settings_from_look, style_of
from brahma.store import allocate_project, load_project, save_project

SHEET = (1024, 1024)
PLATE = (1216, 832)


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
    )


def _seed(project: Project, key: str) -> int:
    return stable_seed(project.id, key, str(project.revision(key)))


def _style_recipe(root: Path, project: Project, sampler: SamplerLock) -> tuple[ImageRecipe, Path]:
    key = "style"
    recipe = ImageRecipe(
        key=key,
        kind="style",
        prompt=style_anchor_prompt(style_of(root, project)),
        negative_prompt=negative_prompt(style_of(root, project)),
        width=SHEET[0],
        height=SHEET[1],
        seed=_seed(project, key),
        references=(),
        sampler=text_sampler(sampler),
    )
    return recipe, root / "refs" / "style.png"


def _character_recipes(root: Path, project: Project, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    recipes = []
    for character in project.plan.characters:
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


def _location_recipes(root: Path, project: Project, sampler: SamplerLock) -> list[tuple[ImageRecipe, Path]]:
    recipes = []
    for location in project.plan.locations:
        key = f"location:{location.id}"
        recipe = ImageRecipe(
            key=key,
            kind="location",
            prompt=location_plate_prompt(style_of(root, project), location),
            negative_prompt=negative_prompt(style_of(root, project)),
            width=PLATE[0],
            height=PLATE[1],
            seed=_seed(project, key),
            references=(),
            sampler=text_sampler(sampler),
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
    return ImageRecipe(
        key=key,
        kind="panel",
        prompt=panel_prompt(look, location, characters, panel),
        negative_prompt=negative_prompt(look),
        width=width,
        height=height,
        seed=_seed(project, key),
        references=panel_references(panel, sheets, plates),
        sampler=sampler,
    )


def _reference_paths(root: Path, plan: StoryPlan) -> tuple[dict[str, Path], dict[str, Path]]:
    sheets = {character.id: root / "refs" / "characters" / f"{character.id}.png" for character in plan.characters}
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
