#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "kikit>=1.6.0",
#     "shapely>=2.0.0",
# ]
# ///
"""
KiKit Scripted Panelization for Multi-Board Eurorack Faceplates.

Reads a KiCad PCB layout containing multiple side-by-side boards (such as 4x 12HP
Eurorack faceplates sharing common graphics) and creates a unified manufacturing panel
joined by mousebite break-off tabs between adjacent boards, without outer rails.

Usage:
    uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 .agents/skills/panelize/scripts/panelize.py -i VCA/VCA.kicad_pcb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def check_environment() -> None:
    """Ensure pcbnew can be imported, locating KiCad's bundled module if needed."""
    try:
        import pcbnew  # noqa: F401
        return
    except ImportError:
        pass

    # Candidate paths for KiCad's Python site-packages on macOS and Linux
    candidates = [
        Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/lib/python3.9/site-packages"),
        Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/lib/python3.9/site-packages"),
        Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/python"),
        Path("/usr/lib/python3/dist-packages"),
    ]

    for candidate in candidates:
        if candidate.is_dir() and (candidate / "pcbnew.py").is_file():
            sys.path.insert(0, str(candidate))
            try:
                import pcbnew  # noqa: F401
                return
            except ImportError:
                continue

    kicad_python = (
        "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework"
        "/Versions/3.9/bin/python3"
    )
    sys.stderr.write(
        "Error: 'pcbnew' module could not be loaded in the current Python environment.\n\n"
        "On macOS, KiCad's pcbnew C++ module requires Python 3.9 matching KiCad's build.\n"
        "Please run this script with uv specifying KiCad's Python interpreter:\n\n"
        f"    uv run --python {kicad_python} panelize.py\n\n"
    )
    sys.exit(1)


