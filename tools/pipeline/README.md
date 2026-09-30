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
