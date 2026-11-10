import io
import json
import zipfile

from PIL import Image

from brahma.consistency import SamplerLock
from brahma.images.mock import MockImages
from brahma.layout import frames_for
from brahma.openraster import ORA_MIME
from brahma.pipeline import assemble_pages, draw_panels, generate_references, make_story
from brahma.script import MockScript
from brahma.store import load_project


def _sampler(workflow: str = "sdxl") -> SamplerLock:
    return SamplerLock(
        ckpt="sd_xl_base_1.0.safetensors",
        steps=30,
        cfg=6.5,
        sampler="dpmpp_2m",
        scheduler="karras",
        workflow=workflow,
        ipadapter="ip-adapter-plus_sdxl_vit-h.safetensors",
        clip_vision="CLIP-ViT-H-14.safetensors",
    )


def test_frames_cover_every_panel_count_without_leaving_the_page():
    for count in range(1, 7):
        frames = frames_for(count)
        assert len(frames) == count
        for frame in frames:
            assert frame.width > 64 and frame.height > 64
            assert frame.x >= 0 and frame.y >= 0
            assert frame.x + frame.width <= 1800
            assert frame.y + frame.height <= 2700


def test_make_exports_a_layered_page_and_locks_identity(tmp_path):
    root = make_story(
        "El detective cruzó la calle. La lluvia no paraba. Ana miró el reloj. Luego entró.",
        MockScript(),
        MockImages(),
        _sampler("sdxl_ipadapter"),
        tmp_path,
        log=lambda *_args: None,
    )
    project = load_project(root)
    ora = root / "pages" / "page-01.ora"
    assert ora.is_file()
    assert (root / "pages" / "page-01.png").is_file()

    with zipfile.ZipFile(ora) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == ORA_MIME.encode()
        xml = archive.read("stack.xml").decode()
        assert "lettering" in xml and "art" in xml and "paper" in xml
        art = archive.read("data/panel-01-art.png")
        lettering = archive.read("data/panel-01-lettering.png")
        assert art != lettering
        assert Image.open(io.BytesIO(lettering)).mode == "RGBA"

    empty = json.loads((root / "panels" / "p01_01.json").read_text())
    person = json.loads((root / "panels" / "p01_02.json").read_text())
    sheet = json.loads((root / "refs" / "characters" / "protagonista.json").read_text())
    assert empty["references"][0]["role"] == "location"
    assert person["references"][0]["role"] == "character"
    assert person["references"][0]["weight"] == 0.85
    assert "p01_01" not in person["references"][0]["path"]
    assert person["references"][0]["path"].endswith("protagonista.png")
    assert sheet["sampler"]["workflow"] == "sdxl"
    assert person["sampler"]["workflow"] == "sdxl_ipadapter"
    assert sheet["prompt"]
    appearance = project.plan.characters[0].appearance
    assert appearance in sheet["prompt"]
    assert appearance in person["prompt"]
    assert empty["sampler"]["steps"] == 30
    assert empty["sampler"]["cfg"] == 6.5


def test_dialogue_edits_rebuild_lettering_without_redrawing(tmp_path):
    root = make_story(
        "El detective cruzó la calle. La lluvia seguía.",
        MockScript(),
        MockImages(),
        _sampler(),
        tmp_path,
        log=lambda *_args: None,
    )
    before = _layer(root / "pages" / "page-01.ora", "data/panel-02-lettering.png")
    art_before = (root / "panels" / "p01_02.png").read_bytes()

    project = load_project(root)
    project.plan.pages[0].panels[1].dialogue[0].text = "Otra frase, el mismo dibujo."
    from brahma.store import save_project

    save_project(root, project)
    assemble_pages(root, log=lambda *_args: None)
    assert (root / "panels" / "p01_02.png").read_bytes() == art_before
    after = _layer(root / "pages" / "page-01.ora", "data/panel-02-lettering.png")
    assert before != after


def test_redraw_one_panel_changes_its_seed_only(tmp_path):
    images = MockImages()
    root = make_story(
        "Una frase. Otra frase. Y una más.",
        MockScript(),
        images,
        _sampler(),
        tmp_path,
        log=lambda *_args: None,
    )
    first = (root / "panels" / "p01_02.png").read_bytes()
    other = (root / "panels" / "p01_01.png").read_bytes()
    draw_panels(root, images, _sampler(), panel="p01_02", log=lambda *_args: None)
    assert (root / "panels" / "p01_02.png").read_bytes() != first
    assert (root / "panels" / "p01_01.png").read_bytes() == other
    assert load_project(root).revision("p01_02") == 2


def test_force_refs_do_not_touch_panels(tmp_path):
    images = MockImages()
    root = make_story("Solo una frase larga para el plano.", MockScript(), images, _sampler(), tmp_path, log=lambda *_args: None)
    panel = (root / "panels" / "p01_01.png").read_bytes()
    generate_references(root, images, _sampler(), force=True, log=lambda *_args: None)
    assert (root / "panels" / "p01_01.png").read_bytes() == panel
    assert load_project(root).revision("character:protagonista") == 2


def _layer(path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)
