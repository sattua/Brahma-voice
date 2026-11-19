from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from brahma.config import AppConfig, ImageConfig, ScriptConfig
from brahma.consistency import figure_clause, negative_prompt, scene_style, sheet_style
from brahma.cutout import CastShadow, key_green, match_scene, place_figure
from brahma.effects import EFFECTS_NEGATIVE, has_effect, split_effects, without_effects
from brahma.errors import BrahmaError
from brahma.models import CharacterSpec, Mark, StyleSpec
from brahma.review import duplicate_figures
from brahma.session import plan
from brahma.settings import ProjectSettings
from brahma.stage import (
    Placement,
    back_to_front,
    blocking_preview,
    blocking_report,
    check_box,
    default_boxes,
    distinct_poses,
    in_frame,
    scale_check,
    still_beat,
)


@pytest.mark.parametrize(
    ("text", "clean", "moved"),
    [
        ("near-black void, charcoal armor, cobalt and cyan sparks", "near-black void, charcoal armor, cobalt", ["cyan sparks"]),
        ("diffused sunlight with golden hour haze", "diffused sunlight", ["golden hour haze"]),
        ("wet bone and brushed steel and glowing mineral veins", "wet bone and brushed steel and mineral veins", ["glowing"]),
        ("a rainy city street at night", "a city street at night", ["rainy"]),
        ("Noche de luna llena con niebla.", "Noche de luna llena", ["niebla"]),
    ],
)
def test_effects_leave_the_text(text, clean, moved):
    got_clean, got_moved = split_effects(text)
    assert got_clean.rstrip(".") == clean
    assert len(got_moved) == len(moved)
    assert all(word in item for word, item in zip(moved, got_moved))


def test_a_negated_effect_stays():
    for text in ("no glow on the armor", "not a glow", "matte metal, no emissive lights"):
        assert without_effects(text) == text


def test_has_effect():
    assert has_effect("lens flare over the lamp")
    assert not has_effect("a lamp on the helmet")


def test_settings_keep_effects_apart():
    settings = ProjectSettings.model_validate(
        {
            "art": "photorealistic cinematic",
            "palette": "near-black void, charcoal armor, cobalt and cyan sparks",
            "line": "no ink outline",
            "rendering": "photographic",
            "lighting": "diffused sunlight with golden hour haze",
        }
    )
    assert "sparks" not in settings.palette
    assert "haze" not in settings.lighting
    assert "cyan sparks" in settings.effects
    assert "golden hour haze" in settings.effects


def test_effects_are_always_in_the_negative():
    style = StyleSpec(medium="ink", line="ink", rendering="flat", palette="teal", lighting="day")
    assert EFFECTS_NEGATIVE in negative_prompt(style)
    assert EFFECTS_NEGATIVE in negative_prompt(style, sheet=True)


def test_sheet_light_and_shot_words_stay_out_of_scenes():
    style = StyleSpec(
        medium="photorealistic cinematic close-up with sharp detail",
        line="no ink outline",
        rendering="photographic",
        palette="charcoal",
        lighting="soft studio light on the sheets, low-key frontal light in scenes and panels",
    )
    scene = scene_style(style)
    assert "close-up" not in scene.medium
    assert "studio" not in scene.lighting and "low-key frontal light" in scene.lighting
    sheet = sheet_style(style)
    assert "studio" in sheet.lighting and "low-key" not in sheet.lighting


def test_a_figure_leaves_the_sheet_layout_on_the_sheet():
    soldier = CharacterSpec(
        id="soldier",
        name="Soldier",
        role="guard",
        appearance="Gray helmet and a blue visor. Sheet on a plain white background. Both views wear the same uniform",
        wardrobe="gray cloth jacket with glowing trim",
        distinguishing="red lamp on the helmet",
        marks=[Mark(part="red lamp", side="wearer_left", text="a dull point")],
    )
    clause = figure_clause(soldier, "front")
    assert clause.startswith("Soldier. Gray helmet and a blue visor.")
    assert "white background" not in clause and "Both views" not in clause
    assert "glowing" not in clause
    assert "red lamp is on the right side of the picture" in clause
    assert ".." not in clause


def test_reference_jobs_carry_no_effects(tmp_path):
    config = AppConfig(workspace_dir=tmp_path, script=ScriptConfig(backend="mock"), image=ImageConfig(backend="cursor"))
    planned = plan(
        "El detective cruzó la calle.",
        config,
        {"palette": "teal, amber, and drifting embers", "lighting": "moonlight with thick fog"},
    )
    assert "embers" not in planned["settings"]["palette"]
    assert planned["settings"]["effects"]
    for job in planned["jobs"]:
        before_negative = job["description"].split("Avoid:")[0]
        assert "embers" not in before_negative and "thick fog" not in before_negative


