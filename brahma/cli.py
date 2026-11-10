"""Command line for planning a story, locking its references, and exporting pages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brahma import __version__
from brahma.config import image_model, load_config, sampler_lock, script_model
from brahma.errors import BrahmaError
from brahma.pipeline import assemble_pages, draw_panels, generate_references, make_story, plan_story


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brahma", description="Tell a story. Get a graphic novel whose panels stay consistent.")
    parser.add_argument("--config", help="path to brahma.config.json")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    health = commands.add_parser("health", help="check the script model and the image backend")
    health.set_defaults(func=_health)

    plan = commands.add_parser("plan", help="break a story into pages, characters and places")
    _add_story_args(plan)
    plan.set_defaults(func=_plan)

    refs = commands.add_parser("refs", help="draw the style sample, character sheets and location plates")
    refs.add_argument("project", type=Path, help="story directory")
    refs.add_argument("--force", action="store_true", help="draw the references again")
    refs.set_defaults(func=_refs)

    draw = commands.add_parser("draw", help="draw panels from the locked references")
    draw.add_argument("project", type=Path)
    draw.add_argument("--panel", help="redraw one panel, for example p01_02")
    draw.add_argument("--force", action="store_true", help="redraw every selected panel")
    draw.set_defaults(func=_draw)

    assemble = commands.add_parser("assemble", help="build layered OpenRaster pages from the current art and dialogue")
    assemble.add_argument("project", type=Path)
    assemble.set_defaults(func=_assemble)

    make = commands.add_parser("make", help="plan, lock references, draw and export")
    _add_story_args(make)
    make.set_defaults(func=_make)

    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        return args.func(args, config)
    except BrahmaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _add_story_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("story", nargs="?", help="the story in the author's words")
    parser.add_argument("--file", type=Path, help="read the story from a text file")


def _story_text(args) -> str:
    if args.file and args.story:
        raise BrahmaError("pass the story as text or as --file, not both")
    if args.file:
        if not args.file.is_file():
            raise BrahmaError(f"story file not found: {args.file}")
        return args.file.read_text(encoding="utf-8")
    if args.story:
        return args.story
    raise BrahmaError("pass the story as text or --file")


def _health(args, config) -> int:
    script = script_model(config)
    images = image_model(config)
    script_ok, script_detail = script.health()
    image_health = images.health()
    identity = "reference image" if "ipadapter" in config.image.workflow else "locked prompt"
    image = config.image
    print(f"script: {script.name} — {script_detail}")
    print(f"image: {images.name} — {image_health.detail}")
    print(f"identity: {identity}")
    print(
        f"sampler: {image.ckpt_name}, {image.steps} steps, cfg {image.cfg}, {image.sampler}, {image.scheduler}"
    )
    if script_ok and image_health.available:
        return 0
    return 2


def _plan(args, config) -> int:
    project, root = plan_story(_story_text(args), script_model(config), sampler_lock(config), config.workspace_dir)
    print(root)
    print(f"{project.plan.title}: {len(project.plan.pages)} pages, {len(project.plan.characters)} characters")
    return 0


def _refs(args, config) -> int:
    generate_references(args.project, image_model(config), sampler_lock(config), force=args.force)
    return 0


def _draw(args, config) -> int:
    draw_panels(args.project, image_model(config), sampler_lock(config), panel=args.panel, force=args.force)
    return 0


def _assemble(args, config) -> int:
    assemble_pages(args.project)
    return 0


def _make(args, config) -> int:
    root = make_story(
        _story_text(args),
        script_model(config),
        image_model(config),
        sampler_lock(config),
        config.workspace_dir,
    )
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
