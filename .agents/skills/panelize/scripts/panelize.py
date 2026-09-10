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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pcbnew
    from kikit import panelize as kp
    from kikit.common import Layer, collectEdges
    from kikit.substrate import Substrate
    from kikit.units import mm
    from shapely.geometry import LineString, box

# Module-level placeholders for lazily imported KiCad and geometry libraries
pcbnew = None
kp = None
Layer = None
collectEdges = None
Substrate = None
mm = None
LineString = None
box = None


def check_environment() -> None:
    """Ensure pcbnew can be imported, locating KiCad's bundled module if needed."""
    try:
        import pcbnew

        return
    except ImportError:
        pass

    # Candidate paths for KiCad's Python site-packages on macOS and Linux
    candidates = [
        Path(
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/lib/python3.9/site-packages"
        ),
        Path(
            "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/lib/python3.9/site-packages"
        ),
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
        f"    uv run --python {kicad_python} {sys.argv[0]}\n\n"
    )
    sys.exit(1)


def ensure_environment() -> None:
    """Ensure KiCad and KiKit dependencies are imported and available globally."""
    global pcbnew, kp, Layer, collectEdges, Substrate, mm, LineString, box
    if pcbnew is not None:
        return

    check_environment()

    import pcbnew as _pcbnew
    from kikit import panelize as _kp
    from kikit.common import Layer as _Layer
    from kikit.common import collectEdges as _collectEdges
    from kikit.substrate import Substrate as _Substrate
    from kikit.units import mm as _mm
    from shapely.geometry import LineString as _LineString
    from shapely.geometry import box as _box

    pcbnew = _pcbnew
    kp = _kp
    Layer = _Layer
    collectEdges = _collectEdges
    Substrate = _Substrate
    mm = _mm
    LineString = _LineString
    box = _box


@dataclass
class PanelConfig:
    """Configuration options for panel generation and mousebites."""

    tab_width_mm: float = 5.0
    tab_y_positions_mm: list[float] = field(default_factory=lambda: [34.0, 62.5, 92.0])
    hole_diameter_mm: float = 0.5
    hole_spacing_mm: float = 0.75
    mousebite_offset_mm: float = 0.25
    tolerance_mm: float | None = None


def compute_artwork_tolerance(
    board: pcbnew.BOARD, default_buffer_mm: float = 10.0
) -> int:
    """Calculate the tolerance in nanometers to capture artwork extending beyond Edge.Cuts."""
    edge_bbox = board.GetBoardEdgesBoundingBox()
    all_drawings = list(board.GetDrawings())
    if not all_drawings:
        return 0

    min_x = min(d.GetBoundingBox().GetX() for d in all_drawings)
    min_y = min(d.GetBoundingBox().GetY() for d in all_drawings)
    max_x = max(
        d.GetBoundingBox().GetX() + d.GetBoundingBox().GetWidth() for d in all_drawings
    )
    max_y = max(
        d.GetBoundingBox().GetY() + d.GetBoundingBox().GetHeight() for d in all_drawings
    )

    overflow = max(
        0,
        edge_bbox.GetX() - min_x,
        edge_bbox.GetY() - min_y,
        max_x - (edge_bbox.GetX() + edge_bbox.GetWidth()),
        max_y - (edge_bbox.GetY() + edge_bbox.GetHeight()),
    )
    if overflow > 0:
        return overflow + int(default_buffer_mm * mm)
    return 0


def init_panel(input_pcb: Path, output_pcb: Path, tolerance_nm: int) -> kp.Panel:
    """Initialize a KiKit Panel and append the input board at TopLeft (0, 0)."""
    output_pcb.parent.mkdir(parents=True, exist_ok=True)
    panel = kp.Panel(str(output_pcb))
    panel.appendBoard(
        str(input_pcb),
        pcbnew.VECTOR2I(0, 0),
        origin=kp.Origin.TopLeft,
        tolerance=tolerance_nm,
    )
    return panel


