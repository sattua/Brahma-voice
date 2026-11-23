import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from brahma.errors import BrahmaError
from brahma.figures import box_crop, top_band, view_columns, view_crop
from brahma.sequence import (
    SequenceFrame,
    SequenceSettings,
    accept_panel,
    figure_frame,
    filter_frame,
    load_sequence,
    place_image,
    plate_frame,
    plate_path,
    release_after,
    save_sequence,
    sequence_step,
    stage_of,
    unify_frame,
)


def test_sequence_settings_roundtrip(tmp_path: Path):
    saved = SequenceSettings(
        action="Caminan y avanzan por el sendero. No están parados.",
        place="bosque tropical",
        time="noche de luna llena",
        continuity="Unos segundos después han avanzado unos metros.",
        characters=["rifleman"],
        anchors="Luz roja opaca en el lado izquierdo del casco.",
        beats=["Entran al sendero.", "Avanzan unos metros."],
    )
    path = save_sequence(tmp_path, saved)
    assert path.name == "sequence.json"
    assert load_sequence(tmp_path) == saved


def test_missing_sequence_settings(tmp_path: Path):
    with pytest.raises(BrahmaError):
        load_sequence(tmp_path)


def test_box_crop_takes_the_named_fraction(tmp_path: Path):
    portrait = tmp_path / "style.png"
    image = Image.new("RGB", (100, 100), (255, 255, 255))
    image.paste((0, 0, 180), (0, 55, 34, 100))
    image.save(portrait)
    dest = box_crop(portrait, tmp_path / "brush.png", [0, 0.55, 0.34, 1])
    with Image.open(dest) as brush:
        assert brush.size == (34, 45)
        assert brush.getpixel((2, 2))[:3] == (0, 0, 180)


def test_top_band(tmp_path: Path):
    sheet = tmp_path / "sheet.png"
    body = Image.new("RGB", (100, 200), (0, 0, 200))
    body.paste((200, 0, 0), (0, 0, 100, 44))
    body.save(sheet)
    with Image.open(top_band(sheet, tmp_path / "head.png", 0.22)) as head:
        assert head.size == (100, 44)
        assert head.getpixel((50, 40))[:3] == (200, 0, 0)


def test_view_crop_finds_figures_of_different_widths(tmp_path: Path):
    strip = tmp_path / "strip.png"
    image = Image.new("RGB", (500, 100), (255, 255, 255))
    edges = [(10, 60), (90, 200), (230, 290), (330, 480)]
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200), (90, 90, 0)]
    for (left, right), color in zip(edges, colors):
        image.paste(color, (left, 10, right, 90))
    image.save(strip)
    assert view_columns(strip) == edges
    with Image.open(view_crop(strip, "back", tmp_path / "back.png")) as back:
        assert back.size == (150, 100)
        assert back.getpixel((5, 50))[:3] == (90, 90, 0)
    with Image.open(view_crop(strip, "side", tmp_path / "side.png", ["front", "three-quarter", "side", "back"])) as side:
        assert side.getpixel((5, 50))[:3] == (0, 0, 200)


FIGURES = [
    {"name": "lead", "character": "rifleman", "pose": "walks along the path and watches ahead"},
    {"name": "middle", "character": "rifleman", "pose": "walks along the path and watches the forest on his left"},
    {"name": "back", "character": "rifleman", "pose": "walks along the path and watches the forest on his right"},
]
BLOCKING = {
    "1": [
        {"figure": "lead", "box": [0.42, 0.45, 0.52, 0.8]},
        {"figure": "middle", "box": [0.34, 0.5, 0.46, 0.88]},
        {"figure": "back", "box": [0.2, 0.55, 0.36, 0.97]},
    ],
}


def _project(tmp_path: Path, **extra) -> Path:
    root = tmp_path / "book"
    sequence = root / "panels" / "patrulla"
    sequence.mkdir(parents=True)
    (root / "settings.json").write_text(
        json.dumps(
            {
                "art": "photorealistic cinematic close-up with sharp digital detail",
                "palette": "near-black void, charcoal armor, cobalt and cyan sparks",
                "line": "no ink outline",
                "rendering": "photographic skin and hair",
                "lighting": "low-key frontal light in scenes",
                "negative": "oil paint, white void",
                "notes": "DO_NOT_COPY_THESE_NOTES",
            }
        ),
        encoding="utf-8",
    )
    fields = {
        "action": "Los tres caminan por el sendero.",
        "place": "El comienzo de un sendero estrecho.",
        "time": "Noche de luna llena con niebla.",
        "shot": "wide shot, the path starts at the bottom of the frame",
        "anchors": "Linterna en el lado izquierdo del casco.",
        "figures": FIGURES,
        "blocking": BLOCKING,
        "beats": ["Entran al sendero.", "Avanzan unos metros por el mismo sendero."],
        **extra,
    }
    save_sequence(sequence, SequenceSettings.model_validate(fields))
    return sequence


