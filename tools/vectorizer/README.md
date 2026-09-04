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

## CLI
```bash
python tools/vectorizer/vectorize.py input.png --output-dir out
```

Useful options:
- `--threshold` (legacy shared tolerance fallback)
- `--line-threshold` / `--bezier-threshold`
- `--min-contour-length`
- `--max-contours`
- `--keep-contour`
- `--blur-sigma`
- `--foreground-quantile`
- `--membership-mode`
- `--line-rotation-total-deg`
- `--line-min-length`
- `--line-min-points`
- `--joint-refine`
- `--fill`

## Batch YAML
The batch runner accepts defaults plus per-asset overrides. Example:

```yaml
output_dir: vectorized
defaults:
  line_error_px: 0.25
  bezier_error_px: 0.75
  min_contour_length: 5
  max_contours: 0
  line_rotation_total_deg: 2.5
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
  line_rotation_total_deg: 2.5

assets:
  - name: note_teal_top_right
    input: extracted_rgb/note_teal_top_right.png
    fill: "#52BEC1"

  - name: blob_coral_right
    input: extracted_rgb/blob_coral_right.png
    fill: "#FDB8A9"
    bezier_error_px: 1.0
    line_rotation_total_deg: 3.0
```

You can then rerun only that asset:
```bash
python tools/vectorizer/batch.py examples/rgb_recheck_assets/vectorize.yaml --only blob_coral_right
```
