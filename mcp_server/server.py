"""Brahma MCP server. The agent draws; this server keeps the locks, the takes, and the pages.

Workflow: novel_plan -> generate each job -> novel_refs -> novel_draw -> novel_assemble.
Sequences go through novel_sequence, region fixes through novel_edit, checks through novel_review.
Copy every job description verbatim. Pass reference_image_paths unchanged.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from brahma.config import load_config
from brahma.errors import BrahmaError
from brahma.session import assemble, character, configure, draw, edit, health, plan, refs, review, sequence

INSTRUCTIONS = """\
Brahma turns a told story into a graphic novel.
The person does not run commands. You call these tools.
Workflow: novel_plan -> draw each reference job -> novel_refs with the files -> novel_review each sheet -> novel_draw -> draw each plate job -> novel_draw with the files -> show each blocking preview and wait for the person's yes -> novel_draw approve_blocking -> draw each figure job -> novel_draw with the files, until no jobs remain -> novel_assemble.
Each call to novel_plan creates a new folder in the workspace and writes settings.json (art, palette, line, rendering, lighting, negative, notes). Pass setup when the person names a look. Later images read that file; do not invent a second palette. Continuing a story uses the project path already returned, not a new novel_plan.
If the person attaches pictures, pass them as images [{path, role, id, reading}]. role style is a graphic reference. role sketch is a hand drawing: look at it and put what you see in reading (art, palette, line, rendering, lighting, notes). Set id on a sketch of one character so it travels only with that character, never to places or the style sample. If you do not send a reading, the tool returns needs_reading and you must call novel_settings after looking. role character or location keeps that picture for one person or place; set id.
When a result has jobs, generate one image per job with Cursor's image tool. Generate independent jobs in parallel.
Copy description verbatim. Set aspect_ratio. Pass reference_image_paths unchanged.
Hand every generated file back to the tool that asked for it (novel_refs, novel_draw, novel_sequence place, novel_edit). The tool copies it to dest inside the story folder and keeps it in takes/. Pass accept false and a note for a picture you reject: it is kept, not lost. Never give the person a path under .cursor/projects/.../assets.
Every panel is built in this order: first the place, then the blocking the person approves, then the characters, then a filter only if asked. The place is drawn empty (plate: no people, no effects). The blocking is where each character stands and how tall it is: stick figures over the plate, feet on the spot, back to front, the dashed horizon line, and each height as a share of the frame (alto). A paper drawing of stick figures inside a frame is read the same way, head to feet. Set the horizon after looking at the plate; scale_off flags a standing character too big or too small for where its feet are. On a static camera the standing figures already accepted on that plate lock one ground ratio (ground_ratio): a new figure's height is (feet − horizon) / that ratio, and anyone more than 15% off is scale_off even when the new figures agree with each other. The first panel, and a moving camera, use the median of the panel being drawn. A page panel does not take another panel's ground. Never draw a figure before the person approves the blocking of its panel: show the preview and wait. A changed box, pose, horizon, or plate takes the approval back. Then each character is drawn alone on flat green, keyed, and set into its box, back to front; outside the figure and its shadow the place does not change. novel_draw and novel_sequence op frame return these steps one at a time.
Effects (sparks, glowing specks, fireflies, bokeh, lens flare, light rays, glow, fog, mist, haze, rain, snow, smoke, embers) stay out of every base image; the tools move them into settings.json effects and keep them in the negative. A filter is drawn only when asked, over a finished panel, into a separate -fx.png that is never a reference.
No two figures share a pose: a blocking with two equal poses is refused, pose_repeated warns when a figure keeps its pose from panel n-1, and novel_review returns duplicate_pose. Redraw only that figure.
Which picture a new image may look at. A page panel: its plate looks only at the place plate (never the style sample, often a portrait); each figure looks at its character sheet, turnaround view, and pose sheet; never at another page panel. A sequence is one continuous shot: a static camera shares one plate, a moving camera draws plate n from plate n-1; each figure looks at its turnaround view, pose sheet, and the same figure in panel n-1 once n-1 is accepted. Those are the only two rules.
The pose sheet (face close-ups on top, action poses below) is drawn after the turnaround; choose its poses with novel_character poses.
Character changes go to novel_character (body, marks with a side, poses, visual). A mark on the wearer's left is on the right of the picture in a front view and on the left in a back view; the tools say this for you.
To change one part of a picture (a hand, a lamp), call novel_edit with the region. Only the region is pasted back; the rest stays pixel for pixel. Do not redraw a whole sheet to fix a hand.
Before accepting, call novel_review and open the crops. To redraw one reference, call novel_refs with force true and key.
Dialogue-only changes go to novel_assemble as dialogue [{panel, text}] and do not need new art.
Results are compact JSON. Pages are OpenRaster (.ora), editable in Krita and GIMP."""

log = logging.getLogger("brahma.mcp")
mcp = MCPServer(name="brahma", instructions=INSTRUCTIONS)


def _config():
    return load_config()


async def _run(fn) -> str:
    try:
        result = await anyio.to_thread.run_sync(fn)
    except BrahmaError as exc:
        result = {"status": "failed", "message": str(exc)}
    except Exception as exc:
        log.exception("tool crashed")
        result = {"status": "failed", "reason": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(name="novel_health", description="Check the script model and how images are drawn.")
async def novel_health() -> str:
    return await _run(lambda: health(_config()))


@mcp.tool(
    name="novel_plan",
    description="Start a story in a new workspace folder. Saves settings.json (art, palette, and the rest of the look) and returns reference image jobs. Do not paraphrase them.",
)
async def novel_plan(
    story: Annotated[str, Field(description="The story in the author's words.")],
    setup: Annotated[
        dict[str, Any] | None,
        Field(description="Look lock: art, palette, line, rendering, lighting, negative, notes. Omitted keys are chosen once and saved."),
    ] = None,
    images: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Uploads: [{path, role, id, reading}]. role is style, sketch, character, or location. A sketch reading is written into settings."),
    ] = None,
) -> str:
    return await _run(lambda: plan(story, _config(), setup, images))


@mcp.tool(
    name="novel_settings",
    description="Add graphic references to an existing project, or save the reading of a hand drawing into settings.json.",
)
async def novel_settings(
    project: Annotated[str, Field(description="Story directory.")],
    setup: Annotated[
        dict[str, Any] | None,
        Field(description="Fields to lock: art, palette, line, rendering, lighting, negative, notes."),
    ] = None,
    images: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Uploads: [{path, role, id, reading}]. role sketch with a reading updates the global look."),
    ] = None,
) -> str:
    return await _run(lambda: configure(project, _config(), setup, images))


@mcp.tool(
    name="novel_refs",
    description="List or accept the style sample, character sheets, turnarounds, pose sheets, and location plates. Without images, returns jobs. With images [{key, path, accept, note}], keeps each as a take and installs the accepted ones. force with key redraws one reference; the old one stays in takes/.",
)
async def novel_refs(
    project: Annotated[str, Field(description="Story directory returned by novel_plan.")],
    images: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Generated files: [{key, path, accept, note}]. accept defaults to true."),
    ] = None,
    force: bool = False,
    key: Annotated[str | None, Field(description="With force, only this reference, such as character:ana.")] = None,
) -> str:
    return await _run(lambda: refs(project, _config(), images, force, key))


@mcp.tool(
    name="novel_draw",
    description=(
        "Page panels in steps. Each panel returns one step at a time: key pXX_YY:plate is the empty place (no people, no effects), "
        "then the blocking (no image: under blocking, the preview pXX_YY-blocking.png with stick figures, horizon, and heights, "
        "plus boxes, heights, and scale_off) waits for the person's yes, "
        "then pXX_YY:figure:<character> draws one character alone on flat green; it is keyed and set into its box, back to front. "
        "When every figure is in, panels/pXX_YY.png is the panel. Pass images [{key, path, accept, note}] after generating. "
        "blocking {panel: {horizon, figures: [{character, box, pose}]}} sets the horizon, boxes, and distinct poses, and takes the approval back. "
        "approve_blocking [panel] only after the person says yes to its preview. effects with panel returns a filter job "
        "written to pXX_YY-fx.png, a separate layer, never a reference. panel alone redraws that panel from the plate."
    ),
)
async def novel_draw(
    project: str,
    images: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Generated files: [{key, path, accept, note}] with keys pXX_YY:plate, pXX_YY:figure:<id>, pXX_YY:fx."),
    ] = None,
    panel: Annotated[str | None, Field(description="One panel id, such as p01_02.")] = None,
    force: bool = False,
    blocking: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Per panel: {horizon, figures: [{character, box [left, top, right, bottom], pose}]} or just the figures list. "
                "Box in 0..1; the bottom may pass the frame up to 1.5. horizon is 0..1 from the top. No two poses alike."
            )
        ),
    ] = None,
    effects: Annotated[
        list[str] | None,
        Field(description="Filter only, over a finished panel: sparks, rain, fog, glow, light rays."),
    ] = None,
    approve_blocking: Annotated[
        list[str] | None,
        Field(description="Panels whose blocking preview the person approved, such as [p01_02]. Not in the same call that changes them."),
    ] = None,
) -> str:
    return await _run(lambda: draw(project, _config(), images, panel, force, blocking, effects, approve_blocking))


@mcp.tool(
    name="novel_character",
    description="Change one character in project.json, validated. fields: name, role, appearance, wardrobe, distinguishing, body, marks [{part, side: wearer_left|wearer_right|center, text}], poses (up to six action poses for the pose sheet), visual. Returns where each mark lands in front and back views.",
)
async def novel_character(
    project: str,
    id: Annotated[str, Field(description="Character id.")],
    fields: Annotated[dict[str, Any], Field(description="Only the fields that change.")],
) -> str:
    return await _run(lambda: character(project, _config(), id, fields))


@mcp.tool(
    name="novel_sequence",
    description=(
        "One continuous shot in panels/<name>, panel-01.png, panel-02.png... Each panel is staged: the empty place, then the blocking the person approves, "
        "then each figure, then an optional filter. "
        "op create|set with settings (action, place, time, continuity, characters, anchors, camera static|moving, shot, location, "
        "figures [{name, character, pose}], blocking {panel: [{figure, box, pose, view}]}, horizon (0..1 from the top), beats, view, aspect_ratio, effects, "
        "shadow {back, lean, strength}, brush_source, brush_box). A changed box, pose, or horizon takes back the approval of that panel's blocking. "
        "op frame with index returns the next step: the plate (no people, no effects; a static camera shares one plate), "
        "then the blocking (status needs_approval: preview panel-NN-blocking.png with stick figures, horizon, and heights, plus boxes, heights, ground_ratio, scale_off; on a static camera ground_ratio is the accepted ground and a new height is (feet − horizon) / ground_ratio), "
        "then one figure at a time, back to front, drawn alone on flat green and set into its box; it looks at its turnaround view, pose sheet, "
        "and the same figure in accepted panel n-1. No figure is drawn before op accept step blocking. op stage with index redraws the blocking preview. "
        "op place with index, step plate|figure|filter|panel, figure, image, verdict pending|accepted|rejected, note. "
        "op accept with index and step plate|blocking|figure|panel; step blocking only after the person says yes to the preview. "
        "op filter with index and effects returns the filter job, written to a separate file. "
        "op review with index compares panel n with n-1 and flags figures with the same pose. "
        "op unify with index repaints panel n with the paint of accepted n-1, keeping its camera. op status."
    ),
)
async def novel_sequence(
    project: str,
    name: Annotated[str, Field(description="Sequence folder name, such as patrulla-nocturna.")],
    op: Annotated[
        str, Field(description="create, set, status, frame, plate, stage, figure, filter, place, accept, review, or unify.")
    ],
    index: Annotated[int | None, Field(description="Panel number, starting at 1.")] = None,
    settings: Annotated[dict[str, Any] | None, Field(description="For create and set.")] = None,
    image: Annotated[str | None, Field(description="For place: the file the image tool wrote.")] = None,
    verdict: Annotated[str, Field(description="For place: pending, accepted, or rejected.")] = "pending",
    note: Annotated[str, Field(description="Why a take was accepted or rejected.")] = "",
    step: Annotated[str, Field(description="For place: plate, figure, filter, or panel. For accept: plate, blocking, figure, or panel.")] = "panel",
    figure: Annotated[str | None, Field(description="For place, accept, and figure: the figure name, such as lead.")] = None,
    effects: Annotated[list[str] | None, Field(description="For filter: sparks, rain, fog, glow, light rays.")] = None,
) -> str:
    return await _run(
        lambda: sequence(
            project,
            _config(),
            name,
            op,
            index=index,
            settings=settings,
            image=image,
            verdict=verdict,
            note=note,
            step=step,
            figure=figure,
            effects=effects,
        )
    )


@mcp.tool(
    name="novel_edit",
    description=(
        "Change only one region of a picture in the story (sheet, turnaround, pose sheet, panel). "
        "First call with target, regions [[x0,y0,x1,y1]] in pixels or 0..1 fractions, and change: returns a job whose first reference is the picture with the region in magenta. "
        "Second call with target and image: only the region is pasted back, everything else stays pixel for pixel. Both versions are kept in takes/."
    ),
)
async def novel_edit(
    project: str,
    target: Annotated[str, Field(description="Picture inside the story, absolute or relative to it.")],
    regions: Annotated[list[list[float]] | None, Field(description="Boxes [x0, y0, x1, y1].")] = None,
    change: Annotated[str | None, Field(description="What the region becomes.")] = None,
    image: Annotated[str | None, Field(description="Generated file, on the second call.")] = None,
    note: str = "",
) -> str:
    return await _run(lambda: edit(project, _config(), target, regions, change, image, note))


@mcp.tool(
    name="novel_review",
    description="Named crops of a picture in the story, and with compare a side-by-side strip and a change percent per region. regions {name: [x0,y0,x1,y1]} are kept for the next review. Also lists the takes of that picture.",
)
async def novel_review(
    project: str,
    target: Annotated[str, Field(description="Picture inside the story.")],
    regions: Annotated[dict[str, list[float]] | None, Field(description="Named boxes in pixels or 0..1 fractions.")] = None,
    compare: Annotated[str | None, Field(description="Another picture inside the story, such as a replaced take.")] = None,
) -> str:
    return await _run(lambda: review(project, target, regions, compare))


@mcp.tool(
    name="novel_assemble",
    description="Build layered OpenRaster pages from the current panel art. Optional dialogue [{panel, text}] changes a balloon without redrawing.",
)
async def novel_assemble(
    project: str,
    dialogue: Annotated[list[dict[str, Any]] | None, Field(description="Balloon edits: [{panel, text}].")] = None,
) -> str:
    return await _run(lambda: assemble(project, dialogue))


@mcp.resource("brahma://health", name="health", description="Script model and image backend.", mime_type="application/json")
def health_resource() -> str:
    return json.dumps(health(_config()), ensure_ascii=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
