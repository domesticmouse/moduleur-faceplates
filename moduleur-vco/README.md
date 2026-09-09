# Moduleur VCO Faceplate Panelization

Scripted panelization for the multi-board Eurorack faceplate in `VCO/VCO.kicad_pcb` using [KiKit](https://yaqwsx.github.io/KiKit/) and [`uv`](https://docs.astral.sh/uv/).

The layout contains 4 side-by-side 12HP Eurorack faceplates (each ~60.6 mm × 128.5 mm) sharing a continuous front graphic. `panelize.py` connects the 4 boards into a unified 1×4 manufacturing panel using break-away mousebite tabs across the vertical seams, without outer rails.

## Requirements

- KiCad 10 (or 9 / 8) installed on macOS (or Linux)
- [`uv`](https://docs.astral.sh/uv/)

KiKit dependencies are declared directly within `panelize.py` using **PEP 723** inline script metadata.

## Running the Panelization

On macOS, KiCad's `pcbnew` Python module is compiled against KiCad's bundled Python 3.9 runtime. Run the script via `uv` specifying KiCad's Python interpreter:

```bash
uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 panelize.py
```

### Options

```bash
uv run --python /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 panelize.py [OPTIONS]
```

- `--input`, `-i`: Path to input PCB layout (default: `VCO/VCO.kicad_pcb`)
- `--output`, `-o`: Path to output panel PCB (default: `VCO/VCO_panel.kicad_pcb`)
- `--tab-width`: Width of each mousebite tab in mm (default: `5.0`)
- `--tab-positions`: Vertical Y positions (in mm from top edge) for tabs (default: `34.0 62.5 92.0`)
- `--hole-diameter`: Drill diameter for mousebites in mm (default: `0.5`)
- `--hole-spacing`: Center-to-center pitch of mousebite holes in mm (default: `0.75`)
- `--mousebite-offset`: Hole offset into tab in mm (default: `0.25`)

## Generated Output

- `VCO/VCO_panel.kicad_pcb`: Final panelized board with all original graphics, copper, silkscreen, and cutouts, joined by 18 mousebite cut edges (144 drill holes across 9 tabs).
- `VCO/VCO_panel.kicad_pro`, `VCO/VCO_panel.kicad_prl`: KiCad project settings matching the panel.
