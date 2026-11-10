"""Image backend contract.

A request already carries the locks: prompt, negative, seed, sampler, and at
most one reference image. Backends do not invent style.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from brahma.consistency import ImageRecipe


@dataclass
class ImageHealth:
    backend: str
    available: bool
    detail: str = ""


class ImageGenerator(ABC):
    name: str = "image"

    @abstractmethod
    def health(self) -> ImageHealth: ...

    @abstractmethod
    def generate(self, recipe: ImageRecipe, out_path: Path) -> None: ...
