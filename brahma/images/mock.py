"""Deterministic stand-in. Same recipe, same pixels. A new reference changes the pixels."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from PIL import Image, ImageDraw

from brahma.consistency import ImageRecipe
from brahma.images.base import ImageGenerator, ImageHealth


def _color(seed: int, salt: bytes) -> tuple[int, int, int]:
    digest = hashlib.sha256(struct.pack(">I", seed) + salt).digest()
    return digest[0], digest[1], digest[2]


class MockImages(ImageGenerator):
    name = "mock"

    def health(self) -> ImageHealth:
        return ImageHealth(self.name, True, "mock images, no GPU")

    def generate(self, recipe: ImageRecipe, out_path: Path) -> None:
        salt = recipe.prompt.encode() + b"\0" + recipe.negative_prompt.encode()
        for reference in recipe.references:
            salt += b"\0" + reference.role.encode() + reference.path.name.encode() + struct.pack(">f", reference.weight)
        image = Image.new("RGB", (recipe.width, recipe.height), _color(recipe.seed, salt))
        draw = ImageDraw.Draw(image)
        draw.rectangle([6, 6, recipe.width - 7, recipe.height - 7], outline=(20, 20, 20), width=6)
        accent = _color(recipe.seed, salt + b"accent")
        draw.rectangle([0, 0, recipe.width, 28], fill=accent)
        label = f"{recipe.kind} {recipe.key}"[:80]
        draw.text((10, 8), label, fill=(255, 255, 255))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path, format="PNG")
