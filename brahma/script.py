"""Turns a told story into a StoryPlan.

The script model plans. It does not draw. Visual clauses are English so the
image model gets one stable description; dialogue stays in the author's language.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

from brahma.errors import BrahmaError
from brahma.models import (
    MAX_PAGES,
    MAX_PANELS,
    CharacterSpec,
    DialogueLine,
    LocationSpec,
    PageSpec,
    PanelSpec,
    StoryPlan,
    StyleSpec,
)

SYSTEM_PROMPT = """You are the writer and art director of a graphic novel.
Break the user's story into a production plan. Return one JSON object and nothing else.

Rules:
- At most 8 pages and at most 6 panels per page. Compress the story to fit.
- At most 4 characters and 4 locations. Merge extras into these.
- ids are short lowercase ascii slugs.
- Names and dialogue.text stay in the language of the story.
- style, appearance, wardrobe, distinguishing, location appearance, location lighting, and panel action are English. They are sent to an image model and must stay stable and concrete.
- Do not ask the image model to draw letters, balloons, captions, or panel borders. Lettering is added later.
- shot is one of: extreme-close, close, medium, medium-wide, wide, establishing.
- angle is one of: eye-level, low, high, over-shoulder, dutch.
- Every location_id and character_id used in a panel must be defined.
- dialogue.speaker is "narrator" or a character id. At most 3 lines per panel.
- Page numbers are 1..N with no gaps.
- Character appearance describes the same person every time: age, face, hair, skin. Put clothes only in wardrobe. Put one or two permanent marks in distinguishing.
- Keep the palette, line, and rendering specific enough that two artists would paint the same picture.

Schema:
{
  "title": "string",
  "logline": "string",
  "language": "es",
  "style": {
    "medium": "inked graphic novel",
    "line": "clear ink line, strong silhouettes",
    "rendering": "flat color with soft cell shading",
    "palette": "muted teal, warm amber, ink black",
    "lighting": "overcast daylight",
    "negative": ""
  },
  "characters": [
    {
      "id": "ana",
      "name": "Ana",
      "role": "protagonist",
      "appearance": "woman in her 30s, short black hair, sharp jaw, brown eyes, light brown skin",
      "wardrobe": "ochre trench coat, dark turtleneck, worn leather satchel",
      "distinguishing": "thin scar through the left eyebrow"
    }
  ],
  "locations": [
    {
      "id": "station",
      "name": "estación",
      "appearance": "old train station hall, high iron arches, wet stone floor",
      "lighting": "cold overhead lamps"
    }
  ],
  "pages": [
    {
      "number": 1,
      "panels": [
        {
          "shot": "establishing",
          "angle": "eye-level",
          "location_id": "station",
          "character_ids": [],
          "action": "rain blowing through the open doors of an empty hall",
          "dialogue": [{"speaker": "narrator", "text": "La lluvia llevaba tres días."}],
          "continuity": ""
        }
      ]
    }
  ]
}
"""

_SPANISH = re.compile(r"\b(el|la|los|las|un|una|que|de|en|y|con|por|su|al|del|se)\b", re.I)
_SHOTS = ("establishing", "wide", "medium", "close", "medium-wide", "extreme-close")
_ANGLES = ("eye-level", "low", "high", "over-shoulder", "eye-level", "dutch")


def detect_language(story: str) -> str:
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", story)
    if not words:
        return "es"
    hits = sum(1 for word in words if _SPANISH.fullmatch(word))
    return "es" if hits >= max(2, len(words) // 12) else "en"


class ScriptModel(ABC):
    name: str = "script"

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...

    @abstractmethod
    def plan(self, story: str, setup: dict | None = None) -> StoryPlan: ...


def parse_plan(payload: str) -> StoryPlan:
    text = re.sub(r"<think>.*?</think>", "", payload, flags=re.DOTALL).strip()
    if not text.startswith("{") and "```" not in text[:20]:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BrahmaError(f"script model did not return JSON: {exc}") from exc
    try:
        return StoryPlan.model_validate(data)
    except Exception as exc:
        raise BrahmaError(f"script plan is not usable: {exc}") from exc


class MockScript(ScriptModel):
    """A stable plan for tests and for drawing before a language model is configured.

    Dialogue keeps the author's sentences. Visual clauses are fixed English so the
    image locks do not change between runs.
    """

    name = "mock"

    def health(self) -> tuple[bool, str]:
        return True, "mock script, no language model"

    def plan(self, story: str, setup: dict | None = None) -> StoryPlan:
        language = detect_language(story)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", story.strip()) if part.strip()]
        if not sentences:
            sentences = ["Una historia en silencio." if language == "es" else "A quiet story."]
        sentences = sentences[: MAX_PAGES * MAX_PANELS]
        pages: list[PageSpec] = []
        for start in range(0, len(sentences), 3):
            chunk = sentences[start : start + 3]
            panels = []
            for offset, sentence in enumerate(chunk):
                index = start + offset
                panels.append(
                    PanelSpec(
                        shot=_SHOTS[index % len(_SHOTS)],
                        angle=_ANGLES[index % len(_ANGLES)],
                        location_id="escena",
                        character_ids=["protagonista"] if index % 3 else [],
                        action=f"moment {index + 1}, the protagonist in the same place, continuing the scene",
                        dialogue=[DialogueLine(speaker="narrator" if index % 3 == 0 else "protagonista", text=sentence[:240])],
                        continuity="same person and same place as the reference sheets",
                    )
                )
            pages.append(PageSpec(number=len(pages) + 1, panels=panels))
        title = "Historia" if language == "es" else "Story"
        return StoryPlan(
            title=title,
            logline=sentences[0][:300],
            language=language,
            style=StyleSpec(
                medium="inked graphic novel",
                line="clear ink line with strong silhouettes",
                rendering="flat color with soft cell shading on paper",
                palette="muted teal, warm amber, ink black",
                lighting="overcast daylight with soft shadows",
            ),
            characters=[
                CharacterSpec(
                    id="protagonista",
                    name="Protagonista",
                    role="protagonist",
                    appearance="adult with a calm oval face, dark wavy hair, brown eyes, medium skin tone",
                    wardrobe="ink-blue coat with a high collar, charcoal trousers, scuffed boots",
                    distinguishing="a silver ring on the right index finger",
                )
            ],
            locations=[
                LocationSpec(
                    id="escena",
                    name="escena",
                    appearance="a rainy city street at night, narrow sidewalk, shuttered shops, wet asphalt",
                    lighting="sodium street lamps and cool blue shadows",
                )
            ],
            pages=pages,
        )
