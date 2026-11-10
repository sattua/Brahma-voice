"""Which models to call. Quality knobs that must stay identical across a story live here too."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from brahma.consistency import SamplerLock
from brahma.errors import BrahmaError
from brahma.images.base import ImageGenerator
from brahma.images.comfyui import ComfyUIImages
from brahma.images.cursor import CursorImages
from brahma.images.mock import MockImages
from brahma.llm import OpenAIScript
from brahma.script import MockScript, ScriptModel

REPO_ROOT = Path(__file__).resolve().parents[1]


class ScriptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = "mock"
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5:14b"
    api_key_env: str | None = None
    timeout_s: float = 180.0
    temperature: float = 0.3
    think: bool = False
    vision_model: str | None = None


class ImageConfig(BaseModel):
    """Sampler defaults are the SDXL settings already used on this machine by graphic-harness."""

    model_config = ConfigDict(extra="forbid")

    backend: str = "mock"
    comfyui_url: str = "http://127.0.0.1:8188"
    workflow: str = "sdxl"
    ckpt_name: str = "sd_xl_base_1.0.safetensors"
    ipadapter_file: str = "ip-adapter-plus_sdxl_vit-h.safetensors"
    clip_vision: str = "CLIP-ViT-H-14.safetensors"
    steps: int = Field(default=30, ge=1, le=80)
    cfg: float = Field(default=6.5, ge=1.0, le=20.0)
    sampler: str = "dpmpp_2m"
    scheduler: str = "karras"
    timeout_s: float = 600.0


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_dir: Path = REPO_ROOT / "workspace"
    script: ScriptConfig = Field(default_factory=ScriptConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)


def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    chosen = path or os.environ.get("BRAHMA_CONFIG")
    if chosen is None and (REPO_ROOT / "brahma.config.json").is_file():
        chosen = REPO_ROOT / "brahma.config.json"
    if not chosen:
        return AppConfig()
    file = Path(chosen)
    if not file.is_file():
        raise BrahmaError(f"config not found: {file}")
    try:
        config = AppConfig.model_validate(json.loads(file.read_text(encoding="utf-8")))
    except Exception as exc:
        raise BrahmaError(f"invalid config {file}: {exc}") from exc
    if not config.workspace_dir.is_absolute():
        config.workspace_dir = (REPO_ROOT / config.workspace_dir).resolve()
    return config


def script_model(config: AppConfig) -> ScriptModel:
    if config.script.backend == "mock":
        return MockScript()
    if config.script.backend == "openai":
        return OpenAIScript(
            config.script.base_url,
            config.script.model,
            api_key_env=config.script.api_key_env,
            timeout_s=config.script.timeout_s,
            temperature=config.script.temperature,
            think=config.script.think,
        )
    raise BrahmaError(f"unknown script backend {config.script.backend}")


def image_model(config: AppConfig) -> ImageGenerator:
    if config.image.backend == "mock":
        return MockImages()
    if config.image.backend == "comfyui":
        return ComfyUIImages(config.image.comfyui_url, timeout_s=config.image.timeout_s)
    if config.image.backend == "cursor":
        return CursorImages()
    raise BrahmaError(f"unknown image backend {config.image.backend}")


def sampler_lock(config: AppConfig) -> SamplerLock:
    image = config.image
    return SamplerLock(
        ckpt=image.ckpt_name,
        steps=image.steps,
        cfg=image.cfg,
        sampler=image.sampler,
        scheduler=image.scheduler,
        workflow=image.workflow,
        ipadapter=image.ipadapter_file,
        clip_vision=image.clip_vision,
    )
