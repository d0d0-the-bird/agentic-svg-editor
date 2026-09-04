# Asset Extractor

An editable-YAML asset isolation tool for promotional artwork.

The extractor intentionally separates **selection/isolation** from **vectorization**. An agent can adjust one asset's selection, rerun only that asset, inspect the result, and continue downstream without repeating the whole job.

## Batch usage

```bash
python tools/asset_extractor/extract.py batch job/crops.yaml
```

Rerun only one asset after editing its YAML entry:

```bash
python tools/asset_extractor/extract.py batch job/crops.yaml --only megaphone_orange
```

## Selection modes

### 1. `box`

Plain rectangular crop. Best when no unrelated artwork is inside the rectangle.

```yaml
- name: simple_asset
  selection:
    mode: box
    box: [100, 200, 300, 400]
```

### 2. `polygon`

Keeps only a hand-specified polygon inside the crop. Useful when a rectangle cannot avoid neighboring artwork.

```yaml
- name: angled_asset
  selection:
    mode: polygon
    box: [100, 200, 300, 400]
    coordinate_space: source
    points:
      - [110, 240]
      - [270, 210]
      - [285, 380]
      - [130, 390]
    feather: 0.5
```

### 3. `color`

Selects **all pixels** in the box close to the color at a seed point. This is useful for disconnected decorations such as dot clusters or multiple rays.

```yaml
- name: teal_dots
  selection:
    mode: color
    box: [900, 20, 1030, 150]
    seed: [970, 50]
    tolerance: 90
    feather: 0.55
```

### 4. `color_connected`

Selects only the connected matching-color region containing the seed point. This is useful when neighboring artwork has the same color but is disconnected—for example, a megaphone next to sound rays.

```yaml
- name: megaphone
  selection:
    mode: color_connected
    box: [98, 1314, 210, 1435]
    seed: [171, 1339]
    tolerance: 115
    feather: 0.65
```

### 5. `foreground_mask`

Builds a foreground mask by estimating the local background color and keeping pixels sufficiently different from it. This is useful for **multicolor assets on a clean background**.

```yaml
- name: multicolor_logo_rays
  selection:
    mode: foreground_mask
    box: [160, 50, 340, 210]
    threshold: 32
    background: corners
    background_patch: 5
    feather: 0.5
```

Optional: if you want only the connected foreground blob around one seed point, add `seed`.

```yaml
- name: one_multicolor_blob
  selection:
    mode: foreground_mask
    box: [160, 50, 340, 210]
    seed: [250, 120]
    threshold: 28
    background: corners
    connectivity: 8
```

### 6. `polygon_foreground`

Combines a polygon selection with foreground-vs-background masking inside the polygon. This is the practical mode for **small multicolor decorative assets** when you want manual spatial control plus automatic background removal.

```yaml
- name: logo_side_rays
  selection:
    mode: polygon_foreground
    box: [120, 40, 360, 220]
    coordinate_space: source
    points:
      - [145, 70]
      - [330, 65]
      - [320, 185]
      - [140, 190]
    threshold: 30
    background: corners
    polygon_feather: 0.0
    feather: 0.5
```

### 7. `components`

Creates a candidate mask with another selection mode, then splits it into connected components and keeps only the requested ones. This is useful when one crop region contains several detached shapes—for example, the **three rays near the megaphone**.

```yaml
- name: megaphone_rays_left
  selection:
    mode: components
    box: [60, 1270, 260, 1450]
    base:
      mode: foreground_mask
      threshold: 26
      background: corners
    keep_components: [1, 2, 3]
    min_area: 12
    feather: 0.4
```

Instead of naming components manually, you can keep whichever components contain specific seed points:

```yaml
- name: footer_rays
  selection:
    mode: components
    box: [820, 1350, 1100, 1500]
    base:
      mode: color
      seed: [930, 1420]
      tolerance: 80
    keep_seeds:
      - [860, 1410]
      - [905, 1445]
      - [970, 1398]
```

Components are ranked by **descending area**. So `keep_components: [1, 2]` means “keep the two largest components in this cropped candidate mask.” The manifest records all available components with area and bbox diagnostics.

### 8. `mask_file`

Uses an explicitly supplied grayscale mask. White is kept, black is removed. This is the escape hatch for selections that cannot be expressed conveniently by the other modes.

```yaml
- name: custom_asset
  selection:
    mode: mask_file
    box: [100, 200, 300, 400]
    path: custom_mask.png
```

## Background estimation options

`foreground_mask` and `polygon_foreground` support:

- `background: corners` — estimate background from crop corners (default)
- `background: edges` — estimate background from border strips
- `background: samples` — use explicit `background_samples`

Example:

```yaml
selection:
  mode: foreground_mask
  box: [100, 100, 240, 220]
  threshold: 30
  background: samples
  background_samples:
    - [108, 108]
    - [232, 108]
    - [108, 212]
    - [232, 212]
```

## Output

Masked selections are written as RGBA PNGs with transparency. By default the example jobs also save the grayscale mask itself.

Useful diagnostics:

- `00_selection_overlay.png` — all boxes/polygons/seeds over the original image
- `01_asset_contact_sheet.png` — extracted assets on a transparency checkerboard
- `<asset>.mask.png` — actual mask for non-box selections
- `manifest.json` — resolved selection settings, component diagnostics, and output paths

## Transparent border

A small transparent border is useful before vectorization, especially when an asset touches the source image edge. It lets the contour close around the visible shape cleanly.

```yaml
defaults:
  transparent_border: 2
```

## Suggested selection strategy

Use the simplest thing that works:

1. `box` for isolated simple assets
2. `polygon` when only geometry isolation is needed
3. `color_connected` for single-color connected assets
4. `color` for disconnected same-color asset groups
5. `foreground_mask` for multicolor-on-clean-background assets
6. `polygon_foreground` for multicolor assets in tight local regions
7. `components` when you need only some detached pieces from a candidate mask
8. `mask_file` as the final escape hatch

## Iteration rule

If extraction looks wrong, edit the YAML selection and rerun the asset with `--only`. Do not compensate for a bad extraction by changing the vectorizer.

## Expanding the cropped image

If artwork touches the edge of the requested crop, the extractor can add synthetic background pixels **around the crop** without modifying the original source image.

```yaml
- name: edge_touching_asset
  expand_border: 2
  expand_color: '#FFFFFF'
  selection:
    mode: box
    box: [100, 200, 180, 280]
```

Or set defaults for a whole job:

```yaml
defaults:
  expand_border: 2
  expand_color: '#FCF8F0'
```

`expand_border` is measured in pixels on every side. `expand_color` accepts `#RRGGBB` or `[r, g, b]`.

This is especially useful before contour extraction because a shape that touches the crop edge otherwise has no visible background transition on that side.

For plain box crops the expansion is opaque background. For already-masked RGBA selections, the RGB channels store the requested expansion color while the added border remains outside the alpha support mask.