def panelize(
    input_pcb: Path,
    output_pcb: Path,
    tab_width_mm: float = 5.0,
    tab_y_positions_mm: list[float] | None = None,
    hole_diameter_mm: float = 0.5,
    hole_spacing_mm: float = 0.75,
    mousebite_offset_mm: float = 0.25,
    tolerance_mm: float | None = None,
) -> None:
    check_environment()

    import pcbnew
    from kikit import panelize as kp
    from kikit.common import Layer, collectEdges
    from kikit.substrate import Substrate
    from kikit.units import mm
    from shapely.geometry import LineString, box

    if tab_y_positions_mm is None:
        # Default clear vertical positions: ~26%, ~49%, ~72% height (128.5 mm total)
        # Clear of Eurorack mounting slots and potentiometer/jack holes
        tab_y_positions_mm = [34.0, 62.5, 92.0]

    input_pcb = input_pcb.resolve()
    output_pcb = output_pcb.resolve()
    output_pcb.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading input PCB: {input_pcb}")

    # Inspect input board to ensure artwork extending outside Edge.Cuts is fully captured
    source_board = pcbnew.LoadBoard(str(input_pcb))
    edge_bbox = source_board.GetBoardEdgesBoundingBox()
    all_drawings = list(source_board.GetDrawings())

    if tolerance_mm is None:
        if all_drawings:
            min_x = min(d.GetBoundingBox().GetX() for d in all_drawings)
            min_y = min(d.GetBoundingBox().GetY() for d in all_drawings)
            max_x = max(d.GetBoundingBox().GetX() + d.GetBoundingBox().GetWidth() for d in all_drawings)
            max_y = max(d.GetBoundingBox().GetY() + d.GetBoundingBox().GetHeight() for d in all_drawings)

            overflow = max(
                0,
                edge_bbox.GetX() - min_x,
                edge_bbox.GetY() - min_y,
                max_x - (edge_bbox.GetX() + edge_bbox.GetWidth()),
                max_y - (edge_bbox.GetY() + edge_bbox.GetHeight()),
            )
            # Add a 10 mm buffer to ensure all graphical elements fit fully inside the source area
            tolerance_nm = overflow + int(10 * mm) if overflow > 0 else 0
        else:
            tolerance_nm = 0
    else:
        tolerance_nm = int(tolerance_mm * mm)

    if tolerance_nm > 0:
        print(f"Background artwork extends beyond Edge.Cuts; expanding source extraction tolerance by {tolerance_nm / 1e6:.2f} mm")

    panel = kp.Panel(str(output_pcb))

    # Append input board aligned to TopLeft (0, 0)
    panel.appendBoard(
        str(input_pcb),
        pcbnew.VECTOR2I(0, 0),
        origin=kp.Origin.TopLeft,
        tolerance=tolerance_nm,
    )

    # Detect sub-boards from substrate polygons
    geoms = sorted(
        panel.boardSubstrate.substrates.geoms,
        key=lambda g: g.bounds[0],  # sort by minx
    )
    print(f"Detected {len(geoms)} separate board outlines in layout:")
    for idx, g in enumerate(geoms):
        b = [round(v / 1e6, 2) for v in g.bounds]
        w = round(b[2] - b[0], 2)
        h = round(b[3] - b[1], 2)
        print(f"  Board {idx + 1}: X=[{b[0]}, {b[2]}] mm ({w} mm wide), Y=[{b[1]}, {b[3]}] mm ({h} mm high)")

    if len(geoms) < 2:
        print("Warning: Fewer than 2 board outlines found. No seams to join.")

    # Identify seams between adjacent boards
    seams: list[tuple[float, float]] = []
    for i in range(len(geoms) - 1):
        x_left = geoms[i].bounds[2] / 1e6   # right edge of left board (mm)
        x_right = geoms[i + 1].bounds[0] / 1e6  # left edge of right board (mm)
        gap = round(x_right - x_left, 2)
        seams.append((x_left, x_right))
        print(f"Seam {i + 1}: between Board {i + 1} (X={x_left:.2f}) and Board {i + 2} (X={x_right:.2f}), gap={gap:.2f} mm")

    overlap_mm = 0.5  # Slight overlap into boards ensures contiguous union
    cuts: list[LineString] = []

    print(f"\nAdding {len(tab_y_positions_mm)} tabs per seam (width = {tab_width_mm} mm):")
    for seam_idx, (x1, x2) in enumerate(seams, start=1):
        for y in tab_y_positions_mm:
            y_top = y - tab_width_mm / 2.0
            y_bot = y + tab_width_mm / 2.0

            # Tab substrate bridging the gap
            tab_poly = box(
                (x1 - overlap_mm) * mm,
                y_top * mm,
                (x2 + overlap_mm) * mm,
                y_bot * mm,
            )
            panel.appendSubstrate(tab_poly)

            # Break-off cut lines along each board edge.
            # KiKit offsets cuts to the "left" of the line direction vector:
            # - For the left edge (x1), orienting bottom-to-top (y_bot -> y_top) offsets right (+X) into the tab.
            # - For the right edge (x2), orienting top-to-bottom (y_top -> y_bot) offsets left (-X) into the tab.
            cuts.append(LineString([(x1 * mm, y_bot * mm), (x1 * mm, y_top * mm)]))
            cuts.append(LineString([(x2 * mm, y_top * mm), (x2 * mm, y_bot * mm)]))
            print(f"  Seam {seam_idx} tab at Y={y:.1f} mm (Y range: [{y_top:.1f}, {y_bot:.1f}] mm)")

    print(f"\nRendering mousebites for {len(cuts)} cut edges:")
    print(f"  Drill diameter: {hole_diameter_mm} mm")
    print(f"  Drill spacing:  {hole_spacing_mm} mm")
    print(f"  Offset:         {mousebite_offset_mm} mm")

    panel.makeMouseBites(
        cuts,
        diameter=hole_diameter_mm * mm,
        spacing=hole_spacing_mm * mm,
        offset=mousebite_offset_mm * mm,
    )

    print(f"\nSaving panel to: {output_pcb}")
    panel.save(reconstructArcs=True)

    # Validate output panel substrate
    saved_board = pcbnew.LoadBoard(str(output_pcb))
    edges = collectEdges(saved_board, Layer.Edge_Cuts)
    sub = Substrate(edges)
    is_single = sub.isSinglePiece()
    bounds = [round(b / 1e6, 2) for b in sub.bounds()]
    num_fps = len(list(saved_board.GetFootprints()))

    print("\nPanel validation:")
    print(f"  Substrate is single unified piece: {is_single}")
    print(f"  Panel dimensions: {bounds[2] - bounds[0]:.2f} mm x {bounds[3] - bounds[1]:.2f} mm")
    print(f"  Mousebite drill holes (footprints): {num_fps}")

    # Remove any transient lock file left behind by pcbnew.LoadBoard
    lck_file = output_pcb.parent / f"~{output_pcb.stem}.kicad_pro.lck"
    if lck_file.is_file():
        lck_file.unlink(missing_ok=True)

    print("Done! Panelization completed successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Panelize multi-board Eurorack faceplates into a 1x4 panel with mousebites using KiKit."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path("VCO/VCO.kicad_pcb"),
        help="Path to source KiCad PCB layout (default: VCO/VCO.kicad_pcb)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Path for generated panel PCB (default: <input_dir>/<input_stem>_panel.kicad_pcb)",
    )
    parser.add_argument(
        "--tab-width",
        type=float,
        default=5.0,
        help="Width of each mousebite tab in mm (default: 5.0)",
    )
    parser.add_argument(
        "--tab-positions",
        type=float,
        nargs="+",
        default=[34.0, 62.5, 92.0],
        help="Y-coordinates (in mm from top edge) for tabs (default: 34.0 62.5 92.0)",
    )
    parser.add_argument(
        "--hole-diameter",
        type=float,
        default=0.5,
        help="Mousebite drill hole diameter in mm (default: 0.5)",
    )
    parser.add_argument(
        "--hole-spacing",
        type=float,
        default=0.75,
        help="Mousebite hole center-to-center spacing in mm (default: 0.75)",
    )
    parser.add_argument(
        "--mousebite-offset",
        type=float,
        default=0.25,
        help="Mousebite offset into tab in mm (default: 0.25)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="Source extraction tolerance in mm (default: auto-detected from artwork bounds)",
    )

    args = parser.parse_args()
    input_pcb: Path = args.input
    output_pcb: Path = (
        args.output
        if args.output is not None
        else input_pcb.parent / f"{input_pcb.stem}_panel{input_pcb.suffix}"
    )

    panelize(
        input_pcb=input_pcb,
        output_pcb=output_pcb,
        tab_width_mm=args.tab_width,
        tab_y_positions_mm=args.tab_positions,
        hole_diameter_mm=args.hole_diameter,
        hole_spacing_mm=args.hole_spacing,
        mousebite_offset_mm=args.mousebite_offset,
        tolerance_mm=args.tolerance,
    )


if __name__ == "__main__":
    main()
