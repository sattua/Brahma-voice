"""OpenAI-compatible script client.

Ollama, LM Studio, vLLM and hosted gateways all speak this. The default model
in the example config is Qwen2.5 14B, which fits a 16 GB GPU when ComfyUI is idle.
"""

from __future__ import annotations

import os

import httpx

from brahma.errors import BrahmaError
from brahma.models import StoryPlan
from brahma.script import SYSTEM_PROMPT, ScriptModel, detect_language, parse_plan
from brahma.settings import setup_note


class OpenAIScript(ScriptModel):
    name = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key_env: str | None = None,
        timeout_s: float = 180.0,
        temperature: float = 0.3,
        think: bool = False,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.think = think
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        kwargs: dict = {"base_url": self.base_url, "timeout": timeout, "headers": self._headers()}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _headers(self) -> dict[str, str]:
        if not self.api_key_env:
            return {}
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise BrahmaError(f"environment variable {self.api_key_env} is not set")
        return {"Authorization": f"Bearer {key}"}

    def health(self) -> tuple[bool, str]:
        try:
            self._headers()
        except BrahmaError as exc:
            return False, str(exc)
        try:
            with self._client(5.0) as client:
                response = client.get("/models")
        except httpx.HTTPError as exc:
            return False, f"script model not reachable at {self.base_url}: {exc}"
        if not response.is_success:
            return False, f"script model HTTP {response.status_code}"
        return True, f"{self.model} at {self.base_url}"

    def plan(self, story: str, setup: dict | None = None) -> StoryPlan:
        ok, detail = self.health()
        if not ok:
            raise BrahmaError(detail)
        language = detect_language(story)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Language: {language}\n\nStory:\n{story.strip()}{setup_note(setup)}"},
        ]
        raw = self._complete(messages, json_mode=True)
        try:
            return parse_plan(raw)
        except BrahmaError as first:
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"That JSON was rejected: {first}. Return only a corrected JSON object.",
                }
            )
            return parse_plan(self._complete(messages, json_mode=True))

    def _complete(self, messages: list[dict[str, str]], *, json_mode: bool) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": 8000,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload["think"] = self.think
        try:
            with self._client(self.timeout_s) as client:
                response = client.post("/chat/completions", json=payload)
                if response.status_code == 400:
                    payload.pop("response_format", None)
                    payload.pop("think", None)
                    response = client.post("/chat/completions", json=payload)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"].get("content")
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise BrahmaError(f"script request failed: {exc}") from exc
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise BrahmaError("script model returned an empty message")
        return content
