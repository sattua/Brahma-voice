"""Speech balloons as their own transparent layer, never baked into the art."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from brahma.models import DialogueLine

_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONTS:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def render_lettering(width: int, height: int, lines: list[DialogueLine]) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not lines:
        return image
    draw = ImageDraw.Draw(image)
    font = _font(28 if width >= 500 else 20)
    small = _font(16 if width >= 500 else 13)
    max_text = int(width * 0.72)
    y = 18
    for line in lines:
        wrapped = _wrap(line.text, font, max_text, draw)
        line_h = 32 if width >= 500 else 24
        text_h = line_h * len(wrapped)
        label_h = 18 if line.speaker != "narrator" else 0
        box_w = max(int(draw.textlength(row, font=font)) for row in wrapped) + 36
        box_w = min(width - 24, max(box_w, 120))
        box_h = text_h + label_h + 28
        x = 16
        if y + box_h > height - 8:
            break
        if line.speaker == "narrator":
            draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=4, fill=(255, 248, 230, 235), outline=(30, 30, 30, 255), width=2)
            text_y = y + 12
        else:
            draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=18, fill=(255, 255, 255, 240), outline=(20, 20, 20, 255), width=3)
            tail_y = y + box_h
            draw.polygon([(x + 36, tail_y - 2), (x + 54, tail_y - 2), (x + 28, tail_y + 16)], fill=(255, 255, 255, 240))
            draw.text((x + 16, y + 8), line.speaker, font=small, fill=(70, 70, 70, 255))
            text_y = y + 8 + label_h
        for row in wrapped:
            draw.text((x + 16, text_y), row, font=font, fill=(15, 15, 15, 255))
            text_y += line_h
        y += box_h + 10
    return image