def test_two_figures_never_share_a_pose():
    with pytest.raises(BrahmaError, match="same pose"):
        distinct_poses(
            [
                Placement("a", "x", "Walks, rifle in both hands.", (0.1, 0.1, 0.3, 0.9)),
                Placement("b", "x", "walks rifle in both hands", (0.5, 0.1, 0.7, 0.9)),
            ]
        )
    distinct_poses(
        [
            Placement("a", "x", "walks and watches left", (0.1, 0.1, 0.3, 0.9)),
            Placement("b", "x", "walks and watches right", (0.5, 0.1, 0.7, 0.9)),
        ]
    )


def test_boxes_and_order():
    boxes = default_boxes(3, "wide")
    assert len(boxes) == 3
    assert all(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 for x0, y0, x1, y1 in boxes)
    assert default_boxes(1, "establishing")[0][3] - default_boxes(1, "establishing")[0][1] < 0.4
    near = Placement("near", "x", "a", (0.1, 0.4, 0.4, 0.98))
    far = Placement("far", "x", "b", (0.5, 0.3, 0.6, 0.6))
    assert [item.name for item in back_to_front([near, far])] == ["far", "near"]


def test_still_beat():
    assert still_beat("Se quedan quietos, escuchando.")
    assert still_beat("They stand still.")
    assert not still_beat("Avanzan unos metros.")


def test_the_blocking_preview_draws_the_horizon(tmp_path):
    plate = tmp_path / "plate.png"
    Image.new("RGB", (400, 200), (10, 30, 10)).save(plate)
    placements = [Placement("lead", "x", "walks", (0.8, 0.1, 0.95, 1.2))]
    preview = blocking_preview(plate, placements, tmp_path / "blocking.png", horizon=0.5)
    with Image.open(preview) as image:
        assert image.getpixel((196, 100))[:3] == (255, 255, 255)
        assert image.getpixel((196, 60))[:3] == (10, 30, 10)
        assert image.getpixel((350, 80))[:3] == (255, 64, 64)
        assert image.getpixel((350, 199))[:3] != (10, 30, 10)
    report = blocking_report(placements, 0.5)
    assert report["heights"] == {"lead": 1.1}
    assert report["horizon"] == 0.5 and report["order"] == ["lead"]
    without = blocking_preview(plate, placements, tmp_path / "plain.png")
    with Image.open(without) as image:
        assert image.getpixel((196, 100))[:3] == (10, 30, 10)


def test_scale_off_flags_a_figure_too_big_for_its_spot():
    placements = [
        Placement("a", "x", "walks", (0.1, 0.3, 0.2, 0.9)),
        Placement("b", "x", "watches the path", (0.3, 0.35, 0.4, 0.95)),
        Placement("c", "x", "stands guard", (0.5, 0.1, 0.7, 0.9)),
    ]
    flagged = scale_check(placements, 0.5)
    assert [item["figure"] for item in flagged] == ["c"]
    assert flagged[0]["size"] == "too big for its spot"
    assert scale_check(placements, None) == []


def test_scale_off_leaves_a_crouching_figure_alone():
    placements = [
        Placement("a", "x", "walks", (0.1, 0.3, 0.2, 0.9)),
        Placement("b", "x", "watches the path", (0.3, 0.35, 0.4, 0.95)),
        Placement("c", "x", "se agacha detrás de la roca", (0.5, 0.1, 0.7, 0.9)),
    ]
    assert scale_check(placements, 0.5) == []


def test_scale_off_flags_a_panel_that_agrees_with_itself_but_not_the_accepted_ground():
    too_big = [
        Placement("a", "x", "walks", (0.1, 0.1, 0.2, 0.9)),
        Placement("b", "x", "stands", (0.3, 0.05, 0.4, 0.95)),
    ]
    assert scale_check(too_big, 0.5) == []
    flagged = scale_check(too_big, 0.5, ground=0.8)
    assert [item["figure"] for item in flagged] == ["a", "b"]
    assert {item["size"] for item in flagged} == {"too big for its spot"}
    assert {item["expected"] for item in flagged} == {0.8}


def test_scale_off_accepts_a_panel_on_the_accepted_ground():
    on_ground = [
        Placement("a", "x", "walks", (0.1, 0.4, 0.2, 0.9)),
        Placement("b", "x", "stands", (0.3, 0.3875, 0.4, 0.95)),
    ]
    assert scale_check(on_ground, 0.5, ground=0.8) == []
    report = blocking_report(on_ground, 0.5, ground=0.8)
    assert report["ground_ratio"] == 0.8 and report["scale_off"] == []
    assert blocking_report(on_ground, 0.5)["ground_ratio"] is None


def _figure(draw: ImageDraw.ImageDraw, left: int, arm_up: bool, mirror: bool = False) -> None:
    draw.ellipse([left + 20, 10, left + 40, 30], fill=0)
    draw.rectangle([left + 25, 30, left + 35, 80], fill=0)
    draw.line([left + 30, 80, left + 15, 120], fill=0, width=6)
    draw.line([left + 30, 80, left + 45, 120], fill=0, width=6)
    if arm_up:
        start, end = (left + 30, 40), ((left + 5, 5) if mirror else (left + 55, 5))
    else:
        start, end = (left + 30, 40), (left + 50, 70)
    draw.line([*start, *end], fill=0, width=6)


