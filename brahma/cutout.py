"""A figure drawn alone on flat green, cut out, and set into its box on the place.

An image model reframes whatever picture it is given, so a figure painted into a box on the
place comes back the wrong size or cut at the box edge. Drawn alone, full body, on flat green,
it can be keyed, scaled to its box, and set down with its feet on the bottom edge. Outside the
figure and its shadow the place does not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from brahma.errors import BrahmaError

GREEN = (0, 255, 0)
# How much greener than red and blue a pixel has to be to start turning transparent, and to be gone.
KEY_SOFT = 28
KEY_HARD = 80
# The small darkening under the boots. The cast shadow is separate and follows the light.
CONTACT_OPACITY = 0.35
# How far the figure takes the color cast of the place around its box.
TINT = 0.7
# How far a figure may be darkened or brightened to match itself in the previous panel.
MIN_GAIN = 0.3
MAX_GAIN = 1.6


@dataclass(frozen=True)
class CastShadow:
    """The shadow each figure throws on the ground, as the camera sees it.

    back is its length on the picture as a share of the figure's height: positive runs away from
    the camera, up the picture (the light is on the camera's side); negative comes toward it.
    lean moves its far end sideways by that share of the figure's height; negative is the left
    of the picture. strength is how dark it is at the feet, 0 to 1."""

    back: float = 0.3
    lean: float = 0.0
    strength: float = 0.55


def cast_shadow(silhouette: Image.Image, shadow: CastShadow) -> tuple[Image.Image, int, int]:
    """The silhouette laid on the ground: the mask, and its top-left from the figure's left at the feet line."""

    width, height = silhouette.size
    length = max(2, round(abs(shadow.back) * height))
    side = round(shadow.lean * height)
    lead = max(0, -side)
    ratio = height / length
    if shadow.back >= 0:
        data = (1, side / length, -lead - side, 0, ratio, 0)
        top = -length
    else:
        data = (1, -side / length, -lead, 0, -ratio, height - 1)
        top = 0
    size = (width + abs(side), length)
    mask = silhouette.transform(size, Image.Transform.AFFINE, data, resample=Image.Resampling.BILINEAR)
    far = Image.linear_gradient("L").resize(size)
    if shadow.back >= 0:
        far = ImageChops.invert(far)
    sharp = mask.filter(ImageFilter.GaussianBlur(max(0.6, height * 0.004)))
    soft = mask.filter(ImageFilter.GaussianBlur(max(1.2, height * 0.018)))
    mask = Image.composite(soft, sharp, far)
    fade = far.point(lambda value: round(255 * shadow.strength * (1 - 0.5 * value / 255)))
    return ImageChops.multiply(mask, fade), -lead, top


def key_green(image: Image.Image) -> Image.Image:
    """RGBA with the green background transparent and green spill taken off the edges."""

    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    strongest = ImageChops.lighter(red, blue)
    excess = ImageChops.subtract(green, strongest)
    scale = 255 / (KEY_HARD - KEY_SOFT)
    alpha = excess.point(lambda value: 255 - max(0, min(255, round((value - KEY_SOFT) * scale))))
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.6))
    despilled = Image.merge("RGB", (red, ImageChops.darker(green, strongest), blue))
    out = despilled.convert("RGBA")
    out.putalpha(alpha)
    return out


def trim(figure: Image.Image) -> Image.Image:
    box = figure.getchannel("A").point(lambda value: 255 if value > 128 else 0).getbbox()
    if box is None:
        raise BrahmaError("the cutout is empty: the figure was not drawn on flat green")
    return figure.crop(box)


def match_scene(figure: Image.Image, around: Image.Image, tint: float = TINT) -> Image.Image:
    """Take the figure toward the light of the place: no brighter than the place allows, and in its color."""

    solid = figure.getchannel("A").point(lambda value: 255 if value > 128 else 0)
    rgb = figure.convert("RGB")
    lit = ImageStat.Stat(rgb, solid).mean
    scene = ImageStat.Stat(around.convert("RGB")).mean
    lit_level = sum(lit) / 3
    scene_level = sum(scene) / 3
    if lit_level <= 0 or scene_level <= 0:
        return figure
    dim = min(1.0, max(0.45, 1.6 * scene_level / lit_level))
    gains = [dim * (1 + tint * (channel / scene_level - 1)) for channel in scene]
    bands = [band.point(lambda value, gain=gain: min(255, round(value * gain))) for band, gain in zip(rgb.split(), gains)]
    out = Image.merge("RGB", bands).convert("RGBA")
    out.putalpha(figure.getchannel("A"))
    return out


def _bright_mean(rgb: Image.Image, mask: Image.Image) -> list[float]:
    """Mean color of the lit half of the masked pixels."""

    gray = rgb.convert("L")
    median = ImageStat.Stat(gray, mask).median[0]
    lit = ImageChops.multiply(mask, gray.point(lambda value: 255 if value >= median else 0))
    if lit.getbbox() is None:
        lit = mask
    return ImageStat.Stat(rgb, lit).mean


