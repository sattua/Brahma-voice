"""OpenRaster writer.

A page is a zip of PNG layers plus ``stack.xml``. Krita, GIMP, MyPaint and
Drawpile open it. Art, lettering, frames and paper are separate layers, so a
person can repaint or move balloons without regenerating the panel.

The ``mimetype`` entry is stored first and uncompressed, as the spec requires.
Layers are listed top to bottom.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image

ORA_MIME = "image/openraster"


@dataclass
class OraLayer:
    name: str
    image: Image.Image
    x: int = 0
    y: int = 0
    group: str | None = None


@dataclass
class _Group:
    name: str
    x: int
    y: int
    layers: list[OraLayer] = field(default_factory=list)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _thumbnail(merged: Image.Image) -> Image.Image:
    copy = merged.copy()
    copy.thumbnail((256, 256), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    canvas.paste(copy, ((256 - copy.width) // 2, (256 - copy.height) // 2))
    return canvas


def composite(width: int, height: int, layers: list[OraLayer]) -> Image.Image:
    """``layers`` is top-to-bottom, matching the file. Painting runs the other way."""

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in reversed(layers):
        image = layer.image.convert("RGBA")
        canvas.alpha_composite(image, (layer.x, layer.y))
    return canvas


def _stack_xml(width: int, height: int, layers: list[OraLayer], dpi: int) -> tuple[str, list[tuple[str, OraLayer]]]:
    groups: dict[str, _Group] = {}
    root_layers: list[OraLayer | _Group] = []
    for layer in layers:
        if layer.group is None:
            root_layers.append(layer)
            continue
        group = groups.get(layer.group)
        if group is None:
            group = _Group(layer.group, layer.x, layer.y)
            groups[layer.group] = group
            root_layers.append(group)
        group.layers.append(layer)

    def layer_xml(layer: OraLayer, filename: str, dx: int, dy: int) -> str:
        return (
            f'<layer name="{escape(layer.name)}" src="{filename}" '
            f'x="{layer.x - dx}" y="{layer.y - dy}" opacity="1" visibility="visible"/>'
        )

    body: list[str] = []
    files: list[tuple[str, OraLayer]] = []
    for item in root_layers:
        if isinstance(item, OraLayer):
            filename = f"data/{_safe(item.name)}.png"
            files.append((filename, item))
            body.append(layer_xml(item, filename, 0, 0))
            continue
        children = []
        for layer in item.layers:
            filename = f"data/{_safe(item.name)}-{_safe(layer.name)}.png"
            files.append((filename, layer))
            children.append(layer_xml(layer, filename, item.x, item.y))
        inner = "".join(children)
        body.append(
            f'<stack name="{escape(item.name)}" x="{item.x}" y="{item.y}" opacity="1" visibility="visible">{inner}</stack>'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<image version="0.0.3" w="{width}" h="{height}" xres="{dpi}" yres="{dpi}">'
        f'<stack name="root" opacity="1">{"".join(body)}</stack>'
        "</image>"
    )
    return xml, files


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)


def write_openraster(path: Path, width: int, height: int, layers: list[OraLayer], *, dpi: int = 300) -> Path:
    if not layers:
        raise ValueError("a page needs at least one layer")
    xml, files = _stack_xml(width, height, layers, dpi)
    merged = composite(width, height, layers)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        mime = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        mime.compress_type = zipfile.ZIP_STORED
        archive.writestr(mime, ORA_MIME)
        archive.writestr("stack.xml", xml)
        for filename, layer in files:
            archive.writestr(filename, _png_bytes(layer.image))
        archive.writestr("mergedimage.png", _png_bytes(merged))
        archive.writestr("Thumbnails/thumbnail.png", _png_bytes(_thumbnail(merged)))
    return path