def _plate(sequence: Path, index: int = 1, color=(20, 60, 20)) -> Path:
    settings = load_sequence(sequence)
    dest = plate_path(sequence, settings, index)
    Image.new("RGB", (800, 400), color).save(dest)
    settings.plates = sorted({*settings.plates, settings.plate_index(index)})
    save_sequence(sequence, settings)
    return dest


def _green_figure(path: Path, color=(200, 0, 0)) -> Path:
    """A figure job's answer: one tall shape alone on flat green."""

    image = Image.new("RGB", (300, 400), (0, 255, 0))
    image.paste(color, (100, 30, 200, 380))
    image.save(path)
    return path


def _fill(sequence: Path, index: int) -> None:
    """Every figure of panel index, painted in its box, as the image tool would."""

    settings = load_sequence(sequence)
    stage = stage_of(sequence, settings, index)
    while True:
        frame = figure_frame(sequence, index, who="rifleman")
        generated = _green_figure(sequence / f"gen-{index}-{frame.figure}.png")
        placement = next(item for item in settings.placements(index) if item.name == frame.figure)
        stage.put(placement, generated, key=f"seq:{index}:{frame.figure}", verdict="accepted")
        if stage.complete(settings.placements(index)):
            break
    Image.open(stage.base).save(stage.final)


def test_plate_comes_first_and_has_no_figures_and_no_effects(tmp_path: Path):
    sequence = _project(tmp_path)
    frame = plate_frame(sequence, 1)
    assert isinstance(frame, SequenceFrame)
    assert frame.step == "plate"
    assert frame.dest == sequence / "plate.png"
    assert frame.references == ()
    for text in (
        "photorealistic cinematic",
        "near-black void, charcoal armor, cobalt",
        "no ink outline",
        "low-key frontal light in scenes",
        "oil paint, white void",
        "no people",
        "El comienzo de un sendero estrecho.",
        "the path starts at the bottom of the frame",
    ):
        assert text in frame.description
    for text in ("DO_NOT_COPY_THESE_NOTES", "watches the forest", "close-up", "cyan sparks", "niebla"):
        assert text not in frame.description
    assert "niebla" in " ".join(frame.effects)


def test_static_camera_shares_one_plate(tmp_path: Path):
    sequence = _project(tmp_path)
    settings = load_sequence(sequence)
    assert plate_path(sequence, settings, 1) == plate_path(sequence, settings, 2) == sequence / "plate.png"
    with pytest.raises(BrahmaError, match="static"):
        plate_frame(sequence, 2)


def test_moving_camera_draws_each_plate_from_the_last(tmp_path: Path):
    sequence = _project(tmp_path, camera="moving")
    assert plate_frame(sequence, 1).dest == sequence / "plate-01.png"
    with pytest.raises(BrahmaError, match="not accepted"):
        plate_frame(sequence, 2)
    _plate(sequence, 1)
    frame = plate_frame(sequence, 2)
    assert frame.dest == sequence / "plate-02.png"
    assert frame.references == (sequence / "plate-01.png",)


def test_figures_wait_for_the_plate_and_go_back_to_front(tmp_path: Path):
    sequence = _project(tmp_path)
    with pytest.raises(BrahmaError, match="plate is not accepted"):
        figure_frame(sequence, 1, who="rifleman")
    _plate(sequence)
    frame = figure_frame(sequence, 1, who="rifleman, lamp on the right side of the picture")
    assert frame.step == "figure"
    assert frame.figure == "lead"
    assert frame.dest == sequence / "panel-01-base.png"
    assert frame.references == ()
    assert "lamp on the right side of the picture" in frame.description
    assert "watches ahead" in frame.description
    assert "flat pure green background" in frame.description
    assert "watches the forest on his left" not in frame.description
    assert "Entran al sendero." not in frame.description
    assert frame.aspect_ratio == "3:4"
    assert "DO_NOT_COPY_THESE_NOTES" not in frame.description


def test_a_figure_changes_only_its_box(tmp_path: Path):
    sequence = _project(tmp_path)
    _plate(sequence)
    settings = load_sequence(sequence)
    stage = stage_of(sequence, settings, 1)
    frame = figure_frame(sequence, 1, who="rifleman")
    generated = _green_figure(tmp_path / "figure.png")
    lead = next(item for item in settings.placements(1) if item.name == frame.figure)
    done = stage.put(lead, generated, key="seq:1:lead", verdict="accepted")
    with Image.open(stage.base) as base:
        assert base.getpixel((5, 5))[:3] == (20, 60, 20)
        assert base.getpixel((790, 395))[:3] == (20, 60, 20)
        assert base.getpixel((330, 250))[:3] == (20, 60, 20)
        assert base.getpixel((345, 250))[:3] == (20, 60, 20)
        for point in ((376, 250), (376, 185)):
            red, green, blue = base.getpixel(point)[:3]
            assert red > 90 and green < 20 and blue < 20
        assert base.getpixel((376, 175))[:3] == (20, 60, 20)
    with Image.open(done["cutout"]) as cutout:
        assert cutout.mode == "RGBA"
        assert cutout.height == 140
    assert figure_frame(sequence, 1, who="rifleman").figure == "middle"