def test_review_flags_two_figures_with_the_same_pose(tmp_path: Path):
    same = Image.new("L", (240, 130), 255)
    draw = ImageDraw.Draw(same)
    _figure(draw, 10, arm_up=True)
    _figure(draw, 130, arm_up=True, mirror=True)
    path = tmp_path / "same.png"
    same.save(path)
    boxes = {"a": [10 / 240, 0, 70 / 240, 1], "b": [130 / 240, 0, 190 / 240, 1]}
    assert duplicate_figures(path, boxes)[0]["figures"] == ["a", "b"]

    other = Image.new("L", (240, 130), 255)
    draw = ImageDraw.Draw(other)
    _figure(draw, 10, arm_up=True)
    draw.rectangle([130, 60, 200, 120], fill=0)
    path = tmp_path / "other.png"
    other.save(path)
    assert duplicate_figures(path, boxes) == []


def test_a_green_figure_is_keyed_and_takes_the_light_of_the_place():
    drawn = Image.new("RGB", (300, 400), (0, 255, 0))
    drawn.paste((120, 120, 120), (100, 30, 200, 380))
    keyed = key_green(drawn)
    assert keyed.getpixel((5, 5))[3] == 0
    assert keyed.getpixel((150, 200))[3] == 255
    night = Image.new("RGB", (400, 200), (10, 20, 30))
    placed, figure = place_figure(night, drawn, (0.4, 0.2, 0.6, 0.9))
    red, green, blue = placed.getpixel((200, 100))
    assert blue > red and red < 120
    assert placed.getpixel((10, 10)) == (10, 20, 30)
    assert figure.height == 140
    toned = match_scene(keyed.crop((100, 30, 200, 380)), night)
    assert toned.getpixel((50, 50))[2] > toned.getpixel((50, 50))[0]


def test_a_box_may_leave_the_frame_feet_first():
    assert check_box([0.3, 0.1, 0.6, 1.2]) == (0.3, 0.1, 0.6, 1.2)
    with pytest.raises(BrahmaError):
        check_box([0.3, 0.1, 0.6, 1.8])
    with pytest.raises(BrahmaError):
        check_box([0.3, 0.1, 1.2, 0.9])
    assert in_frame([0.3, 0.1, 0.6, 1.2]) == (0.3, 0.1, 0.6, 1.0)

    drawn = Image.new("RGB", (300, 400), (0, 255, 0))
    drawn.paste((120, 120, 120), (100, 30, 200, 380))
    night = Image.new("RGB", (400, 200), (10, 20, 30))
    placed, figure = place_figure(night, drawn, (0.4, 0.2, 0.6, 1.2))
    assert figure.height == 200
    assert placed.size == night.size
    assert placed.getpixel((200, 199)) != (10, 20, 30)
    assert placed.getpixel((10, 10)) == (10, 20, 30)


def test_a_wide_pose_keeps_the_height_of_its_box():
    drawn = Image.new("RGB", (400, 300), (0, 255, 0))
    drawn.paste((120, 120, 120), (20, 20, 380, 280))
    night = Image.new("RGB", (400, 200), (10, 20, 30))
    _, figure = place_figure(night, drawn, (0.45, 0.2, 0.55, 0.9))
    assert figure.height == 140
    assert figure.width > 0.1 * 400


def test_a_figure_lays_its_shadow_on_the_ground_behind_it():
    drawn = Image.new("RGB", (300, 400), (0, 255, 0))
    drawn.paste((120, 120, 120), (100, 30, 200, 380))
    ground = Image.new("RGB", (400, 200), (100, 100, 100))
    plain, _ = place_figure(ground, drawn, (0.45, 0.2, 0.55, 0.9))
    assert plain.getpixel((130, 130)) == (100, 100, 100)
    laid, _ = place_figure(ground, drawn, (0.45, 0.2, 0.55, 0.9), shadow=CastShadow(back=0.4, lean=-0.5))
    assert laid.getpixel((130, 130))[0] < 85
    assert laid.getpixel((300, 130)) == (100, 100, 100)
    assert laid.getpixel((130, 60)) == (100, 100, 100)


def test_a_figure_takes_its_color_from_the_previous_panel():
    drawn = Image.new("RGB", (300, 400), (0, 255, 0))
    drawn.paste((100, 100, 100), (100, 30, 200, 380))
    jungle = Image.new("RGB", (400, 200), (10, 60, 20))
    previous = Image.new("RGB", (100, 200), (20, 30, 40))
    previous.paste((90, 95, 110), (20, 10, 80, 190))
    placed, _ = place_figure(jungle, drawn, (0.4, 0.2, 0.6, 0.9), match=previous)
    red, green, blue = placed.getpixel((200, 100))
    assert blue > green > red
    assert abs(red - 90) < 12
    tinted, _ = place_figure(jungle, drawn, (0.4, 0.2, 0.6, 0.9))
    assert tinted.getpixel((200, 100))[1] > tinted.getpixel((200, 100))[2]
