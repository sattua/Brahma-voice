from pathlib import Path

import pytest
from PIL import Image

from brahma.config import AppConfig, ImageConfig, ScriptConfig
from brahma.consistency import character_clause, character_sheet_prompt, marks_clause, negative_prompt
from brahma.errors import BrahmaError
from brahma.models import CharacterSpec, Mark, StyleSpec
from brahma.session import character, draw, edit, plan, refs, review, sequence
from brahma.store import load_project
from brahma.takes import list_takes


def _config(tmp_path) -> AppConfig:
    return AppConfig(workspace_dir=tmp_path, script=ScriptConfig(backend="mock"), image=ImageConfig(backend="cursor"))


def _png(path: Path, color=(20, 40, 80), size=(64, 64)) -> str:
    Image.new("RGB", size, color).save(path)
    return str(path)


def _creature() -> CharacterSpec:
    return CharacterSpec(
        id="cyber",
        name="Cyber",
        role="protagonist",
        appearance="insect cyborg with an open crown of eight eyes and no mouth",
        wardrobe="matte machined metal shell",
        distinguishing="nose is a grille of vertical slits",
        body="six limbs: two arms and four mantis walking legs, four dragonfly wings",
        marks=[
            Mark(part="three-finger hand", side="wearer_left", text="charcoal-black reptile scales from the wrist down"),
            Mark(part="toothed maw hand", side="derecha", text="bare machined silver with metal teeth"),
        ],
    )


def test_marks_follow_the_view():
    creature = _creature()
    front = marks_clause(creature, "front")
    back = marks_clause(creature, "back")
    assert "three-finger hand is on the right side of the picture" in front
    assert "toothed maw hand is on the left side of the picture" in front
    assert "three-finger hand is on the left side of the picture" in back
    assert "wearer's left" in marks_clause(creature, "side")
    assert "three-finger hand on the wearer's left side" in character_clause(creature)


def test_sheet_prompt_keeps_the_body_plan_and_drops_the_person():
    style = StyleSpec(
        medium="photorealistic cinematic creature",
        line="no ink outline",
        rendering="photographic flesh, matte machined metal plates, no emissive lights",
        palette="wet bone and brushed steel",
        lighting="humid daylight",
        negative="cartoon, photorealistic photo, glowing lights, extra limbs",
    )
    prompt = character_sheet_prompt(style, _creature())
    assert "person" not in prompt
    assert "standing pose" not in prompt
    assert "six limbs" in prompt
    negative = negative_prompt(style, sheet=True)
    assert "photorealistic photo" not in negative
    assert "3d render" not in negative
    assert "glowing lights" in negative
    assert "cartoon" in negative


def test_shared_negative_no_longer_bans_limbs_or_photos():
    style = StyleSpec(medium="inked graphic novel", line="ink", rendering="flat color", palette="teal", lighting="day")
    negative = negative_prompt(style)
    assert "extra limbs" not in negative
    assert "photorealistic" not in negative


def _book(tmp_path, images=None):
    config = _config(tmp_path)
    planned = plan("El detective cruzó la calle. La lluvia no paraba. Ana miró el reloj.", config, images=images)
    return config, Path(planned["project"]), planned


def _install_all(config, root, jobs, tmp_path):
    files = [{"key": job["key"], "path": _png(tmp_path / f"{job['key'].replace(':', '_')}.png")} for job in jobs]
    return refs(root, config, images=files)


def test_a_character_sketch_never_reaches_places_or_the_style_sample(tmp_path):
    upload = _png(tmp_path / "boceto.png", (200, 180, 40))
    reading = {"art": "pencil", "palette": "graphite", "line": "soft", "rendering": "hatching", "lighting": "flat"}
    config, root, planned = _book(
        tmp_path, images=[{"path": upload, "role": "sketch", "id": "protagonista", "reading": reading}]
    )
    sketch = str(root / "references" / "sketch-01.png")
    by_key = {job["key"]: job for job in planned["jobs"]}
    assert sketch in by_key["character:protagonista"]["reference_image_paths"]
    assert sketch not in by_key["location:escena"]["reference_image_paths"]
    assert sketch not in by_key["style"]["reference_image_paths"]


