# QR Generator / Designer

The QR tool is fully **config-driven**. There are no hidden style presets: every visual choice is explicit in YAML/JSON.

## New in v38

### Sparse data-area accents

A small deterministic subset of dark QR modules can be recolored and reshaped. This is useful for TikTok-like cyan / hot-pink accents without applying a rigid checker pattern:

```yaml
modules:
  accents:
    enabled: true
    seed: 31
    probability: 0.048
    colors: ["#25F4EE", "#FE2C55"]
    shapes: [circle, circle, circle, circle, capsule_h, capsule_v]
    scale: 0.78
```

The `shapes` list is intentionally weightable by repetition. In the example above, circles are much more common than horizontal/vertical capsules.

### Per-finder overrides

The three finder markers can now override their pupil/frame color and radii independently:

```yaml
eyes:
  frame_shape: rounded
  pupil_shape: circle
  color: "#111318"
  instances:
    top_left:
      pupil_color: "#FE2C55"
    top_right:
      pupil_color: "#FE2C55"
    bottom_left:
      pupil_color: "#25F4EE"
```

This makes asymmetric branded finder markers possible while preserving a shared base style.

### Circular logo clearing

The existing v36/v37 clearing controls remain available:

```yaml
logo:
  clear_shape: circle
  clear_intersection_mode: any_overlap
  remove_partial_modules: true
  remove_partial_bridges: true
```

## Existing visual controls

The generator also supports:

- module shapes: `square`, `rounded`, `circle`, `diamond`, `squircle`, `star`, `sparkle`, `petal`, `soft_blob`, `capsule_h`, `capsule_v`
- continuous and sampled gradients
- multi-stop gradients
- selective full/waisted bridges
- unified liquid-field contours with organic orthogonal and diagonal adhesion
- optional collapsed straight runs
- optional corner fillers
- per-layer finder rounding
- local or continuous finder gradients
- center logo badges/knockouts
- deterministic decorations
- placed image/traced-SVG decorations (`decorations.images`)
- decode validation of the final rendered PNG

Use unified liquid connectivity when neighboring modules should fuse with the
same organic contour in every direction:

```yaml
modules:
  connectivity:
    enabled: true
    mode: liquid
    liquid:
      supersample: 12
      blur_modules: 0.20
      threshold: 0.37
      simplify_modules: 0.025
      min_contour_area_modules2: 0.02
      orthogonal_bridge_width_modules: 0.62
      fill_dense_junctions: true
      diagonal_seed_modules: 0.22
      diagonal_reach_modules: 0.20
      diagonal_seed_probability: 0.55
      seed: 1
```

Liquid mode rasterizes ordinary dark modules into a supersampled scalar field,
extracts its contours with marching squares, and fits smooth closed SVG paths.
This naturally creates horizontal, vertical, and diagonal adhesion without
separate connector shapes. Sparse colored accents remain independent. Lower
`threshold` or increase `blur_modules` for broader fusion, but treat decode
validation as a hard gate. Diagonal edges are selected only when they continue
orthogonal path endpoints without raising a module above degree two. The seed
width and reach control a rounded pre-blur path segment, while
`diagonal_seed_probability` limits eligible edges deterministically.
`orthogonal_bridge_width_modules` adds constant-width horizontal and vertical
paths selected by `selection.horizontal_probability` and
`selection.vertical_probability`. `fill_dense_junctions` removes pinholes where
four dark modules meet.

See `config.schema.yaml` for the complete contract.

## Generate

```bash
python generate.py examples/playful_instagram.yaml -o /tmp/instagram-qr
```

Create a separate captioned wrapper using the social handle derived from the
exact value stored in the generated QR metadata:

```bash
python caption.py /tmp/instagram-qr.svg -o /tmp/instagram-qr-captioned \
  --accent '#E1306C' --secondary-accent '#F58529'
```

The command writes `.svg`, `.png`, and `.qr.json`, and exits nonzero if the final rendered result does not decode.

## TikTok example

See `examples/playful_tiktok.yaml`. It demonstrates liquid-connected dark
modules with a cyan gradient, pink finder pupils, and a squircle center badge
with the TikTok SVG mark.
