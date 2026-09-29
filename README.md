# Promo Material Tools v38 handoff

Reusable, agent-friendly tools for preparing and editing promotional artwork.

## Python setup with uv

Install [uv](https://docs.astral.sh/uv/), then use it to install Python and
create the project environment:

```bash
brew install uv cairo zbar
uv python install 3.13
uv venv --python 3.13
uv pip install --python .venv/bin/python -r requirements.txt
```

Activate the environment before running the tools:

```bash
source .venv/bin/activate
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
```

## Included tools

### Asset extractor
`tools/asset_extractor`

YAML-driven crops and selections from source images. Use broad RGB crops for the canonical vectorizer workflow.

### Vectorizer
`tools/vectorizer`

Diagnostic-first raster-to-vector conversion. Geometry comes from visible RGB edges. Supports per-asset line/Bézier tolerances and contour selection.

### SVG editor
`tools/svg_editor`

ID-first SVG manipulation with both direct CLI commands and reproducible YAML edit jobs.

Supported first-version operations:
- inspect IDs / missing IDs
- ensure IDs
- move
- scale
- rotate
- set/remove attributes
- replace text
- delete
- duplicate
- rename IDs and update common references
- group / ungroup

Every SVG write produces an edited SVG, rendered PNG preview, and JSON edit log.

### QR generator / designer
`tools/qr_generator`

YAML/JSON-driven styled QR generation with a Python CLI and a local HTML configurator backed by the exact same Python renderer. Supports module shapes, neighborhood-aware connectivity, finder-eye styling, colors, optional logo knockout, SVG/PNG export, and automatic decode validation.

## SVG ID convention

Whenever an SVG is expected to be edited again, give meaningful elements/groups stable IDs such as:

```xml
<g id="logo">...</g>
<g id="card-1">...</g>
<path id="star-top-right" ... />
<text id="main-heading">...</text>
```

Prefer semantic IDs for major objects. `svg_editor ensure-ids` can assign deterministic fallback IDs to elements that do not have one, but those generated IDs should be committed once rather than regenerated repeatedly.

See `tools/svg_editor/README.md`.


## SVG layout layer
The SVG editor now supports YAML `tokens`, automatic rendered bbox anchors, optional custom semantic anchors, and ordered relative `layout` rules. This allows design spacing such as card gaps, padding, and pill insets to live as named values instead of repeated coordinates.

## QR designer

Run `python tools/qr_generator/configurator.py` and open `http://127.0.0.1:8765/` for the visual configurator, or use `python tools/qr_generator/generate.py <job.yaml>` from agents/scripts. Both routes share one QR matrix + SVG renderer. See `tools/qr_generator/README.md`.

## QR configurator: server or offline HTML

`tools/qr_generator/configurator.py` supports both `serve` and `build`. `serve` gives the scan-validated localhost UI; `build` emits a single self-contained HTML configurator that can visually edit the same schema and show/copy/download the current YAML. Use the exported YAML with `generate.py` for the authoritative final SVG/PNG.


## QR generator v30

The QR renderer now supports `modules.connectivity`, which can merge straight runs into bars, bridge adjacent modules, and soften corners for a more designed/playful look while keeping decode validation in the loop.


## QR generator v34

The QR renderer can now apply one continuous multi-stop SVG gradient across all data dots, pills, and bridges. Finder-marker rounding can be controlled with `eyes.radius_mode: layer` for a softer reference-style appearance.


## QR generator v38

The QR generator now supports sparse deterministic data-area accent modules and per-finder overrides, enabling branded layouts such as the TikTok reference style while remaining fully YAML-driven and scan-validated.
