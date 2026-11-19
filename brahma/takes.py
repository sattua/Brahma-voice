"""Every generated picture is kept in the story folder, accepted or not.

takes/<key>/NN.png is one attempt and NN.json says what it was and why it was kept or
turned down. The accepted picture is still copied onto its dest, which is the file every
other step reads. Nothing a person was shown disappears.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from brahma.errors import BrahmaError

TAKES = "takes"


def take_folder(root: Path, key: str) -> Path:
    name = re.sub(r"[^a-z0-9_-]+", "-", key.lower()).strip("-") or "image"
    return root / TAKES / name


def record_take(
    root: Path,
    key: str,
    source: Path,
    *,
    dest: Path | None,
    status: str,
    note: str = "",
    recipe: dict | None = None,
) -> Path:
    """Store source as the next take.

    accepted and placed are copied onto dest; placed still waits for review. rejected,
    replaced, and raw (a model output before compositing) stay only in takes/.
    """

    if status not in {"accepted", "placed", "rejected", "replaced", "raw"}:
        raise BrahmaError(f"unknown take status {status}")
    source = Path(source)
    if not source.is_file():
        raise BrahmaError(f"image not found: {source}")
    folder = take_folder(root, key)
    folder.mkdir(parents=True, exist_ok=True)
    number = 1 + max((int(path.stem) for path in folder.glob("[0-9][0-9]*.png") if path.stem.isdigit()), default=0)
    take = folder / f"{number:02d}.png"
    with Image.open(source) as incoming:
        incoming.load()
        frame = incoming.convert("RGBA")
    frame.save(take, format="PNG")
    meta = {
        "key": key,
        "status": status,
        "note": note.strip()[:400],
        "source": str(source),
        "dest": str(dest) if dest is not None else "",
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if recipe is not None:
        meta["recipe"] = recipe
    take.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if status in {"accepted", "placed"} and dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.save(dest, format="PNG")
    return take


def list_takes(root: Path, key: str) -> list[dict]:
    folder = take_folder(root, key)
    out = []
    for meta in sorted(folder.glob("[0-9][0-9]*.json")):
        data = json.loads(meta.read_text(encoding="utf-8"))
        data.pop("recipe", None)
        data["file"] = str(meta.with_suffix(".png"))
        out.append(data)
    return out


def last_take(root: Path, key: str, status: str | None = None) -> Path | None:
    for item in reversed(list_takes(root, key)):
        if status is None or item["status"] == status:
            return Path(item["file"])
    return None


def retire(root: Path, key: str, dest: Path, note: str) -> Path | None:
    """Keep the current picture as a replaced take, then free dest for a new one."""

    if not dest.is_file():
        return None
    take = record_take(root, key, dest, dest=None, status="replaced", note=note)
    dest.unlink()
    return take
