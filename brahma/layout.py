"""Trade-paperback page: 6 by 9 inches at 300 dpi.

Generated art is a multiple of 64 on the long edge (SDXL's native size) and is
scaled into the frame only when the page is assembled. The full-resolution PNG
stays in ``panels/``.
"""

from __future__ import annotations

from dataclasses import dataclass

PAGE_WIDTH = 1800
PAGE_HEIGHT = 2700
MARGIN = 72
GUTTER = 28
INSET = 10
LONG_EDGE = 1024
PAPER = (243, 239, 228, 255)


@dataclass(frozen=True)
class Frame:
    x: int
    y: int
    width: int
    height: int


def frames_for(count: int) -> list[Frame]:
    if not 1 <= count <= 6:
        raise ValueError(f"a page holds 1 to 6 panels, got {count}")
    inner_w = PAGE_WIDTH - 2 * MARGIN
    inner_h = PAGE_HEIGHT - 2 * MARGIN
    if count == 1:
        rows = [1]
    elif count == 2:
        rows = [1, 1]
    elif count == 3:
        rows = [1, 2]
    elif count == 4:
        rows = [2, 2]
    elif count == 5:
        rows = [1, 2, 2]
    else:
        rows = [2, 2, 2]
    row_h = (inner_h - GUTTER * (len(rows) - 1)) // len(rows)
    frames: list[Frame] = []
    y = MARGIN
    for columns in rows:
        col_w = (inner_w - GUTTER * (columns - 1)) // columns
        x = MARGIN
        for _ in range(columns):
            frames.append(Frame(x, y, col_w, row_h))
            x += col_w + GUTTER
        y += row_h + GUTTER
    return frames


def snap64(value: int) -> int:
    return max(64, int(round(value / 64)) * 64)


def generation_size(frame: Frame, long_edge: int = LONG_EDGE) -> tuple[int, int]:
    scale = long_edge / max(frame.width, frame.height)
    return snap64(round(frame.width * scale)), snap64(round(frame.height * scale))


def art_box(frame: Frame) -> Frame:
    """Art sits inside the frame so the border stroke stays visible and editable."""

    return Frame(frame.x + INSET, frame.y + INSET, frame.width - 2 * INSET, frame.height - 2 * INSET)
