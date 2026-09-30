# AGENTS.md

This repo contains small, inspectable tools for preparing promo-material assets.

## Tools
- `tools/asset_extractor/extract.py` — crop/select regions from source imagery.
- `tools/vectorizer/vectorize.py` — diagnostic-first raster-to-vector conversion.
- `tools/vectorizer/batch.py` — YAML batch vectorization with per-asset overrides.
- `tools/svg_editor/edit.py` — ID-first SVG editing via CLI or YAML jobs.
- `tools/svg_editor/export_pdf.py` — multi-page vector PDF export from SVGs via Inkscape.
- `tools/qr_generator/generate.py` — styled QR generation from YAML/JSON.
- `tools/pipeline/run.py` — runs recipe YAMLs (ordered tool steps with inline configs).
- `tools/pipeline/view.py` — local viewer for recipe outputs with asset highlighting.
- `tools/qr_generator/configurator.py` — local HTML QR designer using the same renderer as the CLI.

## General workflow rules
1. Keep source assets unchanged.
2. Preserve editable YAML job files and diagnostics.
3. Fix problems at the earliest broken stage.
4. Use `--only` for incremental asset reruns where supported.
5. Prefer deterministic tool operations over manually rewriting complex SVG/XML.

## Project layout rules
- Give each asset or composition its own folder with one recipe YAML (named after the folder) run by `tools/pipeline/run.py`.
- Recipes write only into the folder's `generated/`; never hand-edit generated files.
- Keep originals in the project's `sources/` and reference them by relative path.
- Use a project-level recipe with `recipe` steps to build parts in order.

## Vectorizer rules
- Geometry comes from visible RGB edges only.
- Prefer broad RGB crops, then select wanted contour IDs.
- Recommended baseline: `line_error_px: 0.25`, `bezier_error_px: 0.75`.
- Per-asset YAML values override shared defaults.

## SVG editing rules
- Meaningful objects/groups should have stable semantic IDs whenever practical.
- Prefer IDs such as `logo`, `card-1`, `star-top-right`, `main-heading`, not anonymous path positions.
- Before editing an unfamiliar SVG, run `svg_editor inspect`.
- If many renderable elements lack IDs, either add semantic IDs to important objects manually or run `ensure-ids` once and commit the resulting SVG.
- All object-targeting SVG editor operations are ID-first; do not rely on brittle XML child positions.
- YAML edit jobs are preferred once a useful sequence of edits should be reproducible.
- Direct CLI operations are appropriate while experimenting.
- Inspect the generated `.preview.png` and `.edit_log.json` after edits.

## SVG editor current scope
The first version intentionally avoids generic bbox-driven align/distribute operations for arbitrary paths/text. It supports reliable structural transforms and edits now; layout geometry can be added later with a dedicated SVG geometry dependency.

## SVG layout tokens and anchors

For SVGs intended for automated layout, prefer semantic IDs on meaningful groups and objects.

The SVG editor YAML can include:
- `tokens` — named spacing/design values
- `anchors` — optional semantic anchors derived from rendered bbox anchors
- `layout` — relative positioning constraints

Automatic anchors for every ID: `left`, `right`, `top`, `bottom`, `center_x`, `center_y`, `width`, `height`. The SVG page exposes the same anchors as `page.*`.

Reference anchors with explicit syntax such as `${card-1.right}`. This syntax works safely with SVG IDs containing dashes.

Recommended workflow:
1. give important SVG groups semantic IDs
2. inspect anchors with `svg_editor/edit.py anchors`
3. put common spacing in `tokens`
4. express placement relatively in `layout`
5. change tokens rather than scattering absolute coordinates
6. inspect `.preview.png` and `.edit_log.json`

Layout is applied after ordinary SVG edit operations so measurements reflect edited text/content.


## QR generator rules
- Keep QR content and styling in YAML/JSON; do not hand-edit generated QR module geometry.
- Prefer `error_correction: H` for branded/logo QR codes.
- Normally preserve a quiet zone of 4 modules.
- Treat automatic decode validation as a gate: if it fails, simplify styling before shipping.
- The HTML configurator and CLI deliberately share the same Python renderer; do not create a separate browser-only QR rendering implementation.

### QR visual configuration

For interactive QR styling, use either `python tools/qr_generator/configurator.py serve` or build a portable page with `python tools/qr_generator/configurator.py build --config <yaml> -o <html>`. The offline page is for visual tuning and YAML export; final QR assets should be regenerated from that YAML with `generate.py` so scan validation is performed by the Python renderer.