def detect_seams(panel: kp.Panel) -> list[tuple[float, float]]:
    """Detect adjacent sub-board outlines and calculate seam coordinates in mm.

    Returns a list of (x_left_edge, x_right_edge) tuples for adjacent boards.
    """
    geoms = sorted(
        panel.boardSubstrate.substrates.geoms,
        key=lambda g: g.bounds[0],  # sort by minx
    )
    print(f"Detected {len(geoms)} separate board outlines in layout:")
    for idx, g in enumerate(geoms):
        b = [round(v / 1e6, 2) for v in g.bounds]
        w = round(b[2] - b[0], 2)
        h = round(b[3] - b[1], 2)
        print(
            f"  Board {idx + 1}: X=[{b[0]}, {b[2]}] mm ({w} mm wide), Y=[{b[1]}, {b[3]}] mm ({h} mm high)"
        )

    if len(geoms) < 2:
        return []

    seams: list[tuple[float, float]] = []
    for i in range(len(geoms) - 1):
        x_left = geoms[i].bounds[2] / 1e6  # right edge of left board (mm)
        x_right = geoms[i + 1].bounds[0] / 1e6  # left edge of right board (mm)
        gap = round(x_right - x_left, 2)
        seams.append((x_left, x_right))
        print(
            f"Seam {i + 1}: between Board {i + 1} (X={x_left:.2f}) and Board {i + 2} (X={x_right:.2f}), gap={gap:.2f} mm"
        )
    return seams


def add_mousebite_tabs(
    panel: kp.Panel,
    seams: list[tuple[float, float]],
    config: PanelConfig,
    overlap_mm: float = 0.5,
) -> None:
    """Add substrate bridge tabs and perforate edges with mousebites."""
    cuts: list[LineString] = []

    print(
        f"\nAdding {len(config.tab_y_positions_mm)} tabs per seam (width = {config.tab_width_mm} mm):"
    )
    for seam_idx, (x1, x2) in enumerate(seams, start=1):
        for y in config.tab_y_positions_mm:
            y_top = y - config.tab_width_mm / 2.0
            y_bot = y + config.tab_width_mm / 2.0

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
            print(
                f"  Seam {seam_idx} tab at Y={y:.1f} mm (Y range: [{y_top:.1f}, {y_bot:.1f}] mm)"
            )

    print(f"\nRendering mousebites for {len(cuts)} cut edges:")
    print(f"  Drill diameter: {config.hole_diameter_mm} mm")
    print(f"  Drill spacing:  {config.hole_spacing_mm} mm")
    print(f"  Offset:         {config.mousebite_offset_mm} mm")

    panel.makeMouseBites(
        cuts,
        diameter=config.hole_diameter_mm * mm,
        spacing=config.hole_spacing_mm * mm,
        offset=config.mousebite_offset_mm * mm,
    )


def validate_panel(panel_path: Path) -> bool:
    """Validate that the generated panel substrate is a single contiguous piece."""
    saved_board = pcbnew.LoadBoard(str(panel_path))
    edges = collectEdges(saved_board, Layer.Edge_Cuts)
    sub = Substrate(edges)
    is_single = sub.isSinglePiece()
    bounds = [round(b / 1e6, 2) for b in sub.bounds()]
    num_fps = len(list(saved_board.GetFootprints()))

    print("\nPanel validation:")
    print(f"  Substrate is single unified piece: {is_single}")
    print(
        f"  Panel dimensions: {bounds[2] - bounds[0]:.2f} mm x {bounds[3] - bounds[1]:.2f} mm"
    )
    print(f"  Mousebite drill holes (footprints): {num_fps}")
    return is_single


def cleanup_lockfile(board_path: Path) -> None:
    """Remove any transient lock file left behind by pcbnew.LoadBoard."""
    lck_file = board_path.parent / f"~{board_path.stem}.kicad_pro.lck"
    if lck_file.is_file():
        lck_file.unlink(missing_ok=True)


def panelize(
    input_pcb: Path,
    output_pcb: Path,
    config: PanelConfig | None = None,
) -> None:
    """Panelize a multi-board PCB layout into a unified panel joined by mousebites."""
    ensure_environment()
    config = config or PanelConfig()
    input_pcb = input_pcb.resolve()
    output_pcb = output_pcb.resolve()

    print(f"Loading input PCB: {input_pcb}")
    source_board = pcbnew.LoadBoard(str(input_pcb))

    if config.tolerance_mm is None:
        tolerance_nm = compute_artwork_tolerance(source_board)
    else:
        tolerance_nm = int(config.tolerance_mm * mm)

    if tolerance_nm > 0:
        print(
            f"Background artwork extends beyond Edge.Cuts; expanding source extraction tolerance by {tolerance_nm / 1e6:.2f} mm"
        )

    panel = init_panel(input_pcb, output_pcb, tolerance_nm)
    seams = detect_seams(panel)
    if not seams:
        print("Warning: Fewer than 2 board outlines found. No seams to join.")

    add_mousebite_tabs(panel, seams, config)

    print(f"\nSaving panel to: {output_pcb}")
    panel.save(reconstructArcs=True)

    validate_panel(output_pcb)
    cleanup_lockfile(output_pcb)
    cleanup_lockfile(input_pcb)

    print("Done! Panelization completed successfully.")