def test_a_character_reading_stays_out_of_places(tmp_path):
    upload = _png(tmp_path / "boceto.png", (200, 180, 40))
    reading = {"art": "pencil", "palette": "graphite", "line": "soft", "rendering": "hatching", "lighting": "flat",
               "notes": "SIX_LIMBED_CREATURE"}
    _config_, _root, planned = _book(
        tmp_path, images=[{"path": upload, "role": "sketch", "id": "protagonista", "reading": reading}]
    )
    by_key = {job["key"]: job for job in planned["jobs"]}
    assert "SIX_LIMBED_CREATURE" in by_key["character:protagonista"]["description"]
    assert "SIX_LIMBED_CREATURE" not in by_key["location:escena"]["description"]
    assert "SIX_LIMBED_CREATURE" not in by_key["style"]["description"]


def test_a_rejected_take_is_kept_and_dest_does_not_change(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    sheet = root / "refs" / "characters" / "protagonista.png"
    before = sheet.read_bytes()
    bad = _png(tmp_path / "bad.png", (250, 0, 0))
    result = refs(root, config, images=[{"key": "character:protagonista", "path": bad, "accept": False, "note": "two legs"}])
    assert result["rejected"] == ["character:protagonista"]
    assert sheet.read_bytes() == before
    takes = list_takes(root, "character:protagonista")
    assert [item["status"] for item in takes] == ["accepted", "rejected"]
    assert takes[-1]["note"] == "two legs"
    assert Path(takes[-1]["file"]).is_file()


def test_force_with_key_redraws_one_reference_and_keeps_the_old_one(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    redo = refs(root, config, force=True, key="character:protagonista")
    keys = {job["key"] for job in redo["jobs"]}
    assert "character:protagonista" in keys
    assert "location:escena" not in keys
    assert (root / "refs" / "locations" / "escena.png").is_file()
    assert [item["status"] for item in list_takes(root, "character:protagonista")] == ["accepted", "replaced"]


def test_a_character_without_a_body_gets_no_sheet(tmp_path):
    config, root, _planned = _book(tmp_path)
    character(root, config, "narrador", {"name": "Narrador", "role": "narrator", "appearance": "a voice with no body",
                                         "wardrobe": "none", "distinguishing": "voice only", "visual": False})
    again = refs(root, config)
    assert "character:narrador" not in {job["key"] for job in again["jobs"]}


def test_character_tool_validates_and_saves_marks(tmp_path):
    config, root, _planned = _book(tmp_path)
    done = character(
        root,
        config,
        "protagonista",
        {"marks": [{"part": "red lamp", "side": "left", "text": "small cylinder on the helmet"}]},
    )
    assert "red lamp is on the right side of the picture" in done["front"]
    assert load_project(root).plan.characters[0].marks[0].side == "wearer_left"
    with pytest.raises(BrahmaError):
        character(root, config, "protagonista", {"marks": [{"part": "x", "side": "up", "text": "nowhere"}]})
    with pytest.raises(BrahmaError):
        character(root, config, "protagonista", {"mood": "dark"})


def test_edit_changes_only_the_region(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    sheet = root / "refs" / "characters" / "protagonista.png"
    Image.new("RGB", (100, 100), (10, 120, 200)).save(sheet)
    job = edit(root, config, str(sheet), regions=[[20, 20, 60, 60]], change="a dark scaled hand")
    assert job["status"] == "needs_images"
    mask = job["jobs"][0]["reference_image_paths"][0]
    with Image.open(mask) as painted:
        assert painted.getpixel((40, 40))[:3] == (255, 0, 255)
        assert painted.getpixel((5, 5))[:3] == (10, 120, 200)
    generated = _png(tmp_path / "gen.png", (250, 250, 0), size=(200, 200))
    done = edit(root, config, str(sheet), image=generated)
    assert done["status"] == "ok"
    with Image.open(sheet) as result:
        rgb = result.convert("RGB")
        for x in range(100):
            for y in range(100):
                if not (20 <= x < 60 and 20 <= y < 60):
                    assert rgb.getpixel((x, y)) == (10, 120, 200)
        assert rgb.getpixel((40, 40)) == (250, 250, 0)
    statuses = [item["status"] for item in list_takes(root, "edit:refs-characters-protagonista")]
    assert statuses == ["raw", "replaced", "accepted"]


def test_review_crops_and_measures_change(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    sheet = root / "refs" / "characters" / "protagonista.png"
    other = root / "refs" / "locations" / "escena.png"
    Image.new("RGB", (100, 100), (0, 0, 0)).save(sheet)
    Image.new("RGB", (100, 100), (255, 255, 255)).save(other)
    report = review(root, str(sheet), regions={"hand": [0, 0, 0.5, 0.5]}, compare=str(other))
    assert Path(report["crops"]["hand"]).is_file()
    assert Path(report["strip"]).is_file()
    assert report["changed_percent"]["hand"] == 100.0
    again = review(root, str(sheet))
    assert list(again["crops"]) == ["hand"]


def _green_figure(path: Path, color) -> str:
    image = Image.new("RGB", (300, 400), (0, 255, 0))
    image.paste(color, (100, 30, 200, 380))
    image.save(path)
    return str(path)


def _people(config, root, tmp_path):
    strip = tmp_path / "strip.png"
    Image.new("RGB", (400, 100), (255, 255, 255)).save(strip)
    refs(root, config, images=[{"key": "turnaround:protagonista", "path": str(strip)}])
    refs(root, config, images=[{"key": "poses:protagonista", "path": _png(tmp_path / "poses.png")}])


def test_sequence_tool_builds_place_then_figures_then_reviews(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    sequence(
        root,
        config,
        "noche",
        "create",
        settings={
            "action": "Dos figuras cruzan la plaza bajo la lluvia.",
            "time": "noche con lluvia",
            "figures": [
                {"name": "front", "character": "protagonista", "pose": "walks ahead"},
                {"name": "back", "character": "protagonista", "pose": "turns to look behind"},
            ],
            "blocking": {"1": [{"figure": "front", "box": [0.1, 0.3, 0.45, 0.95]}, {"figure": "back", "box": [0.55, 0.2, 0.8, 0.7]}]},
            "beats": ["Entran.", "Cruzan."],
        },
    )
    plate = sequence(root, config, "noche", "frame", index=1)
    assert plate["step"] == "plate"
    job = plate["jobs"][0]
    assert job["dest"].endswith("panels/noche/plate.png")
    assert not any("protagonista" in path for path in job["reference_image_paths"])
    assert "no people" in job["description"] and "lluvia" not in job["description"]
    assert job["effects_moved"]
    with pytest.raises(BrahmaError, match="plate"):
        sequence(root, config, "noche", "figure", index=1)
    sequence(root, config, "noche", "place", index=1, step="plate", image=_png(tmp_path / "plate.png", (20, 60, 20), (320, 180)))
    assert sequence(root, config, "noche", "frame", index=1)["step"] == "plate"
    sequence(root, config, "noche", "accept", index=1, step="plate")
    staged = sequence(root, config, "noche", "stage", index=1)
    assert staged["order"] == ["back", "front"]
    assert Path(staged["preview"]).is_file()
    waiting = sequence(root, config, "noche", "frame", index=1)
    assert waiting["step"] == "blocking" and waiting["status"] == "needs_approval"
    with pytest.raises(BrahmaError, match="not approved"):
        sequence(root, config, "noche", "figure", index=1)
    sequence(root, config, "noche", "accept", index=1, step="blocking")

    first = sequence(root, config, "noche", "frame", index=1)
    assert first["jobs"][0]["figure"] == "back"
    assert first["jobs"][0]["reference_image_paths"][0].endswith("protagonista-front.png")
    assert first["jobs"][0]["aspect_ratio"] == "3:4"
    assert first["jobs"][0]["dest"].endswith("panel-01-base.png")
    placed = sequence(root, config, "noche", "place", index=1, step="figure", figure="back",
                      image=_green_figure(tmp_path / "back.png", (200, 0, 0)))
    assert "next_actions" in placed and placed.get("panel") is None
    with pytest.raises(BrahmaError, match="not accepted"):
        sequence(root, config, "noche", "figure", index=1)
    sequence(root, config, "noche", "accept", index=1, step="figure", figure="back")
    second = sequence(root, config, "noche", "frame", index=1)["jobs"][0]
    assert second["figure"] == "front"
    assert "walks ahead" in second["description"] and "turns to look behind" not in second["description"]
    done = sequence(root, config, "noche", "place", index=1, step="figure", figure="front",
                    image=_green_figure(tmp_path / "front.png", (0, 0, 200)), verdict="accepted")
    assert done["panel"].endswith("panels/noche/panel-01.png")
    with Image.open(done["panel"]) as panel:
        assert panel.getpixel((300, 10))[:3] == (20, 60, 20)
        front_red, _green, front_blue = panel.getpixel((88, 110))[:3]
        back_red, _green, back_blue = panel.getpixel((216, 80))[:3]
        assert front_blue > 90 and front_red < 20
        assert back_red > 90 and back_blue < 20
    checked = sequence(root, config, "noche", "review", index=1)
    assert "duplicate_pose" in checked

    with pytest.raises(BrahmaError, match="not accepted"):
        sequence(root, config, "noche", "frame", index=2)
    sequence(root, config, "noche", "accept", index=1)
    assert sequence(root, config, "noche", "frame", index=2)["step"] == "blocking"
    sequence(root, config, "noche", "accept", index=2, step="blocking")
    later = sequence(root, config, "noche", "frame", index=2)
    assert later["step"] == "figure"
    assert later["jobs"][0]["reference_image_paths"][-1].endswith("panel-02-prev-back.png")
    assert any(item.startswith("pose_repeated") for item in later["warnings"])
    replaced = sequence(root, config, "noche", "place", index=1, step="plate",
                        image=_png(tmp_path / "plate2.png", (60, 20, 20), (320, 180)))
    assert replaced["released"] == [1]
    status = sequence(root, config, "noche", "status")
    assert status["settings"]["accepted"] == [] and status["settings"]["plates"] == []
    assert status["settings"]["blocked"] == []
    statuses = [take["status"] for take in list_takes(root, "sequence:noche:plate")]
    assert "replaced" in statuses and statuses[-1] == "placed"


def test_pose_sheet_follows_the_turnaround_and_reaches_sequences(tmp_path):
    config, root, planned = _book(tmp_path)
    after_sheets = _install_all(config, root, planned["jobs"], tmp_path)
    assert "poses:protagonista" not in {job["key"] for job in after_sheets["jobs"]}
    strip = tmp_path / "strip.png"
    Image.new("RGB", (400, 100), (255, 255, 255)).save(strip)
    after_turn = refs(root, config, images=[{"key": "turnaround:protagonista", "path": str(strip)}])
    character(root, config, "protagonista", {"poses": ["aiming through the scope", "kneeling behind cover"]})
    jobs = {job["key"]: job for job in refs(root, config)["jobs"]}
    assert "poses:protagonista" in {job["key"] for job in after_turn["jobs"]}
    posing = jobs["poses:protagonista"]
    assert "1. aiming through the scope; 2. kneeling behind cover" in posing["description"]
    assert "close-ups of the face" in posing["description"]
    assert "every pose different" in posing["description"]
    assert posing["dest"].endswith("refs/characters/protagonista-poses.png")
    refs(root, config, images=[{"key": "poses:protagonista", "path": _png(tmp_path / "poses.png")}])
    sequence(
        root,
        config,
        "noche",
        "create",
        settings={"action": "Cruza la plaza.", "characters": ["protagonista"], "poses": ["cruza la plaza con prisa"]},
    )
    sequence(root, config, "noche", "place", index=1, step="plate", image=_png(tmp_path / "plate.png"), verdict="accepted")
    assert sequence(root, config, "noche", "frame", index=1)["step"] == "blocking"
    sequence(root, config, "noche", "accept", index=1, step="blocking")
    frame = sequence(root, config, "noche", "frame", index=1)["jobs"][0]
    assert frame["figure"] == "figure-1"
    assert any(path.endswith("protagonista-poses.png") for path in frame["reference_image_paths"])
    assert "pose sheet" in frame["description"]


def test_page_panels_are_built_place_first(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    shown = {
        f"p01_{index:02d}"
        for index, panel in enumerate(load_project(root).plan.pages[0].panels, start=1)
        if panel.character_ids
    }
    plates = draw(root, config)
    keys = {job["key"] for job in plates["jobs"]}
    assert keys and all(key.endswith(":plate") for key in keys)
    assert all("protagonista" not in "".join(job["reference_image_paths"]) for job in plates["jobs"])
    assert all("style.png" not in "".join(job["reference_image_paths"]) for job in plates["jobs"])
    assert all("no people" in job["description"] for job in plates["jobs"])
    files = [{"key": job["key"], "path": _png(tmp_path / f"{job['key'][:6]}.png", (20, 60, 20), (320, 240))} for job in plates["jobs"]]
    waiting = draw(root, config, images=files)
    assert waiting["status"] == "needs_approval" and not waiting.get("jobs")
    assert set(waiting["blocking"]) == shown
    assert Path(waiting["blocking"]["p01_02"]["preview"]).is_file()
    assert waiting["blocking"]["p01_02"]["proposed"] == ["protagonista"]
    assert "p01_02" in shown and "p01_01" not in shown
    assert (root / "panels" / "p01_01.png").is_file()
    with pytest.raises(BrahmaError, match="not approved"):
        draw(root, config, images=[{"key": "p01_02:figure:protagonista", "path": _green_figure(tmp_path / "early.png", (200, 0, 0))}])
    figures = draw(root, config, approve_blocking=sorted(shown))
    assert {job["key"] for job in figures["jobs"]} == {f"{key}:figure:protagonista" for key in shown}
    job = next(item for item in figures["jobs"] if item["key"].startswith("p01_02"))
    names = [Path(path).name for path in job["reference_image_paths"]]
    assert names == ["protagonista.png", "protagonista-front.png", "protagonista-poses.png"]
    assert "flat pure green background" in job["description"]
    assert "ink-blue coat" in job["description"]
    done = draw(root, config, images=[
        {"key": item["key"], "path": _green_figure(tmp_path / f"fig{n}.png", (200, 0, 0))} for n, item in enumerate(figures["jobs"])
    ])
    assert done["status"] == "ok"
    assert done["next_actions"] == ["novel_assemble"]
    with Image.open(root / "panels" / "p01_02.png") as panel:
        assert panel.getpixel((2, 2))[:3] == (20, 60, 20)
    assert "duplicate_pose" not in review(root, "panels/p01_02.png")
    fx = draw(root, config, panel="p01_02", effects=["rain"])
    assert fx["jobs"][0]["dest"].endswith("panels/p01_02-fx.png")
    assert fx["jobs"][0]["reference_image_paths"] == [str(root / "panels" / "p01_02.png")]
    draw(root, config, panel="p01_02", effects=["rain"], images=[{"key": "p01_02:fx", "path": _png(tmp_path / "fx.png", (0, 0, 255))}])
    with Image.open(root / "panels" / "p01_02.png") as panel:
        assert panel.getpixel((2, 2))[:3] == (20, 60, 20)


def test_page_blocking_refuses_two_equal_poses(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    with pytest.raises(BrahmaError):
        draw(root, config, blocking={"p01_02": [{"character": "nadie", "box": [0.1, 0.2, 0.4, 0.9]}]})
    draw(root, config, blocking={"p01_02": [{"character": "protagonista", "box": [0.6, 0.3, 0.9, 0.95], "pose": "leans on the wall"}]})
    project = load_project(root)
    assert project.plan.pages[0].panels[1].figures[0].pose == "leans on the wall"


def _blocked_sequence(config, root, tmp_path):
    sequence(
        root,
        config,
        "ronda",
        "create",
        settings={
            "action": "Dos figuras cruzan la plaza.",
            "figures": [
                {"name": "front", "character": "protagonista", "pose": "walks ahead"},
                {"name": "back", "character": "protagonista", "pose": "turns to look behind"},
            ],
            "blocking": {"1": [{"figure": "front", "box": [0.1, 0.3, 0.45, 0.95]}, {"figure": "back", "box": [0.55, 0.2, 0.8, 0.7]}]},
        },
    )
    sequence(root, config, "ronda", "place", index=1, step="plate", verdict="accepted",
             image=_png(tmp_path / "ronda.png", (20, 60, 20), (320, 180)))
    assert sequence(root, config, "ronda", "frame", index=1)["status"] == "needs_approval"
    sequence(root, config, "ronda", "accept", index=1, step="blocking")


def test_a_moved_box_takes_the_sequence_blocking_back(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    _blocked_sequence(config, root, tmp_path)
    sequence(root, config, "ronda", "place", index=1, step="figure", figure="back", verdict="accepted",
             image=_green_figure(tmp_path / "back.png", (200, 0, 0)))
    moved = sequence(root, config, "ronda", "set", settings={
        "blocking": {"1": [{"figure": "front", "box": [0.05, 0.25, 0.45, 0.98]}, {"figure": "back", "box": [0.55, 0.2, 0.8, 0.7]}]},
    })
    assert moved["blocking_to_approve"] == [1]
    status = sequence(root, config, "ronda", "status")
    assert status["settings"]["blocked"] == []
    assert status["stages"]["1"]["placed"] == [] and not status["stages"]["1"]["blocking_approved"]
    with pytest.raises(BrahmaError, match="not approved"):
        sequence(root, config, "ronda", "place", index=1, step="figure", figure="back",
                 image=_green_figure(tmp_path / "back2.png", (200, 0, 0)))
    waiting = sequence(root, config, "ronda", "frame", index=1)
    assert waiting["step"] == "blocking" and waiting["boxes"]["front"] == [0.05, 0.25, 0.45, 0.98]


def test_a_new_horizon_asks_again_only_where_the_panel_is_not_accepted(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    _blocked_sequence(config, root, tmp_path)
    for name, color in (("back", (200, 0, 0)), ("front", (0, 0, 200))):
        sequence(root, config, "ronda", "place", index=1, step="figure", figure=name, verdict="accepted",
                 image=_green_figure(tmp_path / f"{name}.png", color))
    sequence(root, config, "ronda", "accept", index=1)
    sequence(root, config, "ronda", "frame", index=2)
    sequence(root, config, "ronda", "accept", index=2, step="blocking")
    changed = sequence(root, config, "ronda", "set", settings={"horizon": 0.4})
    assert changed["blocking_to_approve"] == [2] and "released" not in changed
    status = sequence(root, config, "ronda", "status")["settings"]
    assert status["blocked"] == [1] and status["accepted"] == [1] and status["horizon"] == 0.4
    staged = sequence(root, config, "ronda", "stage", index=2)
    assert staged["status"] == "needs_approval" and staged["horizon"] == 0.4


def test_a_static_camera_measures_the_new_panel_against_the_accepted_ground(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    sequence(
        root,
        config,
        "ronda",
        "create",
        settings={
            "action": "Dos figuras cruzan la plaza.",
            "horizon": 0.5,
            "figures": [
                {"name": "front", "character": "protagonista", "pose": "walks ahead"},
                {"name": "back", "character": "protagonista", "pose": "turns to look behind"},
            ],
            "blocking": {
                "1": [
                    {"figure": "front", "box": [0.1, 0.4, 0.45, 0.9]},
                    {"figure": "back", "box": [0.55, 0.45, 0.8, 0.7]},
                ]
            },
        },
    )
    sequence(root, config, "ronda", "place", index=1, step="plate", verdict="accepted",
             image=_png(tmp_path / "ground.png", (20, 60, 20), (320, 180)))
    sequence(root, config, "ronda", "frame", index=1)
    sequence(root, config, "ronda", "accept", index=1, step="blocking")
    for name, color in (("back", (200, 0, 0)), ("front", (0, 0, 200))):
        sequence(root, config, "ronda", "place", index=1, step="figure", figure=name, verdict="accepted",
                 image=_green_figure(tmp_path / f"ground-{name}.png", color))
    sequence(root, config, "ronda", "accept", index=1)
    sequence(root, config, "ronda", "set", settings={
        "blocking": {
            "1": [
                {"figure": "front", "box": [0.1, 0.4, 0.45, 0.9]},
                {"figure": "back", "box": [0.55, 0.45, 0.8, 0.7]},
            ],
            "2": [
                {"figure": "front", "box": [0.1, 0.1, 0.4, 0.9], "pose": "walks too close"},
                {"figure": "back", "box": [0.55, 0.3, 0.8, 0.7], "pose": "stands too close"},
            ],
        }
    })
    staged = sequence(root, config, "ronda", "frame", index=2)
    assert staged["ground_ratio"] == 0.8
    assert [item["figure"] for item in staged["scale_off"]] == ["front", "back"]
    assert sequence(root, config, "ronda", "status")["settings"]["accepted"] == [1]


def test_a_changed_page_blocking_or_plate_asks_again(tmp_path):
    config, root, planned = _book(tmp_path)
    _install_all(config, root, planned["jobs"], tmp_path)
    _people(config, root, tmp_path)
    plates = draw(root, config)
    files = [{"key": job["key"], "path": _png(tmp_path / f"{job['key'][:6]}.png", (20, 60, 20), (320, 240))} for job in plates["jobs"]]
    draw(root, config, images=files)
    with pytest.raises(BrahmaError, match="no blocking preview"):
        draw(root, config, approve_blocking=["p01_01"])
    draw(root, config, approve_blocking=["p01_02"])
    assert load_project(root).plan.pages[0].panels[1].blocked
    changed = draw(root, config, blocking={"p01_02": {"horizon": 0.55}})
    assert changed["status"] == "needs_approval" and changed["blocking_to_approve"] == ["p01_02"]
    assert changed["blocking"]["p01_02"]["horizon"] == 0.55
    assert not load_project(root).plan.pages[0].panels[1].blocked
    lower = {"p01_02": [{"character": "protagonista", "box": [0.5, 0.2, 0.9, 1.2], "pose": "sale por abajo"}]}
    with pytest.raises(BrahmaError, match="same call"):
        draw(root, config, blocking=lower, approve_blocking=["p01_02"])
    assert all(item.box != [0.5, 0.2, 0.9, 1.2] for item in load_project(root).plan.pages[0].panels[1].figures)
    draw(root, config, blocking=lower)
    draw(root, config, approve_blocking=["p01_02"])
    assert load_project(root).plan.pages[0].panels[1].figures[0].box == [0.5, 0.2, 0.9, 1.2]
    draw(root, config, images=[{"key": "p01_02:plate", "path": _png(tmp_path / "new-plate.png", (60, 20, 20), (320, 240))}])
    assert not load_project(root).plan.pages[0].panels[1].blocked


def test_jobs_say_which_references_did_not_fit(tmp_path):
    uploads = [{"path": _png(tmp_path / f"s{i}.png", (i * 40, 0, 0)), "role": "style"} for i in range(5)]
    _config_, _root, planned = _book(tmp_path, images=uploads)
    style = next(job for job in planned["jobs"] if job["key"] == "style")
    assert len(style["reference_image_paths"]) == 4
    assert len(style["dropped_references"]) == 1


def test_draw_still_waits_for_sheets(tmp_path):
    config, root, _planned = _book(tmp_path)
    assert draw(root, config)["status"] == "blocked"
