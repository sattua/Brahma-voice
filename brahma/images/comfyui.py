"""ComfyUI adapter. The server and the models are already installed; this only queues a workflow.

``sdxl`` is stock ComfyUI: the locks live in the prompt.
``sdxl_ipadapter`` uploads the one reference image and conditions on it.
That workflow needs the ComfyUI_IPAdapter_plus node pack.
"""

from __future__ import annotations

import io
import json
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image

from brahma.consistency import ImageRecipe
from brahma.errors import BrahmaError
from brahma.images.base import ImageGenerator, ImageHealth

WORKFLOWS = Path(__file__).parent / "workflows"


def resolve_workflow(name: str) -> Path:
    candidate = Path(name)
    if candidate.is_file():
        return candidate
    builtin = WORKFLOWS / f"{name}.json"
    if builtin.is_file():
        return builtin
    raise BrahmaError(f"workflow not found: {name}")


def fill_workflow(workflow: dict, values: dict[str, object]) -> dict:
    def fill(node: object) -> object:
        if isinstance(node, dict):
            return {key: fill(value) for key, value in node.items()}
        if isinstance(node, list):
            return [fill(value) for value in node]
        if isinstance(node, str):
            for key, value in values.items():
                token = "{{" + key + "}}"
                if node == token:
                    return value
                if token in node:
                    node = node.replace(token, str(value))
        return node

    return fill(workflow)


class ComfyUIImages(ImageGenerator):
    name = "comfyui"

    def __init__(self, url: str, *, timeout_s: float = 600.0, poll_s: float = 1.0, transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        kwargs: dict = {"base_url": self.url, "timeout": timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def health(self) -> ImageHealth:
        try:
            with self._client(5.0) as client:
                response = client.get("/system_stats")
        except httpx.HTTPError as exc:
            return ImageHealth(self.name, False, f"ComfyUI not reachable at {self.url}: {exc}")
        return ImageHealth(self.name, response.is_success, f"ComfyUI HTTP {response.status_code}")

    def generate(self, recipe: ImageRecipe, out_path: Path) -> None:
        health = self.health()
        if not health.available:
            raise BrahmaError(health.detail)
        workflow_path = resolve_workflow(recipe.sampler.workflow)
        raw = workflow_path.read_text()
        values: dict[str, object] = {
            "prompt": recipe.prompt,
            "negative_prompt": recipe.negative_prompt,
            "seed": recipe.seed,
            "width": recipe.width,
            "height": recipe.height,
            "ckpt": recipe.sampler.ckpt,
            "steps": recipe.sampler.steps,
            "cfg": recipe.sampler.cfg,
            "sampler": recipe.sampler.sampler,
            "scheduler": recipe.sampler.scheduler,
            "ipadapter": recipe.sampler.ipadapter,
            "clip_vision": recipe.sampler.clip_vision,
        }
        try:
            with self._client(30.0) as client:
                if "{{ref_image}}" in raw:
                    if not recipe.references:
                        raise BrahmaError(f"{workflow_path.name} needs a reference image and this recipe has none")
                    reference = recipe.references[0]
                    values["ref_image"] = self._upload(client, reference.path)
                    values["ref_weight"] = reference.weight
                workflow = fill_workflow(json.loads(raw), values)
                queued = client.post("/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
                queued.raise_for_status()
                prompt_id = queued.json()["prompt_id"]
                image_ref = self._wait(client, prompt_id)
                data = client.get("/view", params=image_ref).raise_for_status().content
        except httpx.HTTPError as exc:
            raise BrahmaError(f"ComfyUI request failed: {exc}") from exc
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.open(io.BytesIO(data))
        image.save(out_path, format="PNG")

    def _upload(self, client: httpx.Client, path: Path) -> str:
        if not path.is_file():
            raise BrahmaError(f"reference image is missing: {path}")
        response = client.post(
            "/upload/image",
            files={"image": (path.name, path.read_bytes(), "image/png")},
            data={"overwrite": "true"},
        )
        response.raise_for_status()
        name = response.json().get("name")
        if not name:
            raise BrahmaError("ComfyUI upload did not return a filename")
        return name

    def _wait(self, client: httpx.Client, prompt_id: str) -> dict:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            history = client.get(f"/history/{prompt_id}").raise_for_status().json()
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise BrahmaError(f"ComfyUI workflow failed: {status.get('messages')}")
                for output in entry.get("outputs", {}).values():
                    for image in output.get("images", []):
                        return {
                            "filename": image["filename"],
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                        }
                if status.get("completed"):
                    raise BrahmaError("ComfyUI workflow finished without an image")
            time.sleep(self.poll_s)
        raise BrahmaError(f"ComfyUI timed out after {self.timeout_s:.0f}s")
