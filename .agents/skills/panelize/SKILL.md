---
name: panelize
description: >-
  Panelizes multi-board Eurorack faceplates into unified manufacturing panels joined
  by mousebite break-off tabs using KiKit and KiCad. Use when panelizing KiCad PCB
  layouts, generating Eurorack faceplate panels, or adding break-away mousebites between boards.
---

# Eurorack Faceplate Panelization

Creates unified manufacturing panels from KiCad PCB layouts containing multiple side-by-side Eurorack faceplates (such as 4× 12HP faceplates sharing continuous graphics) joined by break-off mousebite tabs across vertical seams without outer rails.

## Helper Script

The panelization procedure is implemented in [scripts/panelize.py](./scripts/panelize.py). Dependencies (`kikit>=1.6.0`, `shapely>=2.0.0`) are declared directly via PEP 723 script metadata.

## Environment & Prerequisites

KiCad's C++ `pcbnew` module on macOS requires running with KiCad's bundled Python interpreter (typically Python 3.9) rather than system or homebrew Python:

- **KiCad Python:** `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3`
- **Runner:** `uv run --python <kicad_python> <script>`

## Running Panelization

### Basic Usage

To panelize a board with auto-derived output name (`<dir>/<stem>_panel.kicad_pcb`):

```bash
uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \
  .agents/skills/panelize/scripts/panelize.py -i <path/to/board.kicad_pcb>
```

For example, for the VCA board:

```bash
uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \
  .agents/skills/panelize/scripts/panelize.py -i VCA/VCA.kicad_pcb
```

### Options and Customization

```bash
uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \
  .agents/skills/panelize/scripts/panelize.py [OPTIONS]
```

| Flag | Default | Description |
| :--- | :--- | :--- |
| `-i`, `--input` | `VCO/VCO.kicad_pcb` | Path to source KiCad PCB layout |
| `-o`, `--output` | `<input_stem>_panel.kicad_pcb` | Path for generated panel PCB |
| `--tab-width` | `5.0` | Width of each mousebite tab in mm |
| `--tab-positions` | `34.0 62.5 92.0` | Vertical Y positions (mm from top edge) for tabs |
| `--hole-diameter` | `0.5` | Drill diameter for mousebites in mm |
| `--hole-spacing` | `0.75` | Hole center-to-center pitch in mm |
| `--mousebite-offset` | `0.25` | Mousebite offset into tab in mm (recesses burrs) |
| `--tolerance` | `None` (auto) | Source extraction tolerance in mm (auto-detects overflowing artwork) |

## How It Works

1. **Artwork Bounds Inspection**: Automatically detects if background silkscreen, copper fills, or drawings extend outside `Edge.Cuts` and expands the source extraction bounding box so full-panel artwork is not clipped.
2. **Sub-Board Detection**: Discovers individual board outlines from `Edge.Cuts` substrates and measures inter-board gap seams.
3. **Tab Substrate Union**: Bridges each inter-board seam with substrate rectangles across the gaps at the designated Y coordinates.
4. **Mousebites Rendering**: Places perforated drill holes along both edges of each tab, offset into the waste material by `mousebite_offset` so edge burrs after breakout do not protrude past the board edge.
5. **Substrate Validation**: Reloads the generated PCB to verify that `Substrate.isSinglePiece()` is `True` and reports final panel dimensions and drill hole count.
