"""Brahma MCP server. Five tools. The agent draws; this server keeps the locks and the pages.

Workflow: novel_plan -> generate each job -> novel_refs -> novel_draw -> novel_assemble.
Copy every job description verbatim. Pass reference_image_paths unchanged.
Never use the previous panel as a reference. Image results are local file paths.
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
from brahma.session import assemble, configure, draw, health, plan, refs

INSTRUCTIONS = """\
Brahma turns a told story into a graphic novel.
The person does not run commands. You call these tools.
Workflow: novel_plan -> draw each reference job -> novel_refs with the files -> open the sheets -> novel_draw -> draw each panel job -> novel_draw with the files -> novel_assemble.
Each call to novel_plan creates a new folder in the workspace and writes settings.json (art, palette, line, rendering, lighting, negative, notes). Pass setup when the person names a look. Later images read that file; do not invent a second palette. Continuing a story uses the project path already returned, not a new novel_plan.
If the person attaches pictures, pass them as images [{path, role, id, reading}]. role style is a graphic reference and is sent with every later image. role sketch is a hand drawing: look at it and put what you see in reading (art, palette, line, rendering, lighting, notes). If you do not send a reading, the tool returns needs_reading and you must call novel_settings after looking. role character or location keeps that picture for one person or place; set id.
When a result has jobs, generate one image per job with Cursor's image tool.
Copy description verbatim. Set aspect_ratio. Pass reference_image_paths unchanged.
Do not add the previous panel as a reference. Identity comes from the character sheet, place from the location plate.
Open the reference images before drawing panels. If a face is wrong, call novel_refs with force true for that sheet only after you have a replacement, or force and draw the job again.
Dialogue-only changes go to novel_assemble as dialogue [{panel, text}] and do not need new art.
Results are compact JSON. Open the PNG paths to review. Pages are OpenRaster (.ora), editable in Krita and GIMP."""

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
    description="List or accept the style sample, character sheets and location plates. Without images, returns jobs. With images [{key, path}], stores them. force asks for the references again.",
)
async def novel_refs(
    project: Annotated[str, Field(description="Story directory returned by novel_plan.")],
    images: Annotated[list[dict[str, Any]] | None, Field(description="Generated files: [{key, path}].")] = None,
    force: bool = False,
) -> str:
    return await _run(lambda: refs(project, _config(), images, force))


@mcp.tool(
    name="novel_draw",
    description="List or accept panel art. Each job's reference_image_paths is the identity lock. Pass images [{key, path}] after generating. panel redraws one panel, for example p01_02.",
)
async def novel_draw(
    project: str,
    images: Annotated[list[dict[str, Any]] | None, Field(description="Generated panel files: [{key, path}].")] = None,
    panel: Annotated[str | None, Field(description="One panel id, such as p01_02.")] = None,
    force: bool = False,
) -> str:
    return await _run(lambda: draw(project, _config(), images, panel, force))


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
