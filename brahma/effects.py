"""Effects are a filter, never part of a base picture.

Sparks, glow, particles, fireflies, lens flares, light rays, fog, mist, haze, rain, snow,
smoke, and embers are painted over an accepted picture as a separate step, into a separate
file. A base picture (style sample, sheet, turnaround, pose sheet, place, panel) never asks
for them, and its negative always forbids them. Text that names one is split: the effect
leaves the base prompt and is kept as an effect, so it can be asked for in the filter.

A fragment that negates an effect ("not a glow", "no fog") stays where it is: it forbids.
"""

from __future__ import annotations

import re

# Adjectives come out of the phrase and leave the rest: "a rainy street" is "a street".
_ADJECTIVES = (
    r"rainy|foggy|misty|hazy|smoky|snowy|glowing|sparkling|shimmering|glittering|"
    r"brillando|humeante|lluviosa?|nevad[oa]|brumos[oa]"
)
# Nouns take their phrase with them: "golden hour haze" leaves the lighting.
_NOUNS = (
    r"sparks?|sparkles?|glows?|glowed|particles?|firefl(?:y|ies)|bokeh|lens flares?|"
    r"light (?:rays|shafts|beams)|god rays|sun ?rays|volumetric (?:light|fog|rays)|light bloom|"
    r"fog|mist|haze|rain(?:fall|drops?)?|snow(?:fall|flakes?)?|smoke|embers?|dust motes|floating dust|"
    r"chispas?|luci[eé]rnagas?|niebla|neblina|bruma|lluvia|nieve|humo|part[ií]culas?|destellos?|resplandor"
)
_ADJECTIVE = re.compile(rf"\b(?:{_ADJECTIVES})\b", re.IGNORECASE)
_NOUN = re.compile(rf"\b(?:{_NOUNS})\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:no|not|without|never|sin|nunca|ni)\b", re.IGNORECASE)
_CONNECTOR = re.compile(r"(\s+(?:with|and|plus|y|con)\s+)", re.IGNORECASE)
_FRAGMENT = re.compile(r"([,.;\n])")

EFFECTS_NEGATIVE = (
    "floating particles, sparks, glowing specks, fireflies, bokeh, lens flare, light rays, "
    "fog, mist, haze, rain, snow, smoke, embers"
)


def has_effect(text: str) -> bool:
    return bool(_NOUN.search(text) or _ADJECTIVE.search(text))


def _negated(text: str) -> bool:
    return bool(_NEGATION.search(text))


def _tidy(text: str) -> str:
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;])", r"\1", text)
    text = re.sub(r"([,;])\s*(?=[,.;])", "", text)
    return text.strip().strip(",;").strip()


def _split_fragment(fragment: str) -> tuple[str, list[str]]:
    """One comma-free fragment. Returns what stays and the effects it named."""

    if not has_effect(fragment) or _negated(fragment):
        return fragment, []
    parts = _CONNECTOR.split(fragment)
    kept: list[str] = []
    moved: list[str] = []
    for index in range(0, len(parts), 2):
        part = parts[index]
        connector = parts[index - 1] if index > 0 else ""
        if _NOUN.search(part) and not _negated(part):
            moved.append(part.strip())
            continue
        if _ADJECTIVE.search(part) and not _negated(part):
            moved.append(part.strip())
            part = _ADJECTIVE.sub("", part)
            if not part.strip():
                continue
        if kept:
            kept.append(connector)
        kept.append(part)
    clean = _tidy("".join(kept))
    if not clean:
        clean = _tidy(_ADJECTIVE.sub("", _NOUN.sub("", fragment)))
        if not re.search(r"[a-záéíóúñ]{3,}", clean, re.IGNORECASE):
            clean = ""
    return clean, moved


def split_effects(text: str) -> tuple[str, list[str]]:
    """The text without effects, and the effects it named, in order."""

    if not text or not has_effect(text):
        return text or "", []
    pieces = _FRAGMENT.split(text)
    out: list[str] = []
    moved: list[str] = []
    for index in range(0, len(pieces), 2):
        fragment = pieces[index]
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        clean, named = _split_fragment(fragment)
        moved.extend(named)
        if clean.strip():
            lead = " " if fragment[:1].isspace() else ""
            out.append(lead + clean + separator)
    return _tidy("".join(out)), moved


def without_effects(text: str) -> str:
    return split_effects(text)[0]


def effects_in(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for item in split_effects(text or "")[1]:
            if item not in found:
                found.append(item)
    return found


_SHOT_WORDS = re.compile(
    r"\b(?:extreme\s+)?(?:close[- ]?ups?|wide shots?|medium shots?|long shots?|full shots?|"
    r"establishing shots?|macro shots?|primer plano|plano abierto|plano general)\b",
    re.IGNORECASE,
)


def without_shot_words(text: str) -> str:
    """The camera belongs to the shot, not to the look."""

    return _tidy(_SHOT_WORDS.sub("", text or ""))


def fragments_without(text: str, pattern: str) -> str:
    """Drop the comma fragments of text that match pattern. Keeps text when nothing is left."""

    pieces = [piece.strip() for piece in re.split(r"[,;]", text or "") if piece.strip()]
    kept = [piece for piece in pieces if not re.search(pattern, piece, re.IGNORECASE)]
    return ", ".join(kept) if kept else (text or "")
