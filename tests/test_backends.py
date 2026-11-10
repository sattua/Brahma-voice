import io
import json

import httpx
from PIL import Image

from brahma.consistency import ImageRecipe, Reference, SamplerLock
from brahma.images.comfyui import ComfyUIImages, fill_workflow
from brahma.llm import OpenAIScript
from brahma.script import SYSTEM_PROMPT


def _sampler() -> SamplerLock:
    return SamplerLock("ckpt.safetensors", 30, 6.5, "dpmpp_2m", "karras", "sdxl_ipadapter", "ip.safetensors", "clip.safetensors")


def _recipe(path) -> ImageRecipe:
    return ImageRecipe(
        key="p01_01",
        kind="panel",
        prompt="a single panel",
        negative_prompt="text",
        width=64,
        height=64,
        seed=7,
        references=(Reference("character", "ana", path, 0.85),),
        sampler=_sampler(),
    )


def test_vision_model_reads_a_drawing(tmp_path):
    from brahma.config import AppConfig, ScriptConfig
    from brahma.vision import read_drawing

    image = tmp_path / "draw.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(image)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["parts"] = body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"art": "pencil sketch", "palette": "gray paper", "line": "thin pencil", "rendering": "unfilled", "lighting": "flat", "notes": "blank background"}'}}]},
        )

    config = AppConfig(script=ScriptConfig(vision_model="qwen-vl", base_url="http://vision.test/v1"))
    reading = read_drawing(image, config, transport=httpx.MockTransport(handler))
    assert reading["art"] == "pencil sketch"
    assert reading["palette"] == "gray paper"
    assert any(part.get("type") == "image_url" for part in seen["parts"])


def test_workflow_placeholders_keep_numbers_as_numbers():
    filled = fill_workflow({"seed": "{{seed}}", "cfg": "{{cfg}}", "text": "seed {{seed}}"}, {"seed": 7, "cfg": 6.5})
    assert filled == {"seed": 7, "cfg": 6.5, "text": "seed 7"}


def test_comfy_uploads_the_one_reference_and_stores_a_png(tmp_path):
    ref = tmp_path / "ana.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(ref)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={})
        if request.url.path == "/upload/image":
            seen["upload"] = request.headers.get("content-type", "")
            return httpx.Response(200, json={"name": "ana.png"})
        if request.url.path == "/prompt":
            seen["prompt"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "abc"})
        if request.url.path == "/history/abc":
            return httpx.Response(
                200,
                json={"abc": {"outputs": {"11": {"images": [{"filename": "brahma_00001_.png", "type": "output"}]}}}},
            )
        if request.url.path == "/view":
            buffer = io.BytesIO()
            Image.new("RGB", (4, 4), (9, 9, 9)).save(buffer, format="PNG")
            return httpx.Response(200, content=buffer.getvalue())
        return httpx.Response(404)

    backend = ComfyUIImages("http://comfy.test", poll_s=0, transport=httpx.MockTransport(handler))
    out = tmp_path / "panel.png"
    backend.generate(_recipe(ref), out)
    assert "multipart/form-data" in seen["upload"]
    prompt = seen["prompt"]["prompt"]
    assert prompt["4"]["inputs"]["image"] == "ana.png"
    assert prompt["5"]["inputs"]["weight"] == 0.85
    assert prompt["9"]["inputs"]["seed"] == 7
    assert prompt["9"]["inputs"]["model"] == ["5", 0]
    assert Image.open(out).size == (4, 4)


def test_script_model_repairs_an_invalid_plan():
    valid = {
        "title": "Lluvia",
        "logline": "Alguien llega.",
        "language": "es",
        "style": {
            "medium": "inked graphic novel",
            "line": "clear ink line",
            "rendering": "flat color",
            "palette": "teal and amber",
            "lighting": "overcast daylight",
        },
        "characters": [
            {
                "id": "ana",
                "name": "Ana",
                "role": "protagonist",
                "appearance": "woman in her 30s with short black hair and brown eyes",
                "wardrobe": "ochre coat",
                "distinguishing": "a scar through the left eyebrow",
            }
        ],
        "locations": [
            {
                "id": "calle",
                "name": "calle",
                "appearance": "a wet narrow street at night",
                "lighting": "sodium lamps",
            }
        ],
        "pages": [
            {
                "number": 1,
                "panels": [
                    {
                        "shot": "close",
                        "angle": "eye-level",
                        "location_id": "calle",
                        "character_ids": ["ana"],
                        "action": "she stops under a lamp",
                        "dialogue": [{"speaker": "Ana", "text": "Llegué tarde."}],
                    }
                ],
            }
        ],
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content)
        calls["n"] += 1
        if calls["n"] == 1:
            assert body["response_format"] == {"type": "json_object"}
            assert SYSTEM_PROMPT[:40] in body["messages"][0]["content"]
            content = '{"title": "no"}'
        else:
            assert "rejected" in body["messages"][-1]["content"]
            content = json.dumps(valid)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    model = OpenAIScript("http://llm.test/v1", "qwen2.5:14b", transport=httpx.MockTransport(handler))
    plan = model.plan("Ana llegó tarde a la calle.")
    assert calls["n"] == 2
    assert plan.pages[0].panels[0].dialogue[0].speaker == "ana"
    assert plan.pages[0].panels[0].dialogue[0].text == "Llegué tarde."
