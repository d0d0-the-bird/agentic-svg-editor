#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image, ImageDraw
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    gaussian_filter,
    generate_binary_structure,
    label,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract raster assets using editable YAML selections and masks.")
    sub = p.add_subparsers(dest="mode", required=True)

    batch = sub.add_parser("batch", help="Extract assets described by a YAML file.")
    batch.add_argument("config", type=Path)
    batch.add_argument("--only", action="append", default=[], help="Process only this named asset; repeatable.")

    one = sub.add_parser("one", help="Extract a single rectangular crop from CLI coordinates.")
    one.add_argument("source", type=Path)
    one.add_argument("output", type=Path)
    one.add_argument("--box", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"), required=True)
    one.add_argument("--padding", type=int, default=0)
    return p.parse_args()


def resolve(base: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def parse_rgb_color(value) -> tuple[int, int, int]:
    if value is None:
        return (255, 255, 255)
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("#") and len(v) == 7:
            return tuple(int(v[i:i+2], 16) for i in (1, 3, 5))
        raise ValueError(f"Unsupported color string {value!r}; use #RRGGBB or [r,g,b]")
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(max(0, min(255, int(x)))) for x in value)
    raise ValueError(f"Unsupported color value {value!r}; use #RRGGBB or [r,g,b]")


def expand_cropped_image(extracted: Image.Image, mask: np.ndarray | None, pixels: int, color) -> tuple[Image.Image, np.ndarray | None]:
    """Add a synthetic border around the crop without changing the source image.

    For plain box crops the new border is opaque background. For masked RGBA assets,
    the RGB border still stores the requested background color but alpha remains zero,
    so the isolation mask semantics are preserved.
    """
    pixels = int(pixels)
    if pixels <= 0:
        return extracted, mask
    rgb = parse_rgb_color(color)
    rgba = extracted.convert("RGBA")
    border_alpha = 255 if mask is None and rgba.getextrema()[3] == (255, 255) else 0
    canvas = Image.new("RGBA", (rgba.width + 2*pixels, rgba.height + 2*pixels), (*rgb, border_alpha))
    canvas.paste(rgba, (pixels, pixels), rgba if border_alpha == 0 else None)
    if mask is not None:
        padded = np.zeros((mask.shape[0] + 2*pixels, mask.shape[1] + 2*pixels), dtype=np.float32)
        padded[pixels:-pixels, pixels:-pixels] = mask
        mask = padded
    return canvas, mask


def padded_box(box, padding: int, width: int, height: int):
    l, t, r, b = map(int, box)
    p = int(padding)
    l = max(0, l - p)
    t = max(0, t - p)
    r = min(width, r + p)
    b = min(height, b + p)
    if r <= l or b <= t:
        raise ValueError(f"Invalid crop box after padding: {(l, t, r, b)}")
    return l, t, r, b


def selection_mode(item: dict[str, Any]) -> str:
    sel = item.get("selection") or {}
    return str(sel.get("mode", item.get("mode", "box"))).lower()


def selection_box(item: dict[str, Any]):
    sel = item.get("selection") or {}
    box = sel.get("box", item.get("box"))
    if box is None or len(box) != 4:
        raise ValueError(f"Asset '{item.get('name')}' must provide box: [left, top, right, bottom]")
    return box


# -----------------
# Mask primitives
# -----------------

def polygon_mask(size: tuple[int, int], points_local: list[list[float]], feather: float = 0.0) -> np.ndarray:
    mask_img = Image.new("L", size, 0)
    ImageDraw.Draw(mask_img).polygon([tuple(map(float, p)) for p in points_local], fill=255)
    mask = np.asarray(mask_img, dtype=np.float32) / 255.0
    if feather > 0:
        mask = gaussian_filter(mask, sigma=float(feather))
        mask = np.clip(mask, 0, 1)
    return mask



def color_mask(source_rgb: np.ndarray, box, seed_global, tolerance: float, feather: float = 0.0) -> np.ndarray:
    l, t, r, b = box
    roi = source_rgb[t:b, l:r].astype(np.float32)
    sx, sy = map(int, seed_global)
    lx, ly = sx - l, sy - t
    if lx < 0 or ly < 0 or lx >= roi.shape[1] or ly >= roi.shape[0]:
        raise ValueError(f"Seed {seed_global} lies outside crop box {box}")
    seed_color = roi[ly, lx]
    dist = np.linalg.norm(roi - seed_color[None, None, :], axis=2)
    mask = (dist <= float(tolerance)).astype(np.float32)
    if feather > 0:
        mask = gaussian_filter(mask, sigma=float(feather))
        mask = np.clip(mask, 0, 1)
    return mask


def connected_color_mask(
    source_rgb: np.ndarray,
    box: tuple[int, int, int, int],
    seed_global: tuple[int, int],
    tolerance: float,
    connectivity: int = 8,
    dilate: int = 0,
    erode: int = 0,
    feather: float = 0.0,
) -> np.ndarray:
    l, t, r, b = box
    roi = source_rgb[t:b, l:r].astype(np.float32)
    sx, sy = map(int, seed_global)
    lx, ly = sx - l, sy - t
    if lx < 0 or ly < 0 or lx >= roi.shape[1] or ly >= roi.shape[0]:
        raise ValueError(f"Seed {seed_global} lies outside crop box {box}")

    seed_color = roi[ly, lx]
    dist = np.linalg.norm(roi - seed_color[None, None, :], axis=2)
    candidate = dist <= float(tolerance)

    h, w = candidate.shape
    seen = np.zeros_like(candidate, dtype=bool)
    q = deque()
    if candidate[ly, lx]:
        seen[ly, lx] = True
        q.append((lx, ly))

    nbrs4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    nbrs8 = nbrs4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
    nbrs = nbrs8 if int(connectivity) == 8 else nbrs4

    while q:
        x, y = q.popleft()
        for dx, dy in nbrs:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and candidate[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((nx, ny))

    mask = seen
    if erode > 0:
        mask = binary_erosion(mask, iterations=int(erode))
    if dilate > 0:
        mask = binary_dilation(mask, iterations=int(dilate))
    mask = mask.astype(np.float32)
    if feather > 0:
        mask = gaussian_filter(mask, sigma=float(feather))
        mask = np.clip(mask, 0, 1)
    return mask


# -----------------
# Background/foreground helpers
# -----------------

def _corner_sample(roi: np.ndarray, patch: int) -> np.ndarray:
    h, w = roi.shape[:2]
    p = max(1, int(patch))
    samples = [
        roi[: min(p, h), : min(p, w)].reshape(-1, 3),
        roi[: min(p, h), max(0, w - p) :].reshape(-1, 3),
        roi[max(0, h - p) :, : min(p, w)].reshape(-1, 3),
        roi[max(0, h - p) :, max(0, w - p) :].reshape(-1, 3),
    ]
    return np.concatenate(samples, axis=0)



def estimate_background_color(
    roi: np.ndarray,
    method: str = "corners",
    patch: int = 4,
    samples: list[list[int]] | None = None,
) -> np.ndarray:
    method = str(method).lower()
    if method == "corners":
        px = _corner_sample(roi, patch)
    elif method == "edges":
        p = max(1, int(patch))
        top = roi[:p, :, :].reshape(-1, 3)
        bottom = roi[-p:, :, :].reshape(-1, 3)
        left = roi[:, :p, :].reshape(-1, 3)
        right = roi[:, -p:, :].reshape(-1, 3)
        px = np.concatenate([top, bottom, left, right], axis=0)
    elif method == "samples":
        if not samples:
            raise ValueError("foreground/background sampling method 'samples' requires selection.background_samples")
        pts = []
        h, w = roi.shape[:2]
        for x, y in samples:
            xi = int(np.clip(round(x), 0, w - 1))
            yi = int(np.clip(round(y), 0, h - 1))
            pts.append(roi[yi, xi])
        px = np.asarray(pts, dtype=np.float32)
    else:
        raise ValueError("background method must be one of: corners, edges, samples")
    return np.median(px.astype(np.float32), axis=0)



def apply_binary_post(mask: np.ndarray, erode: int = 0, dilate: int = 0, feather: float = 0.0) -> np.ndarray:
    mask_bool = mask > 0
    if erode > 0:
        mask_bool = binary_erosion(mask_bool, iterations=int(erode))
    if dilate > 0:
        mask_bool = binary_dilation(mask_bool, iterations=int(dilate))
    out = mask_bool.astype(np.float32)
    if feather > 0:
        out = gaussian_filter(out, sigma=float(feather))
        out = np.clip(out, 0, 1)
    return out



def connected_component_from_seed(candidate: np.ndarray, seed_local: tuple[int, int], connectivity: int = 8) -> np.ndarray:
    h, w = candidate.shape
    lx, ly = map(int, seed_local)
    if lx < 0 or ly < 0 or lx >= w or ly >= h:
        raise ValueError(f"Seed {seed_local} lies outside local crop size {(w, h)}")
    if not candidate[ly, lx]:
        return np.zeros_like(candidate, dtype=np.float32)

    nbrs4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    nbrs8 = nbrs4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
    nbrs = nbrs8 if int(connectivity) == 8 else nbrs4

    seen = np.zeros_like(candidate, dtype=bool)
    q = deque([(lx, ly)])
    seen[ly, lx] = True
    while q:
        x, y = q.popleft()
        for dx, dy in nbrs:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and candidate[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((nx, ny))
    return seen.astype(np.float32)



def foreground_mask(
    source_rgb: np.ndarray,
    box: tuple[int, int, int, int],
    threshold: float,
    feather: float = 0.0,
    background: str = "corners",
    background_patch: int = 4,
    background_samples: list[list[int]] | None = None,
    seed_global: tuple[int, int] | None = None,
    connectivity: int = 8,
    erode: int = 0,
    dilate: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    l, t, r, b = box
    roi = source_rgb[t:b, l:r].astype(np.float32)

    local_samples = None
    if background_samples:
        local_samples = [[float(x) - l, float(y) - t] for x, y in background_samples]
    bg = estimate_background_color(roi, method=background, patch=background_patch, samples=local_samples)
    dist = np.linalg.norm(roi - bg[None, None, :], axis=2)
    candidate = dist >= float(threshold)

    if seed_global is not None:
        sx, sy = map(int, seed_global)
        candidate = connected_component_from_seed(candidate, (sx - l, sy - t), connectivity) > 0

    mask = apply_binary_post(candidate.astype(np.float32), erode=erode, dilate=dilate, feather=feather)
    meta = {
        "background": background,
        "background_patch": int(background_patch),
        "background_color": [round(float(v), 3) for v in bg.tolist()],
        "threshold": float(threshold),
    }
    if background_samples:
        meta["background_samples"] = [list(map(int, s)) for s in background_samples]
    if seed_global is not None:
        meta["seed"] = [int(seed_global[0]), int(seed_global[1])]
        meta["connectivity"] = int(connectivity)
    return mask, meta


# -----------------
# Components helpers
# -----------------

def ranked_components(mask_bool: np.ndarray, connectivity: int = 8, min_area: int = 0):
    structure = generate_binary_structure(2, 2 if int(connectivity) == 8 else 1)
    labels, count = label(mask_bool.astype(bool), structure=structure)
    areas: list[tuple[int, int]] = []
    for lab in range(1, count + 1):
        area = int((labels == lab).sum())
        if area >= int(min_area):
            areas.append((lab, area))
    areas.sort(key=lambda x: x[1], reverse=True)
    ranked = np.zeros_like(labels, dtype=np.int32)
    summary = []
    for rank, (lab, area) in enumerate(areas, start=1):
        ranked[labels == lab] = rank
        ys, xs = np.where(labels == lab)
        summary.append({
            "component": rank,
            "original_label": int(lab),
            "area": int(area),
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
        })
    return ranked, summary



def choose_components(
    ranked: np.ndarray,
    keep_components: list[int] | None = None,
    keep_seeds_global: list[list[int]] | None = None,
    box: tuple[int, int, int, int] | None = None,
) -> list[int]:
    keep: set[int] = set()
    if keep_components:
        keep.update(int(v) for v in keep_components if int(v) > 0)
    if keep_seeds_global:
        if box is None:
            raise ValueError("box is required when selecting components by seed")
        l, t, r, b = box
        h, w = ranked.shape
        for x, y in keep_seeds_global:
            lx = int(x) - l
            ly = int(y) - t
            if 0 <= lx < w and 0 <= ly < h:
                comp = int(ranked[ly, lx])
                if comp > 0:
                    keep.add(comp)
    return sorted(keep)



def load_external_mask(base: Path, mask_path: str | Path, box, target_size) -> np.ndarray:
    p = resolve(base, mask_path)
    m = Image.open(p).convert("L")
    if m.size != target_size:
        l, t, r, b = box
        if m.width >= r and m.height >= b:
            m = m.crop((l, t, r, b))
        else:
            raise ValueError(f"Mask {p} size {m.size} does not match crop size {target_size} or source coordinates")
    return np.asarray(m, dtype=np.float32) / 255.0


# -----------------
# Main selection application
# -----------------

def apply_selection(
    source: Image.Image,
    source_np: np.ndarray,
    base: Path,
    item: dict[str, Any],
    box: tuple[int, int, int, int],
):
    mode = selection_mode(item)
    sel = item.get("selection") or {}
    crop = source.crop(box).convert("RGBA")
    l, t, r, b = box
    size = (r - l, b - t)
    mask = None

    if mode in ("box", "rect", "rectangle"):
        return crop, None, {"mode": "box"}

    if mode == "polygon":
        points = sel.get("points") or item.get("points")
        if not points or len(points) < 3:
            raise ValueError(f"Asset '{item['name']}' polygon mode requires at least 3 points")
        coordinate_space = str(sel.get("coordinate_space", "source")).lower()
        if coordinate_space == "source":
            points_local = [[float(x) - l, float(y) - t] for x, y in points]
        elif coordinate_space == "crop":
            points_local = points
        else:
            raise ValueError("polygon coordinate_space must be 'source' or 'crop'")
        mask = polygon_mask(size, points_local, float(sel.get("feather", 0)))
        meta = {"mode": "polygon", "points": points, "coordinate_space": coordinate_space}

    elif mode in ("color", "color_all", "sample_color"):
        seed = sel.get("seed") or item.get("seed")
        if seed is None or len(seed) != 2:
            raise ValueError(f"Asset '{item['name']}' color mode requires seed: [x, y]")
        mask = color_mask(
            source_np,
            box,
            tuple(seed),
            tolerance=float(sel.get("tolerance", 45)),
            feather=float(sel.get("feather", 0.6)),
        )
        meta = {
            "mode": "color",
            "seed": list(map(int, seed)),
            "tolerance": float(sel.get("tolerance", 45)),
            "feather": float(sel.get("feather", 0.6)),
        }

    elif mode in ("color_connected", "connected_color", "seed_color"):
        seed = sel.get("seed") or item.get("seed")
        if seed is None or len(seed) != 2:
            raise ValueError(f"Asset '{item['name']}' color_connected mode requires seed: [x, y]")
        mask = connected_color_mask(
            source_np,
            box,
            tuple(seed),
            tolerance=float(sel.get("tolerance", 45)),
            connectivity=int(sel.get("connectivity", 8)),
            dilate=int(sel.get("dilate", 0)),
            erode=int(sel.get("erode", 0)),
            feather=float(sel.get("feather", 0.6)),
        )
        meta = {
            "mode": "color_connected",
            "seed": list(map(int, seed)),
            "tolerance": float(sel.get("tolerance", 45)),
            "connectivity": int(sel.get("connectivity", 8)),
            "dilate": int(sel.get("dilate", 0)),
            "erode": int(sel.get("erode", 0)),
            "feather": float(sel.get("feather", 0.6)),
        }

    elif mode in ("foreground_mask", "foreground", "bg_subtract"):
        seed = sel.get("seed") or item.get("seed")
        mask, fg_meta = foreground_mask(
            source_np,
            box,
            threshold=float(sel.get("threshold", 35)),
            feather=float(sel.get("feather", 0.6)),
            background=str(sel.get("background", "corners")),
            background_patch=int(sel.get("background_patch", 4)),
            background_samples=sel.get("background_samples"),
            seed_global=tuple(seed) if seed is not None else None,
            connectivity=int(sel.get("connectivity", 8)),
            dilate=int(sel.get("dilate", 0)),
            erode=int(sel.get("erode", 0)),
        )
        meta = {"mode": "foreground_mask", **fg_meta, "feather": float(sel.get("feather", 0.6))}

    elif mode in ("polygon_foreground", "polygon_keep_nonbackground"):
        points = sel.get("points") or item.get("points")
        if not points or len(points) < 3:
            raise ValueError(f"Asset '{item['name']}' polygon_foreground mode requires at least 3 points")
        coordinate_space = str(sel.get("coordinate_space", "source")).lower()
        if coordinate_space == "source":
            points_local = [[float(x) - l, float(y) - t] for x, y in points]
        elif coordinate_space == "crop":
            points_local = points
        else:
            raise ValueError("polygon coordinate_space must be 'source' or 'crop'")
        poly = polygon_mask(size, points_local, float(sel.get("polygon_feather", 0)))
        fg, fg_meta = foreground_mask(
            source_np,
            box,
            threshold=float(sel.get("threshold", 35)),
            feather=0.0,
            background=str(sel.get("background", "corners")),
            background_patch=int(sel.get("background_patch", 4)),
            background_samples=sel.get("background_samples"),
            seed_global=None,
            connectivity=int(sel.get("connectivity", 8)),
            dilate=int(sel.get("dilate", 0)),
            erode=int(sel.get("erode", 0)),
        )
        mask = np.clip(poly * fg, 0, 1)
        if float(sel.get("feather", 0)) > 0:
            mask = gaussian_filter(mask, sigma=float(sel.get("feather")))
            mask = np.clip(mask, 0, 1)
        meta = {
            "mode": "polygon_foreground",
            "points": points,
            "coordinate_space": coordinate_space,
            "polygon_feather": float(sel.get("polygon_feather", 0)),
            "feather": float(sel.get("feather", 0)),
            **fg_meta,
        }

    elif mode in ("components", "connected_components"):
        base_sel = sel.get("base") or sel.get("base_selection") or item.get("base_selection")
        if not isinstance(base_sel, dict):
            raise ValueError(
                f"Asset '{item['name']}' components mode requires selection.base describing how to build the candidate mask"
            )
        nested = {"name": f"{item.get('name','asset')}__base", "selection": dict(base_sel)}
        nested["selection"].setdefault("box", list(box))
        _img, base_mask, base_meta = apply_selection(source, source_np, base, nested, box)
        if base_mask is None:
            raise ValueError("components base selection must produce a mask, not a plain box crop")

        bin_thresh = float(sel.get("mask_threshold", 0.5))
        ranked, summary = ranked_components(
            base_mask >= bin_thresh,
            connectivity=int(sel.get("connectivity", 8)),
            min_area=int(sel.get("min_area", 0)),
        )
        keep = choose_components(
            ranked,
            keep_components=sel.get("keep_components") or sel.get("keep"),
            keep_seeds_global=sel.get("keep_seeds") or ( [sel.get("seed")] if sel.get("seed") else None ),
            box=box,
        )
        if not keep:
            raise ValueError(
                f"Asset '{item['name']}' components mode did not resolve any components to keep. "
                "Provide keep_components or keep_seeds."
            )
        mask = np.isin(ranked, np.asarray(keep, dtype=np.int32)).astype(np.float32)
        mask = apply_binary_post(
            mask,
            erode=int(sel.get("erode", 0)),
            dilate=int(sel.get("dilate", 0)),
            feather=float(sel.get("feather", 0.6)),
        )
        meta = {
            "mode": "components",
            "base": base_meta,
            "mask_threshold": bin_thresh,
            "keep_components": keep,
            "connectivity": int(sel.get("connectivity", 8)),
            "min_area": int(sel.get("min_area", 0)),
            "available_components": summary,
            "feather": float(sel.get("feather", 0.6)),
        }
        if sel.get("keep_seeds"):
            meta["keep_seeds"] = [list(map(int, s)) for s in sel.get("keep_seeds")]
        elif sel.get("seed"):
            meta["keep_seeds"] = [list(map(int, sel.get("seed")))]

    elif mode in ("mask", "mask_file", "external_mask"):
        mask_path = sel.get("path") or sel.get("mask") or item.get("mask")
        if not mask_path:
            raise ValueError(f"Asset '{item['name']}' mask_file mode requires path")
        mask = load_external_mask(base, mask_path, box, size)
        if float(sel.get("feather", 0)) > 0:
            mask = gaussian_filter(mask, sigma=float(sel.get("feather")))
            mask = np.clip(mask, 0, 1)
        meta = {"mode": "mask_file", "path": str(mask_path)}

    else:
        raise ValueError(f"Unknown selection mode '{mode}' for asset '{item['name']}'")

    rgba = np.asarray(crop, dtype=np.uint8).copy()
    rgba[:, :, 3] = np.clip(mask * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA"), mask, meta


# -----------------
# Diagnostics & batch execution
# -----------------

def save_overlay(source: Image.Image, assets: list[dict[str, Any]], path: Path):
    overlay = source.copy().convert("RGB")
    draw = ImageDraw.Draw(overlay)
    colors = ["#0057D9", "#D9007F", "#008A5A", "#A76400", "#6C4CCF", "#B40000", "#007D8A"]

    for idx, asset in enumerate(assets):
        l, t, r, b = asset["resolved_box"]
        color = colors[idx % len(colors)]
        mode = asset.get("selection", {}).get("mode", "box")
        draw.rectangle([l, t, r, b], outline=color, width=3)

        sel = asset.get("selection", {})
        if mode in ("polygon", "polygon_foreground", "polygon_keep_nonbackground") and sel.get("points"):
            pts = [tuple(map(float, p)) for p in sel["points"]]
            if str(sel.get("coordinate_space", "source")).lower() == "crop":
                pts = [(x + l, y + t) for x, y in pts]
            draw.line(pts + [pts[0]], fill=color, width=4)
        if mode in ("color", "color_connected", "foreground_mask") and sel.get("seed"):
            x, y = sel["seed"]
            rr = 6
            draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill="white", outline=color, width=3)
        if mode == "components":
            for s in sel.get("keep_seeds", []) or []:
                x, y = s
                rr = 6
                draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill="white", outline=color, width=3)

        label_txt = f'{idx + 1}: {asset["name"]} ({mode})'
        bbox = draw.textbbox((0, 0), label_txt)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        y0 = max(0, t - th - 7)
        draw.rectangle([l, y0, min(overlay.width - 1, l + tw + 7), y0 + th + 6], fill="white")
        draw.text((l + 3, y0 + 2), label_txt, fill=color)

    path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(path)



def checkerboard_rgba(im: Image.Image, size=(300, 260)) -> Image.Image:
    im = im.convert("RGBA")
    scale = min(size[0] / im.width, size[1] / im.height)
    thumb = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))))
    bg = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(bg)
    s = 16
    for y in range(0, size[1], s):
        for x in range(0, size[0], s):
            if (x // s + y // s) % 2:
                d.rectangle([x, y, x + s - 1, y + s - 1], fill="#EAEAEA")
    x = (size[0] - thumb.width) // 2
    y = (size[1] - thumb.height) // 2
    bg.paste(thumb, (x, y), thumb)
    return bg



def make_contact_sheet(items: list[tuple[str, Path]], output: Path):
    if not items:
        return
    image_w, image_h, label_h, gap = 300, 260, 42, 16
    cards = []
    for name, path in items:
        im = Image.open(path)
        preview = checkerboard_rgba(im, (image_w, image_h))
        card = Image.new("RGB", (image_w, label_h + image_h), "white")
        card.paste(preview, (0, label_h))
        d = ImageDraw.Draw(card)
        d.text((8, 8), name, fill="black")
        d.rectangle([0, label_h, image_w - 1, label_h + image_h - 1], outline="#D8D8D8")
        cards.append(card)
    cols = min(3, len(cards))
    rows = (len(cards) + cols - 1) // cols
    sheet = Image.new(
        "RGB",
        (cols * image_w + (cols - 1) * gap, rows * (label_h + image_h) + (rows - 1) * gap),
        "white",
    )
    for i, c in enumerate(cards):
        sheet.paste(c, ((i % cols) * (image_w + gap), (i // cols) * (label_h + image_h + gap)))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)



def selection_meta_for_manifest(item: dict[str, Any], box: tuple[int, int, int, int]) -> dict[str, Any]:
    mode = selection_mode(item)
    sel = item.get("selection") or {}
    meta: dict[str, Any] = {"mode": mode}
    if mode in ("polygon", "polygon_foreground", "polygon_keep_nonbackground"):
        meta.update({"points": sel.get("points", item.get("points")), "coordinate_space": sel.get("coordinate_space", "source")})
    elif mode in ("color", "color_all", "sample_color", "color_connected", "connected_color", "seed_color", "foreground_mask"):
        seed = sel.get("seed", item.get("seed"))
        if seed is not None:
            meta["seed"] = seed
    elif mode in ("components", "connected_components"):
        keep_seeds = sel.get("keep_seeds") or ([sel.get("seed")] if sel.get("seed") else None)
        if keep_seeds:
            meta["keep_seeds"] = keep_seeds
        if sel.get("keep_components") or sel.get("keep"):
            meta["keep_components"] = sel.get("keep_components") or sel.get("keep")
        if isinstance(sel.get("base"), dict):
            meta["base"] = {"mode": sel["base"].get("mode", "?")}
    elif mode in ("mask", "mask_file", "external_mask"):
        meta.update({"path": sel.get("path") or sel.get("mask") or item.get("mask")})
    return meta



def run_batch(config_path: Path, only: list[str] | None = None):
    config_path = config_path.resolve()
    base = config_path.parent
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    source_path = resolve(base, cfg["source"])
    source = Image.open(source_path).convert("RGB")
    source_np = np.asarray(source, dtype=np.uint8)
    output_dir = resolve(base, cfg.get("output_dir", "extracted"))
    output_dir.mkdir(parents=True, exist_ok=True)
    defaults = cfg.get("defaults", {}) or {}
    default_padding = int(defaults.get("padding", 0))
    ext = str(defaults.get("extension", "png")).lstrip(".")
    save_masks = bool(defaults.get("save_masks", True))
    default_transparent_border = int(defaults.get("transparent_border", 0))
    default_expand_border = int(defaults.get("expand_border", 0))
    default_expand_color = defaults.get("expand_color", "#FFFFFF")
    only_set = set(only or [])

    processed = []
    manifest = []
    overlay_records = []
    for item in cfg.get("assets", []):
        if item.get("enabled", True) is False:
            continue
        name = str(item["name"])
        raw_box = selection_box(item)
        padding = int(item.get("padding", default_padding))
        box = padded_box(raw_box, padding, source.width, source.height)
        output_name = item.get("output", f"{name}.{ext}")
        output_path = output_dir / output_name

        record = {
            "name": name,
            "source": str(source_path),
            "requested_box": list(map(int, raw_box)),
            "padding": padding,
            "resolved_box": list(box),
            "output": str(output_path),
            "selection": selection_meta_for_manifest(item, box),
            "notes": item.get("notes", ""),
        }
        overlay_records.append(record)
        manifest.append(record)
        if only_set and name not in only_set:
            continue

        extracted, mask, full_meta = apply_selection(source, source_np, base, item, box)
        record["selection"] = full_meta

        expand_border = int(item.get("expand_border", default_expand_border))
        expand_color = item.get("expand_color", default_expand_color)
        record["expand_border"] = expand_border
        record["expand_color"] = list(parse_rgb_color(expand_color))
        extracted, mask = expand_cropped_image(extracted, mask, expand_border, expand_color)

        transparent_border = int(item.get("transparent_border", default_transparent_border))
        record["transparent_border"] = transparent_border
        if transparent_border > 0:
            bordered = Image.new(
                "RGBA", (extracted.width + 2 * transparent_border, extracted.height + 2 * transparent_border), (0, 0, 0, 0)
            )
            bordered.paste(extracted, (transparent_border, transparent_border), extracted)
            extracted = bordered
            if mask is not None:
                padded = np.zeros((mask.shape[0] + 2 * transparent_border, mask.shape[1] + 2 * transparent_border), dtype=np.float32)
                padded[transparent_border:-transparent_border, transparent_border:-transparent_border] = mask
                mask = padded
        output_path.parent.mkdir(parents=True, exist_ok=True)
        extracted.save(output_path)
        processed.append((name, output_path))
        if mask is not None and save_masks:
            mask_path = output_dir / f"{name}.mask.png"
            Image.fromarray(np.clip(mask * 255, 0, 255).astype(np.uint8), "L").save(mask_path)
            record["mask_output"] = str(mask_path)

    if only_set:
        known = {m["name"] for m in manifest}
        missing = only_set - known
        if missing:
            raise ValueError(f"Unknown asset name(s) in --only: {sorted(missing)}")

    overlay_path = output_dir / "00_selection_overlay.png"
    save_overlay(source, overlay_records, overlay_path)
    sheet_path = output_dir / "01_asset_contact_sheet.png"
    if processed:
        make_contact_sheet(processed, sheet_path)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "config": str(config_path),
                "source": str(source_path),
                "source_size": [source.width, source.height],
                "processed_only": sorted(only_set),
                "assets": manifest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Source: {source_path}")
    print(f"Extracted {len(processed)} assets into {output_dir}")
    if only_set:
        print(f"Only: {sorted(only_set)}")
    print(f"Selection overlay: {overlay_path.name}")
    if processed:
        print(f"Contact sheet: {sheet_path.name}")
    print(f"Manifest: {manifest_path.name}")



def run_one(source_path: Path, output_path: Path, box, padding: int):
    source = Image.open(source_path).convert("RGB")
    box = padded_box(box, padding, source.width, source.height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source.crop(box).save(output_path)
    print(output_path)



def main():
    args = parse_args()
    if args.mode == "batch":
        run_batch(args.config, args.only)
    else:
        run_one(args.source, args.output, args.box, args.padding)


if __name__ == "__main__":
    main()
