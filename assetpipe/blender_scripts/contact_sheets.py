"""Contact-sheet composition (spec 14.3) -- Pillow-only, no ``bpy``.

Runs after ``render_views.py`` has written full-resolution per-view PNGs;
kept in its own bpy-free module so it is directly unit-testable with tiny
synthetic images (see ``assetpipe/tests/test_blender_scripts.py``), matching
the deliverable's requirement that contact-sheet composition be bpy-free and
tested.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

MAX_GRID = (2, 3)   # (cols, rows) -- spec 14.3: "<=2x3-grid contact sheets"
CELL_PX = 1024
LABEL_FG = (255, 255, 0)
LABEL_BG = (0, 0, 0)


def chunk_views(view_ids: list[str], grid: tuple[int, int] = MAX_GRID) -> list[list[str]]:
    """Split a flat, ordered ``view_id`` list into <= ``grid[0]*grid[1]``-sized
    groups, one per contact sheet, preserving order."""
    per_sheet = grid[0] * grid[1]
    if per_sheet <= 0:
        raise ValueError("grid must have at least one cell")
    return [view_ids[i:i + per_sheet] for i in range(0, len(view_ids), per_sheet)]


def compose_sheet(cells: list[tuple[str, Path]], out_path: str | Path,
                   grid: tuple[int, int] = MAX_GRID, cell_px: int = CELL_PX) -> Path:
    """Compose up to ``grid[0]*grid[1]`` ``(view_id, image_path)`` cells into
    one contact-sheet PNG, with the ``view_id`` burned into each cell's
    corner (spec 14.3: "the vision model must cite view ids, and burned-in
    labels remove ambiguity about which image is which"). PIL's default font
    is fine -- no font-file dependency."""
    cols, rows = grid
    if len(cells) > cols * rows:
        raise ValueError(f"{len(cells)} cells exceeds the {cols}x{rows} grid")
    if not cells:
        raise ValueError("compose_sheet called with no cells")

    sheet = Image.new("RGB", (cols * cell_px, rows * cell_px), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    for idx, (view_id, path) in enumerate(cells):
        col, row = idx % cols, idx // cols
        cell = Image.open(path).convert("RGB").resize((cell_px, cell_px))
        sheet.paste(cell, (col * cell_px, row * cell_px))

        x0, y0 = col * cell_px + 4, row * cell_px + 4
        text_w = max(1, 7 * len(view_id))
        draw.rectangle([x0 - 2, y0 - 2, x0 + text_w + 2, y0 + 14], fill=LABEL_BG)
        draw.text((x0, y0), view_id, fill=LABEL_FG)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def compose_all(renders_dir: str | Path, view_ids: list[str], out_dir: str | Path,
                 grid: tuple[int, int] = MAX_GRID, cell_px: int = CELL_PX,
                 name_fmt: str = "contact_sheet_{n}.png") -> list[Path]:
    """Compose every view in ``view_ids`` (found as
    ``<renders_dir>/<view_id>.png``) into numbered contact sheets. Returns the
    list of written sheet paths, in order."""
    renders_dir, out_dir = Path(renders_dir), Path(out_dir)
    sheets = []
    for n, group in enumerate(chunk_views(view_ids, grid), start=1):
        cells = [(vid, renders_dir / f"{vid}.png") for vid in group]
        sheets.append(compose_sheet(cells, out_dir / name_fmt.format(n=n), grid, cell_px))
    return sheets
