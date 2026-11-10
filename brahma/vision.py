"""Ask a vision model what a hand drawing looks like. No model is configured on this machine."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from brahma.config import AppConfig
from brahma.settings import clean_reading

_PROMPT = (
    "Look at this hand-drawn image. It is the graphic setup for a whole comic. "
    "Return one JSON object with keys art, palette, line, rendering, lighting, negative, notes. "
    "Use short English. art is the medium you see (pencil, ink, marker, watercolor). "
    "palette names the actual colors. line describes the stroke. rendering describes fill and texture. "
    "lighting describes how light is drawn. negative lists what the drawing is not. "
    "notes is one sentence a painter can follow. Do not invent a plot."
)


def read_drawing(path: Path, config: AppConfig, transport: httpx.BaseTransport | None = None) -> dict | None:
    model = (config.script.vision_model or "").strip()
    if not model:
        return None
    encoded = base64.standard_b64encode(path.read_bytes()).decode()
    payload = {
        "model": model,
        "temperature": 0.2,
        "think": False,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            }
        ],
    }
    kwargs: dict = {"base_url": config.script.base_url, "timeout": config.script.timeout_s}
    if transport is not None:
        kwargs["transport"] = transport
    try:
        with httpx.Client(**kwargs) as client:
            response = client.post("/chat/completions", json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"].get("content") or ""
        return clean_reading(json.loads(content))
    except Exception:
        return None