def test_later_panel_needs_the_previous_accepted_and_looks_at_the_same_figure(tmp_path: Path):
    sequence = _project(tmp_path)
    _plate(sequence)
    _fill(sequence, 1)
    with pytest.raises(BrahmaError, match="not accepted"):
        figure_frame(sequence, 2, who="rifleman")
    accept_panel(sequence, 1)
    frame = figure_frame(sequence, 2, who="rifleman")
    assert frame.references[-1].name == "panel-02-prev-lead.png"
    assert "walks along the path and watches ahead" in frame.description


def test_two_equal_poses_in_one_panel_are_refused():
    same = [dict(item, pose="walks and watches ahead") for item in FIGURES[:2]]
    with pytest.raises(ValidationError, match="same pose"):
        SequenceSettings(action="Caminan.", figures=same)
    with pytest.raises(ValidationError, match="same pose"):
        SequenceSettings(
            action="Caminan.",
            figures=FIGURES,
            blocking={"2": [{"figure": "middle", "box": [0.3, 0.5, 0.4, 0.9], "pose": "Walks along the path and watches ahead!"}]},
        )


def test_a_pose_held_while_the_action_moves_is_flagged(tmp_path: Path):
    sequence = _project(tmp_path)
    _plate(sequence)
    _fill(sequence, 1)
    accept_panel(sequence, 1)
    frame = figure_frame(sequence, 2, who="rifleman")
    assert any(text.startswith("pose_repeated") for text in frame.warnings)
    still = _project(tmp_path / "still", beats=["Entran al sendero.", "Se quedan quietos, escuchando."])
    _plate(still)
    _fill(still, 1)
    accept_panel(still, 1)
    assert figure_frame(still, 2, who="rifleman").warnings == ()


def test_a_figure_may_turn_its_back_in_one_panel():
    settings = SequenceSettings(
        action="Caminan.",
        figures=FIGURES,
        blocking={"3": [{"figure": "middle", "box": [0.3, 0.5, 0.4, 0.9], "view": "espalda", "pose": "Turns to the moon."}]},
    )
    assert settings.view_of(3, "middle") == "back"
    assert settings.view_of(3, FIGURES[0]["name"]) == "front"
    assert settings.view_of(4, "middle") == "front"
    with pytest.raises(ValidationError):
        SequenceSettings(action="Caminan.", figures=FIGURES, blocking={"3": [{"figure": "middle", "box": [0.3, 0.5, 0.4, 0.9], "view": "upside"}]})


def test_old_poses_become_figures():
    settings = SequenceSettings.model_validate(
        {
            "action": "Los tres caminan.",
            "characters": ["rifleman"],
            "poses": ["El de adelante vigila de frente.", "El del medio vigila su izquierda."],
        }
    )
    assert [(item.name, item.character, item.pose) for item in settings.figures] == [
        ("figure-1", "rifleman", "El de adelante vigila de frente."),
        ("figure-2", "rifleman", "El del medio vigila su izquierda."),
    ]
    assert "poses" not in settings.model_dump()


def test_filter_goes_over_an_accepted_panel_into_its_own_file(tmp_path: Path):
    sequence = _project(tmp_path, effects=["cyan sparks"])
    _plate(sequence)
    _fill(sequence, 1)
    with pytest.raises(BrahmaError, match="not accepted"):
        filter_frame(sequence, 1)
    accept_panel(sequence, 1)
    frame = filter_frame(sequence, 1)
    assert frame.dest == sequence / "panel-01-fx.png"
    assert frame.references == (sequence / "panel-01.png",)
    assert "cyan sparks" in frame.description


def test_beats_past_the_end_keep_the_shot_going(tmp_path: Path):
    sequence = _project(tmp_path)
    settings = load_sequence(sequence)
    assert settings.beat(1) == "Entran al sendero."
    assert "a few seconds later" in settings.beat(5)


