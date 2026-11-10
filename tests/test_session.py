from pathlib import Path

from PIL import Image

from brahma.config import AppConfig, ImageConfig, ScriptConfig
from brahma.errors import BrahmaError
from brahma.script import parse_plan
from brahma.session import assemble, configure, draw, plan, refs
from brahma.settings import ProjectSettings, save_settings


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        workspace_dir=tmp_path,
        script=ScriptConfig(backend="mock"),
        image=ImageConfig(backend="cursor"),
    )


def _png(path, color=(20, 40, 80)) -> str:
    Image.new("RGB", (32, 32), color).save(path)
    return str(path)


def test_cursor_jobs_lock_the_panel_to_the_sheet(tmp_path):
    config = _config(tmp_path)
    planned = plan("El detective cruzó la calle. La lluvia no paraba. Ana miró el reloj.", config)
    assert planned["status"] == "needs_images"
    keys = {job["key"] for job in planned["jobs"]}
    assert "style" in keys and "character:protagonista" in keys and "location:escena" in keys
    project = planned["project"]

    made = {}
    for job in planned["jobs"]:
        made[job["key"]] = _png(tmp_path / f"{job['key'].replace(':', '_')}.png")
    stored = refs(project, config, images=[{"key": key, "path": path} for key, path in made.items()])
    assert stored["status"] == "ok"

    pending = draw(project, config)
    assert pending["status"] == "needs_images"
    person = next(job for job in pending["jobs"] if job["key"] == "p01_02")
    empty = next(job for job in pending["jobs"] if job["key"] == "p01_01")
    assert person["reference_image_paths"][0].endswith("protagonista.png")
    assert empty["reference_image_paths"][0].endswith("escena.png")
    assert all("p01_" not in path for path in person["reference_image_paths"])
    assert "ink-blue coat" in person["description"]
    assert person["aspect_ratio"] in {"1:1", "4:3", "3:4", "16:9", "9:16"}

    panels = [{"key": job["key"], "path": _png(tmp_path / f"{job['key']}.png", (90, 10, 10))} for job in pending["jobs"]]
    done = draw(project, config, images=panels)
    assert done["status"] == "ok"
    pages = assemble(project, dialogue=[{"panel": "p01_02", "text": "Otra frase."}])
    assert pages["pages"][0].endswith(".ora")
    assert pages["previews"][0].endswith(".png")


def test_each_story_gets_its_own_folder_and_settings(tmp_path):
    config = _config(tmp_path)
    setup = {"art": "inked manga", "palette": "cobalt blue and bone white"}
    first = plan("El detective cruzó la calle.", config, setup)
    second = plan("El detective cruzó la calle.", config, setup)
    assert Path(first["project"]).parent == tmp_path
    assert Path(second["project"]).parent == tmp_path
    assert first["folder"] != second["folder"]
    assert first["settings"]["art"] == "inked manga"
    assert first["settings"]["palette"] == "cobalt blue and bone white"
    assert "cobalt blue and bone white" in first["jobs"][0]["description"]
    assert "inked graphic novel" not in first["jobs"][0]["description"]

    root = Path(first["project"])
    current = ProjectSettings.model_validate(first["settings"])
    save_settings(root, current.model_copy(update={"palette": "rust red and olive"}))
    again = refs(root, config)
    assert "rust red and olive" in again["jobs"][0]["description"]


def test_setup_rejects_unknown_keys(tmp_path):
    try:
        plan("Una calle bajo la lluvia.", _config(tmp_path), {"mood": "dark"})
    except BrahmaError as exc:
        assert "mood" in str(exc)
    else:
        raise AssertionError("unknown setup key was accepted")


def test_a_graphic_reference_is_kept_and_sent_with_jobs(tmp_path):
    config = _config(tmp_path)
    upload = _png(tmp_path / "foto.png", (12, 80, 40))
    planned = plan("El detective cruzó la calle.", config, images=[{"path": upload, "role": "style"}])
    assert planned["status"] == "needs_images"
    stored = Path(planned["project"]) / "references" / "style-01.png"
    assert stored.is_file()
    assert str(stored) in planned["jobs"][0]["reference_image_paths"]
    assert planned["settings"]["references"][0]["role"] == "style"


def test_a_hand_drawing_is_written_into_settings(tmp_path):
    config = _config(tmp_path)
    upload = _png(tmp_path / "boceto.png", (200, 180, 40))
    reading = {
        "art": "pencil and watercolor",
        "palette": "ochre paper, cobalt wash, graphite gray",
        "line": "soft pencil contour",
        "rendering": "thin watercolor washes",
        "lighting": "flat drawn light",
        "notes": "paper grain stays visible",
    }
    planned = plan(
        "El detective cruzó la calle.",
        config,
        {"palette": "the person asked for ink black"},
        [{"path": upload, "role": "dibujo", "reading": reading}],
    )
    assert planned["settings"]["art"] == "pencil and watercolor"
    assert planned["settings"]["palette"] == "the person asked for ink black"
    assert planned["settings"]["notes"] == "paper grain stays visible"
    assert "paper grain stays visible" in planned["jobs"][0]["description"]
    assert str(Path(planned["project"]) / "references" / "sketch-01.png") in planned["jobs"][0]["reference_image_paths"]


def test_a_sketch_without_a_reading_waits(tmp_path):
    config = _config(tmp_path)
    upload = _png(tmp_path / "boceto.png")
    planned = plan("Una calle bajo la lluvia.", config, images=[{"path": upload, "role": "sketch"}])
    assert planned["status"] == "needs_reading"
    assert "jobs" not in planned
    root = planned["project"]
    done = configure(
        root,
        config,
        {
            "art": "ballpoint pen",
            "palette": "blue ink on warm paper",
            "line": "nervous ballpoint",
            "rendering": "no fill, only line",
            "lighting": "no modeled light",
            "notes": "the paper is left blank",
        },
    )
    assert done["status"] == "needs_images"
    assert done["settings"]["palette"] == "blue ink on warm paper"
    assert done["settings"]["references"][0]["applied"] is True
    assert "blue ink on warm paper" in done["jobs"][0]["description"]


def test_draw_waits_for_references(tmp_path):
    config = _config(tmp_path)
    project = plan("Una sola frase bajo la lluvia.", config)["project"]
    blocked = draw(project, config)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "references_missing"


def test_plan_parser_ignores_a_think_block():
    payload = """<think>draft</think>
{"title":"Lluvia","logline":"Alguien llega.","language":"es","style":{"medium":"inked graphic novel","line":"clear ink line","rendering":"flat color","palette":"teal and amber","lighting":"overcast daylight"},"characters":[],"locations":[{"id":"calle","name":"calle","appearance":"a wet narrow street at night","lighting":"sodium lamps"}],"pages":[{"number":1,"panels":[{"shot":"wide","angle":"eye-level","location_id":"calle","character_ids":[],"action":"rain on the empty street","dialogue":[{"speaker":"narrator","text":"Llovía."}]}]}]}
"""
    plan_model = parse_plan(payload)
    assert plan_model.title == "Lluvia"
