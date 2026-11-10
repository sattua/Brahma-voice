"""One new directory per story, directly under the workspace."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from brahma.errors import BrahmaError
from brahma.models import Project


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:40] or "historia"


def allocate_project(workspace: Path, title: str) -> tuple[str, Path]:
    """Create a fresh folder. The same story told again gets another folder."""

    workspace.mkdir(parents=True, exist_ok=True)
    base = slugify(title)
    number = 1
    while True:
        name = base if number == 1 else f"{base}-{number}"
        root = workspace / name
        try:
            root.mkdir()
        except FileExistsError:
            number += 1
            if number > 10000:
                raise BrahmaError(f"could not allocate a folder for {base}") from None
            continue
        return name, root


def save_project(root: Path, project: Project) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    (root / "story.txt").write_text(project.story, encoding="utf-8")


def load_project(root: Path) -> Project:
    path = root / "project.json"
    if not path.is_file():
        raise BrahmaError(f"no story at {root} (missing project.json)")
    return Project.model_validate_json(path.read_text(encoding="utf-8"))
