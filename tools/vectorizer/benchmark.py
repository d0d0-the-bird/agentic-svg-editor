#!/usr/bin/env python3
"""Benchmark the vectorizer's contour fitting against exact outlines.

Synthetic shapes are drawn at 16x resolution and area-averaged down, so the
low-res image has true antialiasing and the high-res mask gives the exact
outline. Each fitted contour is scored for:

- accuracy: distance from the fitted path to the true outline (max / mean px)
- size: number of line and cubic segments
- smoothness: joins that bend by more than 5 degrees where the outline is smooth
- corners: true corners that come out as a sharp join (within 1 px, >= 25 deg)

Real images (``--real DIR``) have no ground truth; they are scored against their
own traced contour, and joins bending 5-25 degrees are counted as suspect kinks.

    python tools/vectorizer/benchmark.py
    python tools/vectorizer/benchmark.py --impl old_vectorize.py --impl tools/vectorizer/vectorize.py
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial import cKDTree
from skimage.measure import find_contours

SS = 16  # supersampling factor for ground truth
SMOOTH_KINK_DEG = 5.0
CORNER_MIN_DEG = 25.0
CORNER_RADIUS_PX = 1.0

# Settings used by the ucoolele QR recipes (the repo baseline).
PARAMS = dict(line_threshold=0.25, bezier_threshold=0.75, rotation_total_deg=2.5,
              line_min_points=3, line_min_length=5.0)


# ---------------------------------------------------------------- shapes
def polygon_points(cx, cy, radius_fn, n=2000):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    r = radius_fn(t)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def rotated_rect(cx, cy, w, h, angle_deg, radius=0.0, n_arc=200):
    """Rounded rectangle outline (radius 0 = sharp), rotated about its centre."""
    hw, hh = w / 2, h / 2
    pts = []
    if radius <= 0:
        pts = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    else:
        for (sx, sy, a0) in [(hw - radius, -hh + radius, -90), (hw - radius, hh - radius, 0),
                             (-hw + radius, hh - radius, 90), (-hw + radius, -hh + radius, 180)]:
            for a in np.linspace(np.radians(a0), np.radians(a0 + 90), n_arc):
                pts.append((sx + radius * np.cos(a), sy + radius * np.sin(a)))
    pts = np.array(pts, dtype=float)
    a = np.radians(angle_deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return pts @ rot.T + [cx, cy]


def star(cx, cy, r_out, r_in, n=5, angle_deg=-90):
    pts = []
    for k in range(2 * n):
        r = r_out if k % 2 == 0 else r_in
        a = np.radians(angle_deg) + k * np.pi / n
        pts.append((cx + r * np.cos(a), cy + r * np.sin(a)))
    return np.array(pts)


def draw_polys(size, polys):
    """Render filled polygons (low-res coords) at SS x; return hi-res boolean mask."""
    w, h = size
    im = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(im)
    for poly, fill in polys:
        d.polygon([((x + 0.5) * SS, (y + 0.5) * SS) for x, y in poly], fill=fill)
    return np.asarray(im) > 127


def draw_text(size, text, font_px):
    w, h = size
    im = Image.new("L", (w * SS, h * SS), 0)
    font = ImageFont.truetype(str(Path.home() / "Library/Fonts/Lato-Black.ttf"), font_px * SS)
    d = ImageDraw.Draw(im)
    d.text((w * SS / 2, h * SS / 2), text, font=font, fill=255, anchor="mm")
    mask = np.asarray(im) > 127
    # Crop to the glyph plus a 5 px margin, on whole low-res pixels.
    ys, xs = np.nonzero(mask)
    m = 5 * SS
    y0, x0 = max(0, (ys.min() - m) // SS * SS), max(0, (xs.min() - m) // SS * SS)
    y1, x1 = min(mask.shape[0], -(-(ys.max() + m) // SS) * SS), min(mask.shape[1], -(-(xs.max() + m) // SS) * SS)
    return mask[y0:y1, x0:x1]


def synthetic_cases():
    """(name, hi-res mask, true corner points or None to estimate from the outline)."""
    cases = []
    for r in (10, 25, 45):
        size = int(2 * r + 20)
        cases.append((f"circle r={r}", draw_polys((size, size), [(polygon_points(size / 2, size / 2, lambda t: r + 0 * t), 255)]), []))
    cases.append(("ellipse 50x22 rot 30", draw_polys((120, 120), [(polygon_points(60, 60, lambda t: 50 * 22 / np.sqrt((22 * np.cos(t - 0.52)) ** 2 + (50 * np.sin(t - 0.52)) ** 2)), 255)]), []))
    cases.append(("blob 3-lobe", draw_polys((110, 110), [(polygon_points(55, 55, lambda t: 38 + 9 * np.cos(3 * t)), 255)]), []))
    rr = rotated_rect(60, 45, 90, 56, 8, radius=14)
    cases.append(("rounded rect rot 8", draw_polys((120, 90), [(rr, 255)]), []))
    sq = rotated_rect(50, 50, 60, 60, 20)
    cases.append(("square rot 20", draw_polys((100, 100), [(sq, 255)]), sq))
    for sides in (6, 8):
        poly = star(50, 50, 38, 38, sides // 2, angle_deg=-90 + 7)[:sides] if False else np.array(
            [(50 + 38 * np.cos(np.radians(7 + 360 * k / sides)), 50 + 38 * np.sin(np.radians(7 + 360 * k / sides))) for k in range(sides)])
        cases.append((f"{sides}-gon", draw_polys((100, 100), [(poly, 255)]), poly))
    st = star(60, 62, 50, 20)
    cases.append(("star", draw_polys((120, 120), [(st, 255)]), st))
    # Mixed: rectangle with a semicircular end (lines + arc + corners).
    cap = np.vstack([[(20, 30)], [(70, 30)], polygon_points(70, 55, lambda t: 25 + 0 * t, 400)[300:] if False else
                     np.array([(70 + 25 * np.cos(a), 55 + 25 * np.sin(a)) for a in np.linspace(-np.pi / 2, np.pi / 2, 300)]),
                     [(20, 80)]])
    cases.append(("D shape", draw_polys((110, 110), [(cap, 255)]), np.array([(20, 30), (20, 80)])))
    for ch in ("S", "a", "g", "@"):
        cases.append((f"Lato '{ch}' 48px", draw_text((70, 70), ch, 48), None))
    cases.append(("Lato 'k' 24px", draw_text((40, 40), "k", 24), None))
    return cases


def downsample(mask):
    h, w = mask.shape
    return mask.reshape(h // SS, SS, w // SS, SS).mean(axis=(1, 3))


def true_outlines(mask):
    """High-res iso-contours mapped to low-res pixel-centre coordinates."""
    out = []
    padded = np.pad(mask.astype(float), 1)
    for c in find_contours(padded, 0.5):
        xy = np.column_stack([c[:, 1] - 1, c[:, 0] - 1])
        xy = (xy + 0.5) / SS - 0.5
        if len(xy) > 20:
            out.append(xy)
    return out


def estimate_corners(outline, window_px=0.8, min_deg=40.0):
    """Corners of a dense true outline: sharp, concentrated turning."""
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(outline, axis=0), axis=1))])
    total = s[-1]
    def at(dist):
        return np.column_stack([np.interp(dist % total, s, outline[:, 0]), np.interp(dist % total, s, outline[:, 1])])
    a, b = at(s - window_px), at(s + window_px)
    d1, d2 = outline - a, b - outline
    ang = np.degrees(np.abs(np.arctan2(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0], (d1 * d2).sum(1))))
    corners = []
    step = max(1, int(len(outline) / total * window_px))
    for i in np.argsort(-ang):
        if ang[i] < min_deg:
            break
        if all(np.linalg.norm(outline[i] - c) > 2 * window_px for c in corners):
            corners.append(outline[i])
    return np.array(corners) if corners else np.zeros((0, 2))


# ---------------------------------------------------------------- scoring
def load_impl(path: Path):
    spec = importlib.util.spec_from_file_location(f"impl_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def fit_image(impl, rgb):
    alpha, _, _ = impl.estimate_membership(rgb, 0.42, 85.0, "distance")
    records = impl.extract_contours(alpha, 5.0, 0)
    contours, _, _ = impl.orient_solid_left([r["points"] for r in records], alpha, 1.5)
    p = PARAMS
    fits = [impl.hybrid_fit(pts, p["line_threshold"], p["bezier_threshold"], 0.0, 0, p["rotation_total_deg"],
                            p["line_min_points"], p["line_min_length"], True, 6.0, 8.0, 3.0) for pts in contours]
    return contours, fits


def segment_samples(seg, step=0.2):
    typ, data = seg[0], seg[1]
    if typ == "L":
        a, b = np.asarray(data[0]), np.asarray(data[1])
        n = max(2, int(np.linalg.norm(b - a) / step) + 1)
        u = np.linspace(0, 1, n)[:, None]
        return a + u * (b - a)
    p0, p1, p2, p3 = (np.asarray(c) for c in data)
    approx = np.linalg.norm(p1 - p0) + np.linalg.norm(p2 - p1) + np.linalg.norm(p3 - p2)
    u = np.linspace(0, 1, max(4, int(approx / step) + 1))[:, None]
    return (1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * p1 + 3 * (1 - u) * u ** 2 * p2 + u ** 3 * p3


def seg_tangents(seg):
    typ, data = seg[0], seg[1]
    if typ == "L":
        d = np.asarray(data[1]) - np.asarray(data[0])
        return d, d
    p0, p1, p2, p3 = (np.asarray(c) for c in data)
    t0 = p1 - p0 if np.linalg.norm(p1 - p0) > 1e-9 else p2 - p0
    t1 = p3 - p2 if np.linalg.norm(p3 - p2) > 1e-9 else p3 - p1
    return t0, t1


def seg_start(seg):
    return np.asarray(seg[1][0])


def angle_deg(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(a, b) / (na * nb), -1, 1))))


def score_fit(combined, truth, corners):
    samples = np.vstack([segment_samples(s) for s in combined])
    truth_tree = cKDTree(truth)
    d_fit, _ = truth_tree.query(samples)
    d_cov, _ = cKDTree(samples).query(truth)
    joins = []
    for i, seg in enumerate(combined):
        nxt = combined[(i + 1) % len(combined)]
        joins.append((seg_start(nxt), angle_deg(seg_tangents(seg)[1], seg_tangents(nxt)[0])))
    kinks = kept = 0
    for point, ang in joins:
        near_corner = corners is not None and len(corners) and np.min(np.linalg.norm(corners - point, axis=1)) <= CORNER_RADIUS_PX
        if corners is None:
            kinks += SMOOTH_KINK_DEG < ang < CORNER_MIN_DEG
        elif not near_corner and ang > SMOOTH_KINK_DEG:
            kinks += 1
    if corners is not None:
        for c in corners:
            kept += any(np.linalg.norm(point - c) <= CORNER_RADIUS_PX and ang >= CORNER_MIN_DEG for point, ang in joins)
    return {
        "max": float(max(d_fit.max(), d_cov.max())), "mean": float(d_fit.mean()),
        "lines": sum(1 for s in combined if s[0] == "L"), "curves": sum(1 for s in combined if s[0] != "L"),
        "kinks": int(kinks), "joins": len(joins), "corners": len(corners) if corners is not None else 0, "kept": int(kept),
    }


def run_case(impl, rgb, outlines, corners_spec):
    contours, fits = fit_image(impl, rgb)
    total = None
    for pts, fit in zip(contours, fits):
        centre = pts.mean(axis=0)
        truth = min(outlines, key=lambda o: np.linalg.norm(o.mean(axis=0) - centre) + abs(len(o) / SS - len(pts)) * 0.01)
        if corners_spec is None:
            corners = estimate_corners(truth)
        elif isinstance(corners_spec, np.ndarray) and len(corners_spec):
            corners = corners_spec[np.min(cKDTree(truth).query(corners_spec)[0][:, None], axis=1) < 3]
        else:
            corners = np.zeros((0, 2)) if corners_spec is not False else None
        s = score_fit(fit["combined"], truth, corners)
        if total is None:
            total = s
        else:
            for k in ("lines", "curves", "kinks", "joins", "corners", "kept"):
                total[k] += s[k]
            total["max"] = max(total["max"], s["max"])
            total["mean"] = (total["mean"] + s["mean"]) / 2
    return total


def densify(polyline, step=0.05):
    """Resample a polyline so nearest-point distance approximates distance to the line."""
    out = [polyline[:1]]
    for a, b in zip(polyline[:-1], polyline[1:]):
        k = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.append(a + (b - a) * (np.arange(1, k + 1)[:, None] / k))
    return np.vstack(out)


def real_cases(dirs):
    cases = []
    for d in dirs:
        for png in sorted(Path(d).expanduser().rglob("*.png")):
            if png.name.startswith("0") or "mask" in png.name or "contact" in png.name or "overview" in png.name:
                continue
            im = np.asarray(Image.open(png).convert("RGBA"), dtype=np.float32) / 255.0
            rgb = im[:, :, :3] * im[:, :, 3:] + (1 - im[:, :, 3:])
            cases.append((f"{png.parent.parent.name}/{png.stem}", rgb))
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--impl", action="append", type=Path, help="vectorize.py implementation(s) to compare")
    ap.add_argument("--real", action="append", default=[], help="Directory of real PNG crops (searched recursively)")
    args = ap.parse_args()
    impls = args.impl or [Path(__file__).with_name("vectorize.py")]
    modules = [(p.stem, load_impl(p.resolve())) for p in impls]

    rows = []
    for name, mask, corners in synthetic_cases():
        rgb = np.repeat((1.0 - downsample(mask))[:, :, None], 3, axis=2).astype(np.float32)
        outlines = true_outlines(mask)
        rows.append((name, [run_case(m, rgb, outlines, corners) for _, m in modules]))

    for name, rgb in real_cases(args.real):
        results = []
        for _, m in modules:
            alpha, _, _ = m.estimate_membership(rgb, 0.42, 85.0, "distance")
            records = m.extract_contours(alpha, 5.0, 0)
            contours, _, _ = m.orient_solid_left([r["points"] for r in records], alpha, 1.5)
            outlines = [densify(np.vstack([c, c[:1]])) for c in contours]
            results.append(run_case(m, rgb, outlines, False))
        rows.append((name, results))

    head = f"{'case':28s}" + "".join(f" | {n[:34]:^34s}" for n, _ in modules)
    sub = f"{'':28s}" + " | max  mean   L   C kinks corners" * len(modules)
    print(head); print(sub); print("-" * len(sub))
    totals = [dict(max=0.0, mean=0.0, lines=0, curves=0, kinks=0, corners=0, kept=0, n=0) for _ in modules]
    for name, results in rows:
        line = f"{name[:28]:28s}"
        for t, r in zip(totals, results):
            corners = f"{r['kept']}/{r['corners']}" if r["corners"] else "  - "
            line += f" | {r['max']:4.2f} {r['mean']:5.3f} {r['lines']:3d} {r['curves']:3d} {r['kinks']:5d} {corners:>7s}"
            for k in ("lines", "curves", "kinks", "corners", "kept"):
                t[k] += r[k]
            t["max"] = max(t["max"], r["max"]); t["mean"] += r["mean"]; t["n"] += 1
        print(line)
    print("-" * len(sub))
    line = f"{'TOTAL':28s}"
    for t in totals:
        line += f" | {t['max']:4.2f} {t['mean'] / max(t['n'], 1):5.3f} {t['lines']:3d} {t['curves']:3d} {t['kinks']:5d} {t['kept']:3d}/{t['corners']:<3d}"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
