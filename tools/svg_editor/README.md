# SVG Editor

ID-first SVG manipulation and layout for agents.

The tool supports two complementary workflows:

- **direct CLI operations** for fast experimentation
- **YAML jobs** for reproducible edits, design tokens, anchors, and relative layout

Every write produces:

- edited `.svg`
- rendered `.preview.png`
- `.edit_log.json`

## IDs first

Meaningful SVG objects/groups should have stable semantic IDs whenever practical:

```xml
<g id="card-1">...</g>
<rect id="duration-pill-1" .../>
<text id="card-1-title">...</text>
```

Inspect an SVG:

```bash
python tools/svg_editor/edit.py inspect input.svg
```

Assign deterministic fallback IDs to old SVGs:

```bash
python tools/svg_editor/edit.py ensure-ids input.svg output.svg --prefix flyer
```

Fallback IDs are useful for migration, but semantic IDs are preferred for important layout objects.

## Automatic anchors

Every ID can expose anchors from its **rendered bounding box**:

```text
left
right
top
bottom
center_x
center_y
width
height
```

The page exposes the same anchors through `page`.

Inspect them directly:

```bash
python tools/svg_editor/edit.py anchors flyer.svg --id card-1
python tools/svg_editor/edit.py anchors flyer.svg        # page anchors
```

Bounding boxes are measured with **Inkscape's geometry engine**, so paths, groups, text, images, `<use>`, and existing transforms are measured from rendered geometry rather than hand-written estimates.

## YAML tokens

Tokens are named numeric values used by layout expressions:

```yaml
tokens:
  spacing_unit: 8
  page_margin: 5 * spacing_unit
  card_gap: 4 * spacing_unit
  card_padding: 2 * spacing_unit
```

Tokens can reference other tokens with safe arithmetic (`+ - * / // % **`). They do not change the SVG by themselves.

## Anchor references

Use `${id.anchor}` in expressions. The explicit `${...}` syntax is intentional because SVG IDs often contain dashes.

```yaml
layout:
  - id: card-2
    left: "${card-1.right} + card_gap"
    top: "${card-1.top}"
```

Page references work the same way:

```yaml
- id: logo
  center_x: "${page.center_x}"
```

## Custom semantic anchors

Automatic bbox anchors cover most layout. If a design needs a semantic inset or alignment line, define it in YAML:

```yaml
anchors:
  card-1:
    content_left: "${card-1.left} + card_padding"
    content_top: "${card-1.top} + card_padding"
```

Then use it normally:

```yaml
layout:
  - id: card-1-title
    left: "${card-1.content_left}"
    top: "${card-1.content_top} + 10"
```

Custom anchors remain outside the SVG; they do not add invisible geometry.

## Layout rules

A layout rule currently supports **one horizontal** and **one vertical** positioning constraint:

Horizontal:
- `left`
- `right`
- `center_x`

Vertical:
- `top`
- `bottom`
- `center_y`

Example:

```yaml
tokens:
  card_gap: 24
  card_padding: 18

layout:
  - id: card-1
    left: "${page.left} + 48"
    top: 300

  - id: card-2
    left: "${card-1.right} + card_gap"
    top: "${card-1.top}"

  - id: duration-pill-2
    right: "${card-2.right} - card_padding"
    top: "${card-2.top} + card_padding"
```

The editor resolves the target anchor, measures the element's current rendered bbox, and applies the required translation. Layout rules are processed in order, so later rules see geometry changed by earlier rules.

Two horizontal constraints such as `left` + `right` would imply resizing; v1 deliberately rejects that rather than guessing. The same applies to two vertical constraints.

## Job execution order

YAML jobs run in this order:

1. parse SVG
2. optional `ensure_ids`
3. structural/content `operations`
4. resolve `tokens`
5. apply ordered `layout` constraints using automatic/custom anchors
6. write SVG
7. render preview
8. write edit/layout log

This order means layout sees the final text/content/group structure produced by ordinary operations.

## Full YAML example

```yaml
input: flyer.svg
output: flyer-edited.svg
preview: true

tokens:
  spacing_unit: 8
  card_gap: 4 * spacing_unit
  card_padding: 2 * spacing_unit
  pill_right_inset: 2 * spacing_unit

anchors:
  card-1:
    content_left: "${card-1.left} + card_padding"

operations:
  - type: replace_text
    id: card-1-title
    value: "New title"

layout:
  - id: card-2
    left: "${card-1.right} + card_gap"
    top: "${card-1.top}"

  - id: duration-pill-1
    right: "${card-1.right} - pill_right_inset"
```

Run it:

```bash
python tools/svg_editor/edit.py batch layout.yaml
```

The edit log records resolved token values, source expressions, target anchor values, applied `dx/dy`, and bounding boxes before/after each layout rule.

## Direct edit CLI

Examples:

```bash
python tools/svg_editor/edit.py move input.svg output.svg --id star --dx 0 --dy 20
python tools/svg_editor/edit.py scale input.svg output.svg --id logo --sx 0.9 --cx 500 --cy 100
python tools/svg_editor/edit.py rotate input.svg output.svg --id note --angle 12 --cx 100 --cy 80
python tools/svg_editor/edit.py set input.svg output.svg --id pill --attr fill=#7B52C7
python tools/svg_editor/edit.py text input.svg output.svg --id heading --value "New heading"
```

Also supported:

- `delete`
- `duplicate`
- `rename-id`
- `group`
- `ungroup`

## Dependencies

Python:

```bash
pip install -r requirements.txt
```

Layout/bbox features and previews also require the **Inkscape CLI**. It is found via `$INKSCAPE`, then `inkscape` on `PATH`, then the macOS app bundle.

## YAML text source + path export

For deterministic typography, keep the semantic text in the job YAML and export the single SVG with text converted to path geometry:

```yaml
text:
  - id: main-heading
    value: "Prijava za radionice za pametni ukulele"
    font_family: Lato
    font_size: 54
    font_weight: 800

export:
  text_to_path: true
```

`text:` is applied before ordinary operations and layout. `export.text_to_path: true` asks Inkscape to convert every live `<text>`/`<tspan>` object to paths while writing the requested output SVG. There is only one exported SVG; the YAML remains the editable source of truth for text content. No font embedding is performed.

When `text_to_path` is false or omitted, text remains live SVG text.

## PDF export

Export one or more SVGs as a multi-page vector PDF, one page per SVG in the
order given:

```bash
python tools/svg_editor/export_pdf.py front.svg back.svg -o flyer.pdf
```

Embedded `data:image/svg+xml` images are first inlined as nested SVG (the same
step as `inline_svg_images.py`) so they stay vector in the PDF. Text is
converted to paths by default; pass `--keep-text` to keep live text. Inkscape is
found the same way as for the editor (`$INKSCAPE`, `PATH`, macOS app bundle), or
can be given with `--inkscape PATH`. Page sizes come from each SVG.
