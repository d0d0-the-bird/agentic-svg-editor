# Pipeline runner

Runs a recipe: one YAML listing the tool steps that build an asset or
composition. The recipe is the only file to edit; everything it produces
should go into a `generated/` folder next to it so that folder can be deleted
and rebuilt.

```bash
python tools/pipeline/run.py path/to/recipe.yaml
python tools/pipeline/run.py path/to/recipe.yaml --only qr   # one step
python tools/pipeline/run.py path/to/recipe.yaml --list      # show steps
```

## Recipe format

```yaml
description: TikTok QR code with traced handle lettering.
notes:
  - free-form provenance notes, ignored by the runner
steps:
  - id: handle-extract
    tool: extract
    config:              # the tool's usual YAML config, inline
      source: ../sources/uploads/handle.png
      output_dir: generated/handle/extracted
      assets: [...]
  - id: qr
    tool: qr
    config: {value: https://example.com, ...}
    args: [-o, generated/qr]   # extra CLI arguments
```

Relative paths resolve against the recipe file, exactly as they would in a
standalone tool config: the runner writes each step's `config` to a temporary
YAML beside the recipe and runs the tool with the recipe directory as its
working directory.

| tool | runs | takes |
|---|---|---|
| `extract` | `asset_extractor/extract.py batch` | config |
| `vectorize` | `vectorizer/batch.py` | config |
| `compose` | `vectorizer/compose.py` | config |
| `qr` | `qr_generator/generate.py` | config, args |
| `caption` | `qr_generator/caption.py` | args |
| `embed-images` | `svg_editor/embed_images.py` | config |
| `svg-edit` | `svg_editor/edit.py batch` | config |
| `export-pdf` | `svg_editor/export_pdf.py` | args |
| `recipe` | this runner on other recipes | args (recipe paths) |

Use `recipe` steps for a project-level recipe that builds its parts in order.

## Project layout convention

```
<project>/
  <project>.yaml          builds everything (recipe steps)
  sources/                originals, never edited
  assets/<name>/<name>.yaml + generated/
  compositions/<name>/<name>.yaml + generated/
  generated/              project-level outputs (e.g. print PDF)
```

## Viewer

```bash
python tools/pipeline/view.py path/to/project.yaml          # opens http://127.0.0.1:8766/
python tools/pipeline/view.py path/to/project.yaml --port 9000 --no-browser
python tools/pipeline/view.py path/to/project.yaml --host 0.0.0.0   # phones/tablets on the LAN or VPN
```

By default the server only listens on this machine. With `--host 0.0.0.0` it
listens on every interface and prints a URL per address (LAN, VPN). Those URLs
carry an access token: requests without it are refused, and the first visit
sets a cookie so the page keeps working. The token is random per run; pass
`--token VALUE` for a stable URL you can bookmark. Anyone with the URL can view
the recipe's files while the server runs.

On narrow screens the tree and details open as drawers (Recipes / Details),
one finger pans, two fingers pinch-zoom, and tapping a Made-from entry
highlights it (the › button opens its recipe).

A local server that follows the recipe (and nested `recipe` steps).

It opens on the project's **pages**, read like a PDF: every page stacked
vertically with zoom (− / Fit width / +, Ctrl/⌘ + scroll, pinch on touch) and a
page counter; the details panel lists the pages to jump between them. Pages are
the inputs of the project's `export-pdf` step, in order; without one, each
child recipe's final output is a page. Click a project header in the tree to
return to its pages, or a page's Inspect button to open it on the canvas.

Everything else is inspected on the canvas:

- the recipe tree, grouped by project: recipe steps become section labels
  (assets, compositions), recipes collapse to show their steps, and each tool
  has a colour dot; click a recipe or step to display its output, or a gallery
  when a step has many outputs (extract, vectorize)
- generated SVGs loaded inline with wheel zoom and drag pan
- **Made from**: the inputs behind the displayed output, each tagged with the
  element ID it became (compose layer IDs, embed-images IDs, QR logo and
  decoration IDs); hover to highlight it, click to open the recipe that made it
- **Used by**: the steps that consume the displayed file
- **Elements**: every ID in the SVG; hover to highlight, click to zoom to it.
  Clicking in the SVG selects the innermost ID; click again to step outward.

Inputs under another project's `<unit>/generated/` are followed to that unit's
`<unit>.yaml`, so a flyer can link through to the QR project it uses. The page
rescans when the window regains focus or on Reload, so rebuild with `run.py`
and switch back. Only files referenced by the recipes are served.
