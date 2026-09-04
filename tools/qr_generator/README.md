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

- module shapes: `square`, `rounded`, `circle`, `diamond`, `squircle`, `star`, `petal`, `soft_blob`, `capsule_h`, `capsule_v`
- continuous and sampled gradients
- multi-stop gradients
- selective full/waisted bridges
- optional collapsed straight runs
- optional corner fillers
- per-layer finder rounding
- local or continuous finder gradients
- center logo badges/knockouts
- deterministic decorations
- decode validation of the final rendered PNG

See `config.schema.yaml` for the complete contract.

## Generate

```bash
python generate.py examples/playful_instagram.yaml -o /tmp/instagram-qr
```

The command writes `.svg`, `.png`, and `.qr.json`, and exits nonzero if the final rendered result does not decode.

## TikTok reference example

See:

```text
examples/qr_tiktok_reference/tiktok_reference.yaml
```

It demonstrates black connected modules, sparse cyan/pink data accents, asymmetric finder pupils, and a circular center badge with the TikTok SVG mark.
