from pathlib import Path

import pytest
from PIL import Image

from brahma.config import AppConfig, ImageConfig, ScriptConfig
from brahma.errors import BrahmaError
from brahma.session import plan, sequence


def _config(tmp_path) -> AppConfig:
    return AppConfig(workspace_dir=tmp_path, script=ScriptConfig(backend="mock"), image=ImageConfig(backend="cursor"))


def _png(path: Path, color) -> str:
    Image.new("RGB", (64, 64), color).save(path)
    return str(path)


def _book(tmp_path):
    config = _config(tmp_path)
    planned = plan("El detective cruzó la calle. La lluvia no paraba. Ana miró el reloj.", config)
    return config, Path(planned["project"])


def _panel(tmp_path, config, root, index, color):
    sequence(
        root, config, "noche", "place", index=index, step="panel",
        image=_png(tmp_path / f"panel-{index}.png", color), verdict="accepted",
    )


def _rgb(path: Path) -> tuple:
    with Image.open(path) as image:
        frame = image.convert("RGB")
        return frame.size, frame.getpixel((0, 0)), frame.getpixel((63, 63))


def test_a_flipbook_needs_three_accepted_panels_and_keeps_the_first(tmp_path):
    config, root = _book(tmp_path)
    sequence(root, config, "noche", "create", settings={"action": "Caminan."})
    _panel(tmp_path, config, root, 1, (20, 40, 80))
    _panel(tmp_path, config, root, 2, (0, 180, 0))
    with pytest.raises(BrahmaError, match="at least 3"):
        sequence(root, config, "noche", "flipbook")
    _panel(tmp_path, config, root, 3, (180, 0, 0))
    done = sequence(root, config, "noche", "flipbook", between=1, fps=4)
    video = Path(done["mp4"])
    frames = Path(done["frames"])
    assert video.is_file() and video.parent == root / "panels" / "noche"
    assert done["frame_count"] > 3
    assert len(list(frames.glob("frame-*.png"))) == done["frame_count"]
    panel = root / "panels" / "noche" / "panel-01.png"
    assert _rgb(frames / "frame-001.png") == _rgb(panel)
    assert "motion estimate" in done["note"]