def build_parser() -> argparse.ArgumentParser:
    """Build and configure the command-line argument parser."""
    description = """
KiKit Scripted Panelization for Multi-Board Eurorack Faceplates.

Panelizes a KiCad PCB layout containing multiple adjacent Eurorack faceplates
(such as 4x 12HP faceplates sharing continuous graphics) into a unified manufacturing
panel joined by break-off mousebite tabs across vertical seams, without outer rails.

Features:
  - Preserves continuous artwork and silkscreen extending beyond Edge.Cuts.
  - Automatically discovers sub-board outlines and seam gaps along the X-axis.
  - Generates structural tabs bridging inter-board seams at specified Y heights.
  - Perforates tab edges with recessed mousebites so break-off burrs do not protrude.
  - Validates that the resulting panel substrate is a single contiguous piece.
"""

    epilog = """
examples:
  # Panelize a layout with default settings (auto-derived output <dir>/<stem>_panel.kicad_pcb):
  uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \\
      .agents/skills/panelize/scripts/panelize.py -i VCA/VCA.kicad_pcb

  # Specify an explicit output PCB path:
  uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \\
      .agents/skills/panelize/scripts/panelize.py -i VCO/VCO.kicad_pcb -o panels/VCO_panel.kicad_pcb

  # Customize tab width and vertical placement (Y coordinates in mm from top edge):
  uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \\
      .agents/skills/panelize/scripts/panelize.py -i VCA/VCA.kicad_pcb --tab-width 6.0 --tab-positions 30.0 60.0 90.0

  # Fine-tune mousebite drill hole diameter, hole pitch, and recess offset:
  uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \\
      .agents/skills/panelize/scripts/panelize.py -i VCO/VCO.kicad_pcb --hole-diameter 0.6 --hole-spacing 0.8 --mousebite-offset 0.3

notes:
  - Running panelization requires KiCad's 'pcbnew' Python module and 'kikit'.
    On macOS, execute using KiCad's bundled Python 3.9 via 'uv' as shown above.
  - Running with -h / --help works in any standard Python environment without
    requiring KiCad or KiKit to be installed.
"""

    parser = argparse.ArgumentParser(
        prog="panelize.py",
        description=description.strip(),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=Path("VCO/VCO.kicad_pcb"),
        metavar="PATH",
        help="Path to source KiCad PCB layout (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Destination path for generated panel PCB layout (default: <input_dir>/<input_stem>_panel.kicad_pcb)",
    )
    parser.add_argument(
        "--tab-width",
        type=float,
        default=5.0,
        metavar="MM",
        help="Width of each mousebite break-off tab in mm (default: %(default)s mm)",
    )
    parser.add_argument(
        "--tab-positions",
        type=float,
        nargs="+",
        default=[34.0, 62.5, 92.0],
        metavar="Y_MM",
        help="One or more Y-coordinates in mm from board top edge for tab placement (default: %(default)s)",
    )
    parser.add_argument(
        "--hole-diameter",
        type=float,
        default=0.5,
        metavar="MM",
        help="Drill diameter for mousebite perforation holes in mm (default: %(default)s mm)",
    )
    parser.add_argument(
        "--hole-spacing",
        type=float,
        default=0.75,
        metavar="MM",
        help="Center-to-center pitch between mousebite drill holes in mm (default: %(default)s mm)",
    )
    parser.add_argument(
        "--mousebite-offset",
        type=float,
        default=0.25,
        metavar="MM",
        help="Inset offset of mousebites into tab material in mm to recess breakout burrs (default: %(default)s mm)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        metavar="MM",
        help="Source extraction bounding box tolerance buffer in mm (default: auto-detected from artwork bounds)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    input_pcb: Path = args.input
    output_pcb: Path = (
        args.output
        if args.output is not None
        else input_pcb.parent / f"{input_pcb.stem}_panel{input_pcb.suffix}"
    )

    config = PanelConfig(
        tab_width_mm=args.tab_width,
        tab_y_positions_mm=args.tab_positions,
        hole_diameter_mm=args.hole_diameter,
        hole_spacing_mm=args.hole_spacing,
        mousebite_offset_mm=args.mousebite_offset,
        tolerance_mm=args.tolerance,
    )

    panelize(
        input_pcb=input_pcb,
        output_pcb=output_pcb,
        config=config,
    )


if __name__ == "__main__":
    main()