def figure_color(reference: Image.Image) -> list[float]:
    """The color of the figure in a reference: keyed when it stands on green, else the whole crop."""

    rgb = reference.convert("RGB")
    solid = key_green(rgb).getchannel("A").point(lambda value: 255 if value > 128 else 0)
    if solid.histogram()[0] < 0.2 * rgb.width * rgb.height:
        solid = Image.new("L", rgb.size, 255)
    return _bright_mean(rgb, solid)


def match_color(figure: Image.Image, target: list[float]) -> Image.Image:
    """Take the figure to the color of the same figure in the previous panel."""

    solid = figure.getchannel("A").point(lambda value: 255 if value > 128 else 0)
    rgb = figure.convert("RGB")
    lit = _bright_mean(rgb, solid)
    gains = [min(MAX_GAIN, max(MIN_GAIN, want / have)) if have > 0 else 1.0 for want, have in zip(target, lit)]
    bands = [band.point(lambda value, gain=gain: min(255, round(value * gain))) for band, gain in zip(rgb.split(), gains)]
    out = Image.merge("RGB", bands).convert("RGBA")
    out.putalpha(figure.getchannel("A"))
    return out


def _shift(alpha: Image.Image, dx: int, dy: int) -> Image.Image:
    moved = Image.new("L", alpha.size, 0)
    moved.paste(alpha, (dx, dy))
    return moved


def grade_for_shadow(figure: Image.Image, shadow: CastShadow) -> Image.Image:
    """The light that throws the shadow also lights the figure.

    A shadow toward the camera means the light is behind it: the front goes dark and a soft
    cool rim stays on the top, and on the side opposite the lean."""

    if shadow.back >= 0:
        return figure
    rgba = figure.convert("RGBA")
    alpha = rgba.getchannel("A")
    radius = max(3, rgba.height // 55)
    facing_right = ImageChops.subtract(alpha, _shift(alpha, -radius, 0))
    facing_left = ImageChops.subtract(alpha, _shift(alpha, radius, 0))
    facing_up = ImageChops.subtract(alpha, _shift(alpha, 0, radius))
    side = facing_left if shadow.lean > 0 else facing_right
    edge = ImageChops.add(facing_up.point(lambda value: int(value * 0.7)), side.point(lambda value: int(value * 0.55)))
    edge = edge.filter(ImageFilter.GaussianBlur(max(1.4, radius * 0.8)))
    edge = ImageChops.multiply(edge, alpha)
    shaded = [band.point(lambda value: int(value * 0.5)) for band in rgba.convert("RGB").split()]
    cool = (150, 168, 188)
    lit = [
        ImageChops.add(band, edge.point(lambda value, glow=glow: int(glow * 0.32 * value / 255)))
        for band, glow in zip(shaded, cool)
    ]
    out = Image.merge("RGB", lit).convert("RGBA")
    out.putalpha(alpha)
    return out


def lay_shadow(canvas: Image.Image, mask: Image.Image) -> Image.Image:
    """Darken the ground where the mask is. The dirt and the leaves stay; only the light goes."""

    keep = ImageChops.invert(mask)
    return Image.merge("RGB", tuple(ImageChops.multiply(band, keep) for band in canvas.split()))


def place_figure(
    base: Image.Image,
    generated: Image.Image,
    box,
    match: Image.Image | None = None,
    shadow: CastShadow | None = None,
) -> tuple[Image.Image, Image.Image]:
    """The base with the figure in its box, feet on the bottom edge, and the keyed figure.

    The box height is the figure's height; the pose decides its width. match is the same figure
    in the previous panel: its color wins over the color of the place. shadow lays the figure's
    silhouette on the ground; without it there is only the soft shadow under the feet."""

    canvas = base.convert("RGB")
    width, height = canvas.size
    left, top, right, bottom = (round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height))
    figure = trim(key_green(generated))
    scale = (bottom - top) / figure.height
    size = (max(1, round(figure.width * scale)), max(1, round(figure.height * scale)))
    figure = figure.resize(size, Image.Resampling.LANCZOS)
    if shadow is not None:
        figure = grade_for_shadow(figure, shadow)
    if match is not None:
        figure = match_color(figure, figure_color(match))
    else:
        around = canvas.crop((max(0, left - size[0]), max(0, top), min(width, right + size[0]), min(height, bottom)))
        figure = match_scene(figure, around)
    x = round((left + right) / 2 - size[0] / 2)
    y = bottom - size[1]
    contact = Image.new("L", canvas.size, 0)
    spread = size[0] * 0.22
    ImageDraw.Draw(contact).ellipse(
        [x + size[0] / 2 - spread, bottom - size[0] * 0.03, x + size[0] / 2 + spread, bottom + size[0] * 0.035],
        fill=round(255 * CONTACT_OPACITY),
    )
    contact = contact.filter(ImageFilter.GaussianBlur(max(1, size[0] * 0.04)))
    if shadow is not None and shadow.back:
        mask, dx, dy = cast_shadow(figure.getchannel("A"), shadow)
        laid = Image.new("L", canvas.size, 0)
        laid.paste(mask, (x + dx, bottom + dy))
        contact = ImageChops.lighter(contact, laid)
    canvas = lay_shadow(canvas, contact)
    canvas.paste(figure, (x, y), figure)
    return canvas, figure


def save_cutout(figure: Image.Image, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    figure.save(dest, format="PNG")
    return dest
