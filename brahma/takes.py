"""Every generated picture is kept in the story folder, accepted or not.

takes/<key>/NN.png is one attempt and NN.json says what it was and why it was kept or
turned down. The accepted picture is still copied onto its dest, which is the file every
other step reads. Nothing a person was shown disappears.

takes/history.json is the undo stack of each installed file: the takes that were copied
onto it, and a cursor. Undo and redo move the cursor and copy that take back. They do not
delete a take, and they do not rewrite the text of the story.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from brahma.errors import BrahmaError

TAKES = "takes"
HISTORY = "history.json"
INSTALLED = {"accepted", "placed"}


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
    if status in INSTALLED and dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.save(dest, format="PNG")
        _note_installed(root, dest, take)
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
    """Keep the current picture as a replaced take, then free dest for a new one.

    The replaced file is the same pixels as the version already on the stack. It is not
    a new version. The next install appends one.
    """

    if not dest.is_file():
        return None
    take = record_take(root, key, dest, dest=None, status="replaced", note=note)
    dest.unlink()
    return take


def _history_path(root: Path) -> Path:
    return root / TAKES / HISTORY


def _load_history(root: Path) -> dict:
    file = _history_path(root)
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _save_history(root: Path, history: dict) -> None:
    file = _history_path(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _story_path(root: Path, raw: str | Path) -> str | None:
    """A path inside the story, relative, with forward slashes. Empty and outside paths are not."""

    text = str(raw).strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    if relative == Path("."):
        return None
    return relative.as_posix()


def _all_records(root: Path) -> list[dict]:
    """Every take under the story, oldest first. history.json is not a take."""

    folder = root / TAKES
    records = []
    for meta in folder.glob("*/*.json"):
        if not meta.stem.isdigit():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        data["file"] = str(meta.with_suffix(".png"))
        records.append(data)
    records.sort(key=lambda item: (str(item.get("time") or ""), str(item.get("file") or "")))
    return records


def _versions_from_takes(root: Path, dest_rel: str) -> list[str]:
    """Installed versions of one file, oldest first, read from the takes already on disk.

    accepted and placed whose dest is this file are versions. A replaced take whose source
    is this file counts only while nothing has been installed onto it yet: that is the
    snapshot taken before an edit of a file that never passed through record_take. After
    an install, retire's copy is the same pixels as the version already listed.
    """

    seen_installed = False
    versions: list[str] = []
    for item in _all_records(root):
        status = str(item.get("status") or "")
        take_rel = _story_path(root, item.get("file") or "")
        if take_rel is None:
            continue
        if status in INSTALLED and _story_path(root, item.get("dest") or "") == dest_rel:
            versions.append(take_rel)
            seen_installed = True
        elif status == "replaced" and _story_path(root, item.get("source") or "") == dest_rel and not seen_installed:
            versions.append(take_rel)
    return versions


def _entry(versions: list[str], cursor: int) -> dict:
    if not versions:
        return {"versions": [], "cursor": 0}
    return {"versions": versions, "cursor": max(0, min(cursor, len(versions) - 1))}


def _note_installed(root: Path, dest: Path, take: Path) -> None:
    """The picture just copied onto dest is the new end of its stack.

    The first time, the stack is built from the takes already stored, so a book from
    before undo still has its earlier versions. A cursor that was not at the end loses
    the redo tail: a new picture does not bring back a version the person had left.
    """

    dest_rel = _story_path(root, dest)
    take_rel = _story_path(root, take)
    if dest_rel is None or take_rel is None:
        return
    history = _load_history(root)
    current = history.get(dest_rel)
    if not isinstance(current, dict):
        versions = _versions_from_takes(root, dest_rel)
        if take_rel not in versions:
            versions.append(take_rel)
        cursor = versions.index(take_rel) if take_rel in versions else len(versions) - 1
        history[dest_rel] = _entry(versions, cursor)
        _save_history(root, history)
        return
    versions = [item for item in current.get("versions") or [] if isinstance(item, str)]
    cursor = int(current.get("cursor") or 0)
    if versions and cursor < len(versions) - 1:
        versions = versions[: cursor + 1]
    if not versions or versions[-1] != take_rel:
        versions.append(take_rel)
    history[dest_rel] = _entry(versions, len(versions) - 1)
    _save_history(root, history)


def _current_time(root: Path, entry: dict) -> str:
    versions = entry.get("versions") or []
    if not versions:
        return ""
    cursor = max(0, min(int(entry.get("cursor") or 0), len(versions) - 1))
    meta = (root / versions[cursor]).with_suffix(".json")
    if not meta.is_file():
        return ""
    try:
        return str(json.loads(meta.read_text(encoding="utf-8")).get("time") or "")
    except json.JSONDecodeError:
        return ""


def _known_dests(root: Path, history: dict) -> set[str]:
    dests = {key for key in history if isinstance(key, str)}
    for item in _all_records(root):
        status = str(item.get("status") or "")
        if status in INSTALLED:
            rel = _story_path(root, item.get("dest") or "")
        elif status == "replaced":
            rel = _story_path(root, item.get("source") or "")
        else:
            rel = None
        if rel is not None:
            dests.add(rel)
    return dests


def _entry_for(root: Path, dest_rel: str, history: dict) -> dict:
    current = history.get(dest_rel)
    if isinstance(current, dict) and isinstance(current.get("versions"), list):
        versions = [item for item in current["versions"] if isinstance(item, str)]
        return _entry(versions, int(current.get("cursor") or 0))
    versions = _versions_from_takes(root, dest_rel)
    return _entry(versions, len(versions) - 1)


def latest_dest(root: Path, direction: str) -> Path | None:
    """The picture installed most recently that can still take one step in this direction."""

    if direction not in {"undo", "redo"}:
        raise BrahmaError("direction is undo or redo")
    history = _load_history(root)
    chosen: str | None = None
    chosen_time = ""
    for dest_rel in _known_dests(root, history):
        entry = _entry_for(root, dest_rel, history)
        versions = entry["versions"]
        cursor = entry["cursor"]
        if direction == "undo" and cursor <= 0:
            continue
        if direction == "redo" and cursor >= len(versions) - 1:
            continue
        when = _current_time(root, entry)
        if chosen is None or when > chosen_time or (when == chosen_time and dest_rel > chosen):
            chosen = dest_rel
            chosen_time = when
    if chosen is None:
        return None
    return root / chosen


def _copy_onto(take: Path, dest: Path) -> None:
    if not take.is_file():
        raise BrahmaError(f"take not found: {take}")
    with Image.open(take) as incoming:
        incoming.load()
        frame = incoming.convert("RGBA")
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.save(dest, format="PNG")


def step(root: Path, dest: Path, direction: str) -> dict:
    """Move one picture to its previous or next installed take and copy that take onto the file.

    The other takes stay where they are. There is no step past either end, and then the
    file is left untouched.
    """

    if direction not in {"undo", "redo"}:
        raise BrahmaError("direction is undo or redo")
    dest_rel = _story_path(root, dest)
    if dest_rel is None:
        raise BrahmaError(f"not a story image path: {dest}")
    history = _load_history(root)
    entry = _entry_for(root, dest_rel, history)
    versions = entry["versions"]
    cursor = entry["cursor"]
    stuck = "nothing earlier" if direction == "undo" else "nothing later"
    if not versions or (direction == "undo" and cursor <= 0) or (direction == "redo" and cursor >= len(versions) - 1):
        history[dest_rel] = entry
        _save_history(root, history)
        return {
            "moved": False,
            "reason": stuck,
            "dest": str(root / dest_rel),
            "cursor": cursor,
            "earlier": cursor,
            "later": max(0, len(versions) - 1 - cursor),
        }
    cursor = cursor - 1 if direction == "undo" else cursor + 1
    take = root / versions[cursor]
    _copy_onto(take, root / dest_rel)
    entry = _entry(versions, cursor)
    history[dest_rel] = entry
    _save_history(root, history)
    return {
        "moved": True,
        "dest": str(root / dest_rel),
        "take": str(take),
        "cursor": cursor,
        "earlier": cursor,
        "later": len(versions) - 1 - cursor,
    }
