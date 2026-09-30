# Vectorizer

Diagnostic-first raster-to-vector conversion.

## Key rule
This tool always derives geometry from the **visible RGB raster edges**.
It does **not** interpret PNG alpha as the true shape boundary.

If a crop contains multiple objects:
1. run once,
2. inspect `00_all_contours.png`,
3. keep the relevant contour IDs,
4. re-run or use batch YAML with `keep_contours`.

Each run writes both `final.svg`, with all contours in one compound path, and
`final_objects.svg`, with disconnected outer shapes as separate path objects.
Contained counter contours remain grouped with their enclosing object so holes
render correctly.

For explicit semantic grouping, assign every selected contour ID in batch YAML:

```yaml
assets:
  - name: handle
    input: extracted/handle.png
    keep_contours: [1, 2, 3]
    objects:
      letter-at: [1, 3]
      letter-u: [2]
```

Each mapping entry becomes a path ID in `final_objects.svg`. Group an outer
contour with its counter contours to preserve holes. Explicit grouping rejects
unknown, duplicated, or unassigned selected contours.

`compose.py` combines traced SVG layers from YAML. A layer can use `include_ids`
or `exclude_ids` to select specific objects from `final_objects.svg`, making
effects such as selective colored text offsets reproducible:

```yaml
layers:
  - id: cyan-offset
    input: vectorized/handle/final_objects.svg
    x: 8
    y: 6
    fill: '#25F4EE'
    include_ids: [object-contour-1, object-contour-4]
```

Layers also accept `scale`. Composition YAML can define reusable linear
gradients under `gradients`, then reference one with a layer fill such as
`fill: url(#handle-gradient)`.

## CLI
```bash
python tools/vectorizer/vectorize.py input.png --output-dir out
```

Useful options:
- `--threshold` (legacy shared tolerance fallback)
- `--line-threshold` / `--bezier-threshold` (max deviation in px for lines / cubics)
- `--min-contour-length`
- `--max-contours`
- `--keep-contour`
- `--blur-sigma`
- `--foreground-quantile`
- `--membership-mode`
- `--line-min-length` / `--line-min-points`
- `--line-flatness-px` (largest bulge a straight span may show; default 0.3 x line threshold)
- `--corner-angle-deg` (minimum corner turning, default 30; `180` disables corners)
- `--corner-window-px` (arc length per side used to measure corners, default 3)
- `--fill`

`--line-rotation-total-deg`, `--line-angle-deadband-deg`, `--line-rotation-run`
and the `--joint-*` options are still accepted from older YAML but ignored.

## How contours are fitted
Each traced contour becomes lines and cubic Béziers:

1. **Corners**: a point is a corner when it turns at least `--corner-angle-deg`
   over +-`--corner-window-px` and most of that turning happens right at the tip
   (a smooth curve turns in proportion to arc length instead). Corners weaker than
   50 degrees also need straight-ish sides, so pixel stair-steps on a curve do not
   count. The corner point is moved to where the two sides meet, re-sharpening what
   antialiasing rounded, by at most what that rounding can explain.
2. **Lines**: spans of at least 10 px that stay within `--line-threshold` of a
   straight line and show no consistent bulge above `--line-flatness-px`. Shorter
   straight bits are fitted exactly as flat cubics.
3. **Curves**: everything else is fitted Schneider-style: least-squares cubics with
   Newton reparameterization, split at the worst point until within
   `--bezier-threshold`, then neighbouring cubics are merged back where one fits.
4. **Smooth joins**: neighbouring cubics share one tangent (from a local fit), and
   cubics next to a line take the line's direction, so the outline only bends at
   detected corners.

`04_corners.png` shows detected corners (x) and their sharpened vertices (o);
`corner_log.json` lists them.

## Benchmark
```bash
python tools/vectorizer/benchmark.py
python tools/vectorizer/benchmark.py --impl old/vectorize.py --impl tools/vectorizer/vectorize.py --real path/to/crops
```

Scores the fitter on synthetic shapes with exact outlines (circles, polygons,
rounded shapes, Lato glyphs rendered at 16x) and on real crops: max/mean deviation,
line and curve counts, kinks (joins that bend where the outline is smooth) and
sharp corners kept. Run it before and after changing the fitting code.

## Batch YAML
The batch runner accepts defaults plus per-asset overrides. Example:

```yaml
output_dir: vectorized
defaults:
  line_error_px: 0.25
  bezier_error_px: 0.75
  min_contour_length: 5
  max_contours: 0
  line_min_points: 3
assets:
  - name: note_purple
    input: extracted/note_purple.png
    fill: "#C58BCF"
    keep_contours: [1]
```


## Per-asset overrides in batch mode
`batch.py` merges YAML like this:
1. start from `defaults`
2. overlay the current asset block
3. convert the merged values into CLI flags

So this works naturally:
```yaml
defaults:
  line_error_px: 0.25
  bezier_error_px: 0.75

assets:
  - name: note_teal_top_right
    input: extracted_rgb/note_teal_top_right.png
    fill: "#52BEC1"

  - name: blob_coral_right
    input: extracted_rgb/blob_coral_right.png
    fill: "#FDB8A9"
    bezier_error_px: 1.0
    corner_angle_deg: 45
```

You can then rerun only that asset:
```bash
python tools/vectorizer/batch.py examples/rgb_recheck_assets/vectorize.yaml --only blob_coral_right
```
