"""Cursor draws the pixels. This backend only reports that the jobs are ready."""

from __future__ import annotations

from pathlib import Path

from brahma.consistency import ImageRecipe
from brahma.errors import BrahmaError
from brahma.images.base import ImageGenerator, ImageHealth


class CursorImages(ImageGenerator):
    name = "cursor"

    def health(self) -> ImageHealth:
        return ImageHealth(self.name, True, "Cursor's image tool draws each job; reference_image_paths carry identity")

    def generate(self, recipe: ImageRecipe, out_path: Path) -> None:
        raise BrahmaError("Cursor draws this image from the job list. There is no local generator to call.")