def test_unify_keeps_the_camera_and_takes_the_paint(tmp_path: Path):
    sequence = _project(tmp_path, brush_box=[0, 0.5, 0.5, 1])
    root = sequence.parent.parent
    (root / "refs").mkdir()
    Image.new("RGB", (40, 40), (120, 80, 40)).save(root / "refs" / "style.png")
    Image.new("RGB", (8, 8), (10, 10, 10)).save(sequence / "panel-01.png")
    Image.new("RGB", (8, 8), (90, 10, 10)).save(sequence / "panel-02.png")
    with pytest.raises(BrahmaError):
        unify_frame(sequence, 2)
    accept_panel(sequence, 1)
    frame = unify_frame(sequence, 2)
    assert [path.name for path in frame.references] == ["panel-01.png", "panel-02.png", "brush.png"]
    assert frame.dest == sequence / "panel-02.png"
    assert "Take the brush, palette, and finish from the first reference" in frame.description


def test_replacing_a_panel_releases_the_later_ones(tmp_path: Path):
    sequence = _project(tmp_path)
    Image.new("RGB", (8, 8), (10, 10, 10)).save(sequence / "panel-01.png")
    Image.new("RGB", (8, 8), (90, 10, 10)).save(sequence / "panel-02.png")
    accept_panel(sequence, 1)
    accept_panel(sequence, 2)
    assert release_after(sequence, 1) == [1, 2]
    assert load_sequence(sequence).accepted == []
    with pytest.raises(BrahmaError):
        accept_panel(sequence, 2)


def test_place_image_writes_the_file_and_refuses_assets(tmp_path: Path):
    sequence = _project(tmp_path)
    source = tmp_path / "generated.png"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(source)
    frame = plate_frame(sequence, 1)
    step = sequence_step(sequence, 1, "a")

    placed = place_image(source, frame.dest)
    assert placed == sequence / "plate.png"
    assert step == sequence / "panel-01-a.png"
    place_image(source, step)
    with Image.open(placed) as image:
        assert image.format == "PNG"
        assert image.getpixel((0, 0))[:3] == (1, 2, 3)

    assets = tmp_path / ".cursor" / "projects" / "book" / "assets" / "panel-01.png"
    with pytest.raises(BrahmaError):
        place_image(source, assets)


def test_no_patrol_text_in_the_package():
    package = Path(__file__).resolve().parent.parent / "brahma"
    for source in package.rglob("*.py"):
        text = source.read_text(encoding="utf-8").lower()
        for word in ("rifleman", "patrulla", "sendero", "helmet", "casco"):
            assert word not in text, f"{word} in {source.name}"


def test_a_figure_that_enters_later_stays_out_of_the_earlier_panels():
    settings = SequenceSettings(
        action="Caminan y avanzan.",
        figures=[
            {"name": "lead", "character": "soldier", "pose": "Camina hacia la cámara."},
            {"name": "sky", "character": "hunter", "pose": "Vuela lejos, visto desde el suelo."},
        ],
        blocking={
            "1": [{"figure": "lead", "box": [0.4, 0.5, 0.6, 0.95], "pose": "Camina hacia la cámara por el camino."}],
            "2": [
                {"figure": "lead", "box": [0.4, 0.5, 0.6, 0.95], "pose": "Sigue caminando hacia la cámara."},
                {
                    "figure": "sky",
                    "box": [0.6, 0.1, 0.68, 0.16],
                    "pose": "Vuela lejos, visto desde el suelo, delante de la luna.",
                },
            ],
        },
    )
    assert [item.name for item in settings.placements(1)] == ["lead"]
    assert [item.name for item in settings.placements(2)] == ["lead", "sky"]


def test_an_unblocked_panel_still_proposes_every_figure():
    settings = SequenceSettings(
        action="Caminan y avanzan.",
        figures=[
            {"name": "lead", "character": "soldier", "pose": "Camina hacia la cámara."},
            {"name": "sky", "character": "hunter", "pose": "Vuela lejos, visto desde el suelo."},
        ],
    )
    assert [item.name for item in settings.placements(1)] == ["lead", "sky"]


def test_a_pose_from_below_uses_the_underside_sheet(tmp_path: Path):
    from brahma.pipeline import figure_view

    folder = tmp_path / "refs" / "characters"
    folder.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(folder / "hunter-below.png")
    seen = figure_view(tmp_path, "hunter", "front", "Vuela lejos, visto desde el suelo.")
    assert seen == folder / "hunter-below.png"
    assert figure_view(tmp_path, "hunter", "front", "Camina hacia la cámara.") is None


def test_an_old_sequence_counts_its_accepted_panels_as_blocked():
    old = SequenceSettings.model_validate({"action": "Caminan por el sendero.", "accepted": [1, 2]})
    assert old.blocked == [1, 2]
    new = SequenceSettings.model_validate({"action": "Caminan por el sendero.", "accepted": [1, 2], "blocked": [1]})
    assert new.blocked == [1]
    with pytest.raises(ValidationError):
        SequenceSettings.model_validate({"action": "Caminan por el sendero.", "horizon": 1.4})
