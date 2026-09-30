#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cairosvg
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.path import Path as MatplotlibPath
from PIL import Image
from scipy.ndimage import binary_dilation, gaussian_filter, map_coordinates
from skimage.measure import find_contours

LINE_COLOR = "#0057D9"
BEZIER_COLOR = "#D9007F"
HALO_COLOR = "white"
DEFAULT_FILL = "#F28C00"


def parse_args():
    p = argparse.ArgumentParser(description="Diagnostic-first raster-to-vector converter.")
    p.add_argument("input", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("vectorizer-out"))
    p.add_argument("--threshold", type=float, default=0.25,
                   help="Legacy shared tolerance fallback used when line/bezier tolerances are not provided.")
    p.add_argument("--line-threshold", type=float, default=None,
                   help="Maximum orthogonal deviation in pixels allowed for line detection/commit.")
    p.add_argument("--bezier-threshold", type=float, default=None,
                   help="Maximum point-to-curve deviation in pixels allowed for Bézier fitting.")
    p.add_argument("--min-contour-length", type=float, default=8.0)
    p.add_argument("--max-contours", type=int, default=0, help="0 keeps all before explicit contour selection.")
    p.add_argument("--keep-contour", action="append", type=int, default=[],
                    help="Keep this numbered contour after extraction; repeatable. IDs are deterministic after length sorting.")
    p.add_argument("--object-contours", action="append", default=[], metavar="NAME=ID,ID",
                   help="Assign selected contour IDs to one named SVG object; repeatable. When used, every selected contour must be assigned exactly once.")
    p.add_argument("--blur-sigma", type=float, default=0.42)
    p.add_argument("--foreground-quantile", type=float, default=85.0)
    p.add_argument("--membership-mode", choices=["distance", "projection"], default="distance",
                   help="Build RGB foreground strength from distance to background (recommended) or legacy foreground-direction projection.")
    p.add_argument("--normal-sample-distance", type=float, default=1.5)
    p.add_argument("--fill", default=DEFAULT_FILL)
    p.add_argument("--diagnostic-dpi", type=int, default=190)
    p.add_argument("--line-min-length", type=float, default=5.0,
                   help=f"Minimum straight-line length in px (lines are only detected from {LINE_DETECT_MIN_PX:g} px).")
    p.add_argument("--line-min-points", type=int, default=5)
    p.add_argument("--line-flatness-px", type=float, default=None,
                   help="Largest curvature bulge a straight span may show (default: 0.3 x line threshold).")
    p.add_argument("--corner-angle-deg", type=float, default=CORNER_ANGLE_DEG,
                   help="Minimum turning over the corner window for a sharp corner; 180 disables corners.")
    p.add_argument("--corner-window-px", type=float, default=CORNER_WINDOW_PX,
                   help="Arc length on each side used to measure corner turning.")
    # Accepted for older YAML/CLI calls; the current fitter does not use them.
    for flag, kind, default in (("--line-angle-deadband-deg", float, 0.0), ("--line-rotation-run", int, 0),
                                ("--line-rotation-total-deg", float, 2.5), ("--joint-tangent-threshold-deg", float, 6.0),
                                ("--joint-max-trim-px", float, 8.0), ("--joint-min-line-length", float, 3.0)):
        p.add_argument(flag, type=kind, default=default, help="Deprecated; ignored.")
    p.add_argument("--joint-refine", choices=["on", "off"], default="on", help="Deprecated; ignored (joins are G1 by construction).")
    return p.parse_args()


def estimate_membership(rgb, blur_sigma, foreground_quantile, mode="distance"):
    """Estimate a continuous 0..1 foreground-strength field from original RGB pixels."""
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    bg = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - bg[None, None, :], axis=2)
    q = np.clip(foreground_quantile, 50.0, 99.9)
    cut = np.percentile(distance, q)
    strong = distance[distance > 0.1 * float(distance.max())]
    peak = float(np.percentile(strong, 90.0)) if strong.size else float(distance.max())
    if cut < 0.25 * peak:
        # Foreground covers less than (100 - q)% of the image, so the percentile landed in
        # background/antialiasing; take the clearly-foreground pixels as the core instead.
        cut = 0.5 * peak
    core = distance >= cut
    fg = np.median(rgb[core], axis=0)

    if mode == "projection":
        v = fg - bg
        denom = float(v @ v)
        if denom < 1e-12:
            raise RuntimeError("Could not distinguish foreground from background.")
        membership = np.sum((rgb - bg) * v[None, None, :], axis=2) / denom
    else:
        # Color-direction agnostic: robust to antialiasing, JPEG variation, and multicolor assets.
        core_distance = distance[core]
        scale = float(np.percentile(core_distance, 90.0)) if core_distance.size else float(distance.max())
        if scale < 1e-12:
            raise RuntimeError("Could not distinguish foreground from background.")
        membership = distance / scale

    membership = gaussian_filter(np.clip(membership, 0.0, 1.0), sigma=max(0.0, blur_sigma))
    return membership, bg, fg


def extract_contours(alpha, min_length, max_contours):
    """Return deterministic contour records sorted by descending geometric length."""
    found = []
    for c in find_contours(alpha, 0.5):
        xy = np.column_stack([c[:, 1], c[:, 0]])
        if len(xy) < 3:
            continue
        length = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum())
        if length >= min_length:
            if np.linalg.norm(xy[0] - xy[-1]) < 1e-6:
                xy = xy[:-1]
            found.append((xy, length))
    # Stable deterministic order: primarily longest first, then centroid position.
    found.sort(key=lambda item: (-item[1], float(item[0][:,1].mean()), float(item[0][:,0].mean())))
    if max_contours > 0:
        found = found[:max_contours]

    records = []
    for cid, (xy, length) in enumerate(found, start=1):
        minxy = xy.min(axis=0); maxxy = xy.max(axis=0)
        records.append({
            "id": cid,
            "points": xy,
            "length": length,
            "point_count": int(len(xy)),
            "centroid": [float(xy[:,0].mean()), float(xy[:,1].mean())],
            "bbox": [float(minxy[0]), float(minxy[1]), float(maxxy[0]), float(maxxy[1])],
        })
    return records


def select_contours(records, keep_ids):
    if not keep_ids:
        return records
    wanted = set(int(v) for v in keep_ids)
    known = {r["id"] for r in records}
    missing = sorted(wanted - known)
    if missing:
        raise ValueError(f"Unknown contour ID(s): {missing}. Available: {sorted(known)}")
    return [r for r in records if r["id"] in wanted]


def sample_field(field, xy):
    return map_coordinates(field, np.vstack([xy[:, 1], xy[:, 0]]), order=1, mode="nearest")


def orient_solid_left(contours, alpha, sample_distance):
    gy, gx = np.gradient(alpha)
    out, normals, stats = [], [], []
    for idx, pts in enumerate(contours, start=1):
        prev = np.roll(pts, 1, axis=0)
        nxt = np.roll(pts, -1, axis=0)
        tang = nxt - prev
        tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-9)
        left = np.column_stack([tang[:, 1], -tang[:, 0]])
        d = max(0.1, sample_distance)
        score = float(np.mean(sample_field(alpha, pts + d * left) - sample_field(alpha, pts - d * left)))
        reversed_flag = score < 0
        if reversed_flag:
            pts = pts[::-1].copy()

        coords = np.vstack([pts[:, 1], pts[:, 0]])
        n = np.column_stack([
            map_coordinates(gx, coords, order=1, mode="nearest"),
            map_coordinates(gy, coords, order=1, mode="nearest"),
        ])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)

        out.append(pts)
        normals.append(n)
        stats.append({
            "contour": idx,
            "orientation_score_before_normalization": score,
            "reversed": reversed_flag,
            "point_count": int(len(pts)),
        })
    return out, normals, stats


CORNER_ANGLE_DEG = 30.0
CORNER_WINDOW_PX = 3.0
CORNER_CONCENTRATION = 0.65
CORNER_STRONG_DEG = 50.0
CORNER_WEAK_MAX_SPREAD = 1.25
TANGENT_WINDOW_PX = 2.0
CORNER_ROUNDING_PX = 0.6
# Below this length a gentle curve's bulge is within pixel noise, so short
# spans cannot be told apart from lines; they are fitted as (flat) cubics.
LINE_DETECT_MIN_PX = 10.0
FLAT_TANGENT_DEG = 2.0


def chord_parameters(points):
    ds = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(ds)])
    return np.linspace(0.0, 1.0, len(points)) if s[-1] < 1e-9 else s / s[-1]


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / max(n, 1e-12)


def cubic_eval(ctrl, u):
    p0, p1, p2, p3 = ctrl
    u = np.asarray(u)
    return (
        ((1-u)**3)[:, None] * p0
        + (3*(1-u)**2*u)[:, None] * p1
        + (3*(1-u)*u**2)[:, None] * p2
        + (u**3)[:, None] * p3
    )


def cubic_derivative(ctrl, t):
    p0, p1, p2, p3 = ctrl
    t = float(t)
    return 3*(1-t)**2*(p1-p0) + 6*(1-t)*t*(p2-p1) + 3*t**2*(p3-p2)


def _angle_between_deg(a, b):
    ua = _unit(a); ub = _unit(b)
    dot = float(np.clip(np.dot(ua, ub), -1.0, 1.0))
    return float(np.degrees(np.arccos(dot)))


def _model_at_index(points, bezier_model, k):
    """Evaluate the Bézier-only model and its tangent at contour index k (diagnostics)."""
    k = int(np.clip(k, 0, len(points)-1))
    for ctrl, i, j in bezier_model:
        if i <= k <= j:
            u = chord_parameters(points[i:j+1])
            t = float(u[k-i])
            return cubic_eval(ctrl, np.array([t]))[0], cubic_derivative(ctrl, t), ctrl, t, i, j
    ctrl, i, j = min(bezier_model, key=lambda s: min(abs(k-s[1]), abs(k-s[2])))
    t = 0.0 if abs(k-i) <= abs(k-j) else 1.0
    return cubic_eval(ctrl, np.array([t]))[0], cubic_derivative(ctrl, t), ctrl, t, i, j


# ---------------------------------------------------------------- arc-length helpers
def _arc(points, closed):
    """Cumulative arc length; for closed contours the last entry is the full perimeter."""
    pts = np.vstack([points, points[:1]]) if closed else points
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])


def _point_at(points, s, dist, closed):
    total = s[-1]
    pts = np.vstack([points, points[:1]]) if closed else points
    dist = np.mod(dist, total) if closed else np.clip(dist, 0.0, total)
    return np.column_stack([np.interp(dist, s, pts[:, 0]), np.interp(dist, s, pts[:, 1])])


def _relative_arc(s, i, n, closed):
    rel = s[:n] - s[i]
    if closed:
        total = s[-1]
        rel = (rel + total / 2) % total - total / 2
    return rel


def _cross(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _dir_or_none(v):
    return None if float(np.linalg.norm(v)) < 1e-9 else _unit(v)


def _pca_direction(pts):
    c = pts.mean(axis=0)
    _, _, vh = np.linalg.svd(pts - c, full_matrices=False)
    return c, vh[0]


def tangent_at(points, i, half_window_px=TANGENT_WINDOW_PX, closed=False, s=None):
    """Travel-direction tangent at points[i] from a PCA line over +-half_window of arc."""
    n = len(points)
    s = _arc(points, closed) if s is None else s
    rel = _relative_arc(s, i, n, closed)
    half = min(half_window_px, s[-1] / 4) if closed else half_window_px
    idx = np.nonzero(np.abs(rel) <= half)[0]
    if len(idx) < 3:
        lo, hi = max(0, i - 1), min(n - 1, i + 1)
        return _unit(points[hi] - points[lo])
    order = idx[np.argsort(rel[idx])]
    _, d = _pca_direction(points[order])
    if np.dot(d, points[order[-1]] - points[order[0]]) < 0:
        d = -d
    return _unit(d)


# ---------------------------------------------------------------- corners
def _turning_deg(points, s, window):
    a = _point_at(points, s, s[:-1] - window, True)
    b = _point_at(points, s, s[:-1] + window, True)
    d1, d2 = points - a, b - points
    return np.degrees(np.arctan2(d1[:, 0]*d2[:, 1] - d1[:, 1]*d2[:, 0], (d1*d2).sum(axis=1)))


def detect_corners(points, window_px=CORNER_WINDOW_PX, min_angle_deg=CORNER_ANGLE_DEG,
                   concentration=CORNER_CONCENTRATION):
    """Indices of sharp corners on a closed contour.

    A corner does most of its turning right at the tip: blur and antialiasing only
    round it by ~CORNER_ROUNDING_PX, so the turning measured over +-1.25 px is already
    most of the turning over +-``window_px``. A smooth curve, however tight, turns in
    proportion to arc length, so the same ratio stays near 1.25 / window (~0.4).
    Candidates need >= ``min_angle_deg`` over the window and a ratio >= ``concentration``.

    Pixel stair-steps on a curve (e.g. a blocky, upscaled source) also look like
    concentrated but weak corners; below CORNER_STRONG_DEG a corner therefore also
    needs straight-ish arms (turning over twice the window <= CORNER_WEAK_MAX_SPREAD x).
    """
    if min_angle_deg >= 180 or len(points) < 8:
        return []
    s = _arc(points, True)
    w = min(window_px, s[-1] / 8)
    inner = min(1.25, w * 0.5)
    a_outer = np.abs(_turning_deg(points, s, w))
    a_inner = np.abs(_turning_deg(points, s, inner))
    a_wide = np.abs(_turning_deg(points, s, min(2 * w, s[-1] / 4)))
    # Weak candidates (min_angle..CORNER_STRONG_DEG) must have straight-ish arms: on a
    # curve, pixel stair-steps also look like weak corners but keep turning further out.
    strong = a_outer >= max(min_angle_deg, CORNER_STRONG_DEG)
    straight_arms = a_wide <= CORNER_WEAK_MAX_SPREAD * a_outer
    candidate = ((a_outer >= min_angle_deg) & (a_inner >= concentration * a_outer)
                 & (strong | straight_arms))
    chosen = []
    for i in np.argsort(-a_outer):
        if not candidate[i]:
            continue
        rel = _relative_arc(s, i, len(points), True)
        if all(abs(rel[j]) > w for j in chosen):
            chosen.append(int(i))
    return sorted(chosen)


def corner_vertex(points, i, inner_px=1.0, outer_px=CORNER_WINDOW_PX + 1.0, s=None):
    """Sharp corner position: intersection of lines fitted to the two arms."""
    s = _arc(points, True) if s is None else s
    rel = _relative_arc(s, i, len(points), True)
    before = points[(rel <= -inner_px) & (rel >= -outer_px)]
    after = points[(rel >= inner_px) & (rel <= outer_px)]
    if len(before) < 2 or len(after) < 2:
        return points[i].copy()
    c1, d1 = _pca_direction(before)
    c2, d2 = _pca_direction(after)
    cross = d1[0]*d2[1] - d1[1]*d2[0]
    if abs(cross) < np.sin(np.radians(8)):
        return points[i].copy()
    t = ((c2 - c1)[0]*d2[1] - (c2 - c1)[1]*d2[0]) / cross
    v = c1 + t * d1
    # Antialiasing + blur round a corner by roughly CORNER_ROUNDING_PX, so the true
    # vertex can lie at most rho * (1 / sin(interior / 2) - 1) outside the contour.
    # Clamping to that stops curved arms from extrapolating far past the tip.
    turn = np.radians(_angle_between_deg(d1 if np.dot(d1, points[i] - c1) > 0 else -d1,
                                         d2 if np.dot(d2, c2 - points[i]) > 0 else -d2))
    interior = np.pi - turn
    if interior < np.radians(20):
        return points[i].copy()  # near-cusp notch: extrapolated arms are unreliable
    limit = min(CORNER_ROUNDING_PX * (1.0 / np.sin(interior / 2) - 1.0) + 0.15, 1.5)
    shift = v - points[i]
    dist = float(np.linalg.norm(shift))
    return points[i] + shift * min(1.0, limit / dist) if dist > 1e-9 else points[i].copy()


# ---------------------------------------------------------------- straight lines
def bestfit_line_stats(points):
    """Orthogonal PCA line fit, deviation statistics and curvature (sagitta) evidence."""
    c, d = _pca_direction(points)
    normal = np.array([-d[1], d[0]])
    along = (points - c) @ d
    errors = (points - c) @ normal
    sagitta = 0.0
    if len(points) >= 5:
        coef = np.polyfit(along, errors, 2)
        half = (along.max() - along.min()) / 2
        sagitta = float(abs(coef[0]) * half * half)
    abs_err = np.abs(errors)
    return {
        "center": c,
        "direction": d,
        "max_deviation": float(abs_err.max()) if len(abs_err) else 0.0,
        "mean_deviation": float(abs_err.mean()) if len(abs_err) else 0.0,
        "rms_deviation": float(np.sqrt(np.mean(errors**2))) if len(errors) else 0.0,
        "sagitta": sagitta,
    }


def bestfit_line_max_error(points):
    return bestfit_line_stats(points)["max_deviation"]


def find_lines(points, threshold, flatness, min_length, min_points):
    """Maximal straight spans of an open polyline, longest first, non-overlapping.

    A span is straight when it stays within ``threshold`` of its best-fit line AND a
    quadratic fit finds no consistent bulge larger than ``flatness``; the second test
    keeps gentle curves (which also stay within a loose threshold) out of lines.
    """
    n = len(points)
    s = _arc(points, False)
    min_length = max(float(min_length), LINE_DETECT_MIN_PX)

    def straight(i, j):
        st = bestfit_line_stats(points[i:j+1])
        return st["max_deviation"] <= threshold and st["sagitta"] <= flatness

    candidates = []
    for i in range(n - 1):
        lo, hi = i + 1, n - 1
        if not straight(i, lo):
            continue
        # Exponential then binary search for the longest straight span from i.
        step = 1
        while lo + step <= hi and straight(i, lo + step):
            lo += step
            step *= 2
        hi = min(hi, lo + step)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if straight(i, mid):
                lo = mid
            else:
                hi = mid - 1
        j = lo
        if j - i + 1 >= min_points and s[j] - s[i] >= min_length:
            candidates.append((s[j] - s[i], i, j))

    taken = np.zeros(n, dtype=bool)
    lines = []
    for length, i, j in sorted(candidates, reverse=True):
        if taken[i+1:j].any() or taken[i] and taken[i+1] or taken[j] and taken[j-1]:
            continue
        taken[i:j+1] = True
        st = bestfit_line_stats(points[i:j+1])
        lines.append({"start": int(i), "end": int(j), "geometric_length": float(length),
                      "direction": st["direction"], "center": st["center"],
                      **{k: st[k] for k in ("max_deviation", "mean_deviation", "rms_deviation", "sagitta")}})
    return sorted(lines, key=lambda line: line["start"])


def _project(point, center, direction):
    return center + float((point - center) @ direction) * direction


# ---------------------------------------------------------------- cubic fitting
def _bernstein(u):
    return (1-u)**3, 3*(1-u)**2*u, 3*(1-u)*u**2, u**3


def fit_cubic(points, u=None, tangent_start=None, tangent_end=None):
    """Least-squares cubic with fixed endpoints.

    ``tangent_start`` / ``tangent_end`` (forward travel direction) constrain the
    control points to those directions; None leaves that control point free.
    """
    p0, p3 = points[0], points[-1]
    u = chord_parameters(points) if u is None else u
    chord = float(np.linalg.norm(p3 - p0))
    t0 = None if tangent_start is None else _unit(tangent_start)
    t1 = None if tangent_end is None else _unit(tangent_end)

    def fallback():
        a = max(chord, 1e-6) / 3.0
        d = _unit(p3 - p0) if chord > 1e-9 else np.array([1.0, 0.0])
        c1 = p0 + a * (t0 if t0 is not None else d)
        c2 = p3 - a * (t1 if t1 is not None else d)
        return (p0.copy(), c1, c2, p3.copy()), u

    free = (t0 is None) * 2 + (t1 is None) * 2 + (t0 is not None) + (t1 is not None)
    if len(points) < 3 or len(points) * 2 < free + 1:
        return fallback()
    b0, b1, b2, b3 = _bernstein(u)
    rhs = points - b0[:, None] * p0 - b3[:, None] * p3
    cols = []
    if t0 is None:
        cols += [np.column_stack([b1, 0*b1]), np.column_stack([0*b1, b1])]
    else:
        rhs = rhs - b1[:, None] * p0
        cols.append(b1[:, None] * t0)
    if t1 is None:
        cols += [np.column_stack([b2, 0*b2]), np.column_stack([0*b2, b2])]
    else:
        rhs = rhs - b2[:, None] * p3
        cols.append(-b2[:, None] * t1)
    M = np.column_stack([c.reshape(-1) for c in cols])
    sol, *_ = np.linalg.lstsq(M, rhs.reshape(-1), rcond=None)
    k = 0
    if t0 is None:
        c1 = sol[0:2].copy(); k = 2
    else:
        a = float(sol[0]); k = 1
        if not np.isfinite(a) or a < 1e-3 * max(chord, 1e-6) or a > 3 * max(chord, 1.0):
            return fallback()
        c1 = p0 + a * t0
    if t1 is None:
        c2 = sol[k:k+2].copy()
    else:
        b = float(sol[k])
        if not np.isfinite(b) or b < 1e-3 * max(chord, 1e-6) or b > 3 * max(chord, 1.0):
            return fallback()
        c2 = p3 - b * t1
    return (p0.copy(), c1, c2, p3.copy()), u


def _reparameterize(ctrl, points, u):
    """One Newton-Raphson step moving each parameter toward the closest curve point."""
    p0, p1, p2, p3 = ctrl
    q = cubic_eval(ctrl, u)
    d1 = 3 * (((1-u)**2)[:, None] * (p1-p0) + (2*(1-u)*u)[:, None] * (p2-p1) + (u**2)[:, None] * (p3-p2))
    d2 = 6 * (((1-u))[:, None] * (p2 - 2*p1 + p0) + u[:, None] * (p3 - 2*p2 + p1))
    diff = q - points
    num = (diff * d1).sum(axis=1)
    den = (d1 * d1).sum(axis=1) + (diff * d2).sum(axis=1)
    step = np.where(np.abs(den) > 1e-12, num / np.where(np.abs(den) > 1e-12, den, 1.0), 0.0)
    out = np.clip(u - step, 0.0, 1.0)
    out[0], out[-1] = 0.0, 1.0
    return out


def _fit_error(ctrl, points, u):
    d = np.linalg.norm(cubic_eval(ctrl, u) - points, axis=1)
    k = int(np.argmax(d))
    return float(d[k]), k


def _best_cubic(points, t0, t1, iterations=10):
    """Cubic fit refined by alternating Newton reparameterization and refitting."""
    ctrl, u = fit_cubic(points, None, t0, t1)
    err, k = _fit_error(ctrl, points, u)
    for _ in range(iterations):
        u2 = _reparameterize(ctrl, points, u)
        ctrl2, _ = fit_cubic(points, u2, t0, t1)
        err2, k2 = _fit_error(ctrl2, points, u2)
        if err2 >= err - 1e-4:
            break
        ctrl, u, err, k = ctrl2, u2, err2, k2
    return ctrl, err, k


def fit_curve_span(points, tolerance, t0=None, t1=None, base=0):
    """Schneider-style recursive fit of an open span; joins share one tangent (G1).

    Returns [(ctrl, i, j)] with indices relative to ``points`` plus ``base``.
    """
    n = len(points)
    if n <= 2 or float(np.linalg.norm(points[-1] - points[0])) < 1e-9 and n <= 3:
        ctrl, _ = fit_cubic(points[[0, -1]] if n >= 2 else points, None, t0, t1)
        return [(ctrl, base, base + n - 1)]
    if float(np.linalg.norm(points[-1] - points[0])) < 1e-9:
        # Closed span (start == end): split at the point farthest from the seam first.
        k = int(np.argmax(np.linalg.norm(points - points[0], axis=1)))
        k = int(np.clip(k, 1, n - 2))
    else:
        ctrl, err, k = _best_cubic(points, t0, t1)
        if err <= tolerance or n < 4:
            return [(ctrl, base, base + n - 1)]
        k = int(np.clip(k, 1, n - 2))
    tm = tangent_at(points, k)
    return (fit_curve_span(points[:k+1], tolerance, t0, tm, base)
            + fit_curve_span(points[k:], tolerance, tm, t1, base + k))


def merge_curve_segments(points, segments, tolerance, t0=None, t1=None, base=0):
    """Greedily merge neighbouring cubics when one cubic still fits their union."""
    segs = list(segments)
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for a in range(len(segs) - 1):
            (_, i, _), (_, _, j) = segs[a], segs[a + 1]
            start_tan = t0 if a == 0 else _dir_or_none(segs[a][0][1] - segs[a][0][0])
            end_tan = t1 if a + 1 == len(segs) - 1 else _dir_or_none(segs[a + 1][0][3] - segs[a + 1][0][2])
            span = points[i - base:j - base + 1]
            if float(np.linalg.norm(span[-1] - span[0])) < 1e-9:
                continue
            ctrl, err, _ = _best_cubic(span, start_tan, end_tan)
            if err <= tolerance:
                segs[a:a + 2] = [(ctrl, i, j)]
                changed = True
                break
    return segs


def fit_open_run(points, line_threshold, bezier_threshold, flatness, line_min_length, line_min_points,
                 t_start=None, t_end=None, find_straight=True):
    """Lines + G1 cubics for an open run of contour points (endpoints fixed)."""
    n = len(points)
    lines = find_lines(points, line_threshold, flatness, line_min_length, line_min_points) if find_straight else []
    segments, emitted_lines = [], []
    for line in lines:
        i, j = line["start"], line["end"]
        a = points[i] if i == 0 else _project(points[i], line["center"], line["direction"])
        b = points[j] if j == n - 1 else _project(points[j], line["center"], line["direction"])
        line.update({"a": a, "b": b})
        emitted_lines.append(line)

    cursor, cursor_point, prev_tan = 0, points[0], t_start
    for line in emitted_lines + [None]:
        stop = n - 1 if line is None else line["start"]
        stop_point = points[-1] if line is None else line["a"]
        next_tan = t_end if line is None else _unit(line["b"] - line["a"])
        if stop > cursor or float(np.linalg.norm(stop_point - cursor_point)) > 1e-7:
            gap = points[cursor:stop + 1].copy()
            if len(gap) < 2:
                gap = np.vstack([cursor_point, stop_point])
            gap[0], gap[-1] = cursor_point, stop_point
            curves = fit_curve_span(gap, bezier_threshold, prev_tan, next_tan, cursor)
            curves = merge_curve_segments(gap, curves, bezier_threshold, prev_tan, next_tan, cursor)
            for ctrl, gi, gj in curves:
                p0, p1, p2, p3 = ctrl
                chord = float(np.linalg.norm(p3 - p0))
                bulge = max(abs(_cross(_unit(p3 - p0), c - p0)) for c in (p1, p2)) if chord > 1e-9 else 0.0
                # A flat cubic becomes a line only if its end tangents already follow the
                # chord; otherwise the swap would break the smooth join with its neighbours.
                aligned = chord > 1e-9 and all(
                    np.linalg.norm(v) < 1e-9 or _angle_between_deg(v, p3 - p0) <= FLAT_TANGENT_DEG
                    for v in (p1 - p0, p3 - p2))
                if chord >= line_min_length and bulge <= flatness and aligned:
                    segments.append(("L", (p0, p3), gi, gj))
                else:
                    segments.append(("B", ctrl, gi, gj))
        if line is not None:
            segments.append(("L", (line["a"], line["b"]), line["start"], line["end"]))
            cursor, cursor_point, prev_tan = line["end"], line["b"], _unit(line["b"] - line["a"])
    return segments, emitted_lines


def hybrid_fit(points, line_threshold, bezier_threshold, angle_deadband_deg=0.2, rotation_run=4,
               rotation_total_deg=2.5, line_min_points=5, line_min_length=5.0,
               joint_refine=True, joint_tangent_threshold_deg=6.0,
               joint_max_trim_px=8.0, joint_min_line_length=3.0,
               corner_angle_deg=CORNER_ANGLE_DEG, corner_window_px=CORNER_WINDOW_PX, line_flatness_px=None):
    """Fit a closed contour with sharp corners, straight lines and G1-continuous cubics.

    1. Corners: concentrated turning (see detect_corners), snapped to the
       intersection of their arms so antialiasing does not round them.
    2. The contour is split into runs between corners (or kept as one loop).
    3. In each run, straight spans become lines; the rest is fitted with cubics
       (Newton reparameterization, split at the worst point, merged back where
       possible). Neighbouring cubics share a tangent and cubics next to a line
       take the line's direction, so joins are smooth unless they are corners.

    The rotation/joint arguments are accepted for compatibility and unused.
    """
    flatness = float(line_flatness_px) if line_flatness_px is not None else max(0.03, 0.3 * float(line_threshold))
    n = len(points)
    s = _arc(points, True)
    corners = detect_corners(points, corner_window_px, corner_angle_deg)
    combined, lines = [], []

    if corners:
        shift = corners[0]
        rot = np.roll(points, -shift, axis=0)
        cidx = [(c - shift) % n for c in corners]
        s_rot = _arc(rot, True)
        vertices = [corner_vertex(rot, c, s=s_rot) for c in cidx]
        for k, i in enumerate(cidx):
            j = cidx[k + 1] if k + 1 < len(cidx) else n
            run = np.vstack([rot[i:j], rot[j % n][None, :]]) if j < n else np.vstack([rot[i:], rot[:1]])
            run = run.copy()
            v0, v1 = vertices[k], vertices[(k + 1) % len(cidx)]
            # Drop the rounded points hugging each corner; the vertex replaces them.
            rs = _arc(run, False)
            keep = [0] + [m for m in range(1, len(run) - 1) if 0.9 <= rs[m] <= rs[-1] - 0.9] + [len(run) - 1]
            run = run[keep]
            run[0], run[-1] = v0, v1
            segs, run_lines = fit_open_run(run, line_threshold, bezier_threshold, flatness,
                                           line_min_length, line_min_points)
            index_map = np.array(keep) + i
            for typ, data, gi, gj in segs:
                combined.append((typ, data, int(index_map[min(gi, len(keep) - 1)] % n), int(index_map[min(gj, len(keep) - 1)] % n)))
            for line in run_lines:
                line = dict(line, start=int(index_map[line["start"]] % n), end=int(index_map[line["end"]] % n))
                lines.append(line)
    else:
        loop_lines = find_lines(points, line_threshold, flatness, line_min_length, line_min_points)
        if loop_lines:
            # Start the loop at the longest line so it is not cut by the seam; the last
            # curve then ends on that line's direction, keeping the seam smooth.
            shift = max(loop_lines, key=lambda l: l["geometric_length"])["start"]
            rot = np.roll(points, -shift, axis=0)
            closed = np.vstack([rot, rot[:1]])
            first = find_lines(closed, line_threshold, flatness, line_min_length, line_min_points)
            if first and first[0]["start"] == 0:
                t_start = None
                t_end = _unit(_project(closed[first[0]["end"]], first[0]["center"], first[0]["direction"]) - closed[0])
            else:
                t_start = t_end = tangent_at(rot, 0, closed=True)
            segs, run_lines = fit_open_run(closed, line_threshold, bezier_threshold, flatness,
                                           line_min_length, line_min_points, t_start, t_end)
            combined = [(typ, data, gi % n, gj % n) for typ, data, gi, gj in segs]
            lines = [dict(l, start=l["start"] % n, end=l["end"] % n) for l in run_lines]
        else:
            rot = points
            closed = np.vstack([rot, rot[:1]])
            seam = tangent_at(rot, 0, closed=True)
            curves = fit_curve_span(closed, bezier_threshold, seam, seam)
            curves = merge_curve_segments(closed, curves, bezier_threshold, seam, seam)
            combined = [("B", ctrl, gi % n, gj % n) for ctrl, gi, gj in curves]

    # Independent Bézier-only model (diagnostic 03 / 04).
    closed = np.vstack([rot, rot[:1]])
    seam = tangent_at(rot, 0, closed=True)
    bezier_only = [(ctrl, gi, min(gj, n - 1)) for ctrl, gi, gj in fit_curve_span(closed, bezier_threshold, seam, seam)]
    for line in lines:
        line["a"], line["b"] = np.asarray(line["a"]), np.asarray(line["b"])

    log_lines = [{k: v for k, v in line.items() if k not in ("direction", "center")} for line in lines]
    return {"line_only": log_lines, "refined_lines": log_lines, "bezier_only": bezier_only,
            "line_threshold": float(line_threshold), "bezier_threshold": float(bezier_threshold),
            "kept_lines": log_lines, "combined": combined, "rotated_points": rot,
            "corners": [int(c) for c in corners],
            "corner_points": [points[c].copy() for c in corners],
            "corner_vertices": [corner_vertex(points, c, s=s) for c in corners],
            "line_detection": {"flatness_px": flatness, "corner_angle_deg": corner_angle_deg,
                               "corner_window_px": corner_window_px},
            "joint_refinement": []}


def prepare_axis(ax, rgb, title):
    ax.imshow(rgb, alpha=0.58)
    ax.set_title(title, fontsize=15, weight="bold")
    h, w = rgb.shape[:2]
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_aspect("equal")
    ax.axis("off")


def draw_visible(ax, x, y, color, linewidth):
    ax.plot(x, y, color=HALO_COLOR, linewidth=linewidth+4,
            solid_capstyle="round", solid_joinstyle="round")
    ax.plot(x, y, color=color, linewidth=linewidth,
            solid_capstyle="round", solid_joinstyle="round")


def save_all_contours(path, rgb, records, selected_ids, dpi):
    """Diagnostic before contour selection: number every extracted point collection."""
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(rgb)
    ax.set_title("All extracted subpixel contours", fontsize=15, weight="bold")
    selected = set(selected_ids or [])
    for record in records:
        pts = record["points"]
        cid = record["id"]
        is_selected = (not selected) or cid in selected
        ax.plot(pts[:,0], pts[:,1], linewidth=2.4 if is_selected else 1.2,
                alpha=1.0 if is_selected else 0.38)
        cx, cy = record["centroid"]
        ax.text(cx, cy, str(cid), fontsize=11, weight="bold",
                ha="center", va="center",
                bbox=dict(boxstyle="circle,pad=0.25", facecolor="white", alpha=0.9))
    h, w = rgb.shape[:2]
    ax.set_xlim(0, w); ax.set_ylim(h, 0); ax.set_aspect("equal"); ax.axis("off")
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def save_points(path, rgb, contours, normals, dpi):
    fig, ax = plt.subplots(figsize=(7, 7))
    prepare_axis(ax, rgb, "Subpixel points + solid-facing normals")
    for pts, n in zip(contours, normals):
        ax.scatter(pts[:, 0], pts[:, 1], s=5, alpha=.70)
        step = max(1, len(pts)//24)
        q = pts[::step]
        qn = n[::step]
        ax.quiver(q[:, 0], q[:, 1], qn[:, 0], qn[:, 1],
                  angles="xy", scale_units="xy", scale=.25,
                  width=.005, headwidth=4, headlength=5)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def save_line_only(path, rgb, fits, dpi):
    fig, ax = plt.subplots(figsize=(7, 7))
    prepare_axis(ax, rgb, "LINE-ONLY fit")
    for fit in fits:
        for line in fit["line_only"]:
            a, b = line["a"], line["b"]
            draw_visible(ax, [a[0], b[0]], [a[1], b[1]], LINE_COLOR, 3.8)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def save_bezier_only(path, rgb, fits, dpi):
    fig, ax = plt.subplots(figsize=(7, 7))
    prepare_axis(ax, rgb, "BÉZIER-ONLY fit")
    for fit in fits:
        for ctrl, _, _ in fit["bezier_only"]:
            c = cubic_eval(ctrl, np.linspace(0, 1, 120))
            draw_visible(ax, c[:, 0], c[:, 1], BEZIER_COLOR, 3.8)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def save_corners(path, rgb, fits, dpi):
    fig, ax = plt.subplots(figsize=(7, 7))
    prepare_axis(ax, rgb, "Corners (x) and sharpened vertices (o)")
    for fit in fits:
        for typ, data, _, _ in fit["combined"]:
            c = np.array([data[0], data[1]]) if typ == "L" else cubic_eval(data, np.linspace(0, 1, 60))
            ax.plot(c[:, 0], c[:, 1], color=LINE_COLOR if typ == "L" else BEZIER_COLOR, linewidth=1.4, alpha=0.6)
        for raw, vertex in zip(fit.get("corner_points", []), fit.get("corner_vertices", [])):
            ax.plot([raw[0], vertex[0]], [raw[1], vertex[1]], color="black", linewidth=1.0)
            ax.scatter([raw[0]], [raw[1]], marker="x", s=46, color="black", zorder=8)
            ax.scatter([vertex[0]], [vertex[1]], s=40, facecolor="white", edgecolor="black", zorder=9)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def save_combined(path, rgb, fits, dpi):
    fig, ax = plt.subplots(figsize=(7, 7))
    prepare_axis(ax, rgb, "COMBINED line + Bézier fit")
    for fit in fits:
        for typ, data, _, _ in fit["combined"]:
            if typ == "L":
                a, b = data
                draw_visible(ax, [a[0], b[0]], [a[1], b[1]], LINE_COLOR, 4.2)
            else:
                c = cubic_eval(data, np.linspace(0, 1, 120))
                draw_visible(ax, c[:, 0], c[:, 1], BEZIER_COLOR, 4.0)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def svg_subpath(combined):
    if not combined:
        return ""
    _, data, _, _ = combined[0]
    start = data[0]
    commands = [f"M {start[0]:.4f} {start[1]:.4f}"]
    for typ, data, _, _ in combined:
        if typ == "L":
            _, b = data
            commands.append(f"L {b[0]:.4f} {b[1]:.4f}")
        else:
            _, p1, p2, p3 = data
            commands.append(
                f"C {p1[0]:.4f} {p1[1]:.4f} {p2[0]:.4f} {p2[1]:.4f} {p3[0]:.4f} {p3[1]:.4f}"
            )
    commands.append("Z")
    return " ".join(commands)


def save_final(svg_path, png_path, width, height, fits, fill):
    compound = " ".join(svg_subpath(f["combined"]) for f in fits)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        f'  <path d="{compound}" fill="{fill}" fill-rule="nonzero"/>\n'
        f'</svg>\n'
    )
    svg_path.write_text(svg, encoding="utf-8")
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(png_path),
                     output_width=width*4, output_height=height*4,
                     background_color="white")


def parse_object_groups(values):
    groups = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --object-contours value {value!r}; expected NAME=ID,ID")
        name, raw_ids = value.split("=", 1)
        name = re.sub(r"[^A-Za-z0-9_.:-]+", "-", name.strip()).strip("-")
        ids = [int(part.strip()) for part in raw_ids.split(",") if part.strip()]
        if not name or not ids:
            raise ValueError(f"Invalid --object-contours value {value!r}; expected NAME=ID,ID")
        groups.append((name, ids))
    return groups


def save_objects(svg_path, width, height, records, contours, fits, fill, object_groups):
    record_indexes = {record["id"]: index for index, record in enumerate(records)}
    if object_groups:
        assigned = [contour_id for _, ids in object_groups for contour_id in ids]
        unknown = sorted(set(assigned) - set(record_indexes))
        duplicate = sorted({contour_id for contour_id in assigned if assigned.count(contour_id) > 1})
        missing = sorted(set(record_indexes) - set(assigned))
        if unknown or duplicate or missing:
            raise ValueError(
                f"Invalid object contour groups; unknown={unknown}, duplicate={duplicate}, unassigned={missing}"
            )
        paths = []
        for name, contour_ids in object_groups:
            compound = " ".join(svg_subpath(fits[record_indexes[contour_id]]["combined"]) for contour_id in contour_ids)
            paths.append(
                f'  <path id="{name}" data-contours="{",".join(map(str, contour_ids))}" d="{compound}" fill="{fill}" fill-rule="nonzero"/>'
            )
    else:
        paths = _automatic_object_paths(records, contours, fits, fill)

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        + "\n".join(paths)
        + "\n</svg>\n"
    )
    svg_path.write_text(svg, encoding="utf-8")


def _automatic_object_paths(records, contours, fits, fill):
    areas = [
        abs(float(np.dot(points[:, 0], np.roll(points[:, 1], 1)) - np.dot(points[:, 1], np.roll(points[:, 0], 1)))) / 2.0
        for points in contours
    ]
    parents: list[int | None] = []
    for index, points in enumerate(contours):
        containers = [
            candidate
            for candidate, polygon in enumerate(contours)
            if candidate != index
            and areas[candidate] > areas[index]
            and MatplotlibPath(polygon, closed=True).contains_point(points[0])
        ]
        parents.append(min(containers, key=lambda candidate: areas[candidate]) if containers else None)

    depths = []
    for index in range(len(contours)):
        depth = 0
        parent = parents[index]
        while parent is not None:
            depth += 1
            parent = parents[parent]
        depths.append(depth)

    paths = []
    for index, fit in enumerate(fits):
        if depths[index] % 2:
            continue
        children = [child for child, parent in enumerate(parents) if parent == index and depths[child] == depths[index] + 1]
        compound = " ".join(svg_subpath(fits[item]["combined"]) for item in [index, *children])
        contour_id = records[index]["id"]
        paths.append(
            f'  <path id="object-contour-{contour_id}" d="{compound}" fill="{fill}" fill-rule="nonzero"/>'
        )

    return paths


def save_contact_sheet(paths, output):
    images = [Image.open(p).convert("RGB") for p in paths]
    target_h = 520
    thumbs = []
    for im in images:
        r = target_h / im.height
        thumbs.append(im.resize((int(im.width*r), target_h)))
    cols = 3
    rows = (len(thumbs)+cols-1)//cols
    gap = 18
    cell_w = max(im.width for im in thumbs)
    sheet = Image.new("RGB", (cols*cell_w+(cols-1)*gap, rows*target_h+(rows-1)*gap), "white")
    for k, im in enumerate(thumbs):
        x = (k % cols) * (cell_w+gap)
        y = (k // cols) * (target_h+gap)
        sheet.paste(im, (x, y))
    sheet.save(output)


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    image_rgba = Image.open(args.input).convert("RGBA")
    rgba = np.asarray(image_rgba, dtype=np.float32) / 255.0
    rgb_raw = rgba[:, :, :3]
    input_alpha = rgba[:, :, 3]
    h, w = rgb_raw.shape[:2]

    # Canonical workflow: vectorizer traces visible RGB edges only.
    # If transparency is present, composite onto white and ignore alpha as geometry.
    if float(input_alpha.min()) < 0.999:
        rgb = rgb_raw * input_alpha[:, :, None] + (1.0 - input_alpha[:, :, None])
        membership_source = "estimated_from_visible_rgb_composited"
    else:
        rgb = rgb_raw
        membership_source = "estimated_from_color"

    line_threshold = float(args.line_threshold if args.line_threshold is not None else args.threshold)
    bezier_threshold = float(args.bezier_threshold if args.bezier_threshold is not None else args.threshold)

    alpha, bg, fg = estimate_membership(rgb, args.blur_sigma, args.foreground_quantile, args.membership_mode)
    contour_records = extract_contours(alpha, args.min_contour_length, args.max_contours)
    if not contour_records:
        raise RuntimeError("No meaningful contours detected.")
    selected_records = select_contours(contour_records, args.keep_contour)
    object_groups = parse_object_groups(args.object_contours)
    contours = [r["points"] for r in selected_records]

    contours, normals, orientation_stats = orient_solid_left(
        contours, alpha, args.normal_sample_distance
    )
    fits = [hybrid_fit(points, line_threshold, bezier_threshold,
                       line_min_points=args.line_min_points, line_min_length=args.line_min_length,
                       corner_angle_deg=args.corner_angle_deg, corner_window_px=args.corner_window_px,
                       line_flatness_px=args.line_flatness_px) for points in contours]

    p0 = args.output_dir / "00_all_contours.png"
    p1 = args.output_dir / "01_points_normals.png"
    p2 = args.output_dir / "02_line_only.png"
    p3 = args.output_dir / "03_bezier_only.png"
    p4j = args.output_dir / "04_corners.png"
    p4 = args.output_dir / "05_combined.png"
    p5 = args.output_dir / "06_final_filled.png"
    svg = args.output_dir / "final.svg"
    objects_svg = args.output_dir / "final_objects.svg"
    contact = args.output_dir / "pipeline_contact_sheet.png"
    metadata = args.output_dir / "metadata.json"
    line_log = args.output_dir / "line_fit_log.json"
    joint_log = args.output_dir / "corner_log.json"

    save_all_contours(p0, rgb, contour_records, args.keep_contour, args.diagnostic_dpi)
    save_points(p1, rgb, contours, normals, args.diagnostic_dpi)
    save_line_only(p2, rgb, fits, args.diagnostic_dpi)
    save_bezier_only(p3, rgb, fits, args.diagnostic_dpi)
    save_corners(p4j, rgb, fits, args.diagnostic_dpi)
    save_combined(p4, rgb, fits, args.diagnostic_dpi)
    save_final(svg, p5, w, h, fits, args.fill)
    save_objects(objects_svg, w, h, selected_records, contours, fits, args.fill, object_groups)
    save_contact_sheet([p0, p1, p2, p3, p4j, p4, p5], contact)

    line_log_data = {
        "legacy_threshold_px": args.threshold,
        "line_threshold_px": line_threshold,
        "bezier_threshold_px": bezier_threshold,
        "min_line_length_px": args.line_min_length,
        "min_line_points": args.line_min_points,
        "contours": []
    }
    all_max = 0.0
    for ci, fit in enumerate(fits, start=1):
        entries=[]
        for li,line in enumerate(fit["line_only"], start=1):
            entry={k:(float(v) if isinstance(v,(np.floating,float)) else int(v) if isinstance(v,(np.integer,int)) else v)
                   for k,v in line.items() if k not in ("a","b")}
            entry["line"] = li
            entry["start_point"] = [float(x) for x in line["a"]]
            entry["end_point"] = [float(x) for x in line["b"]]
            entries.append(entry); all_max=max(all_max,float(line["max_deviation"]))
        line_log_data["contours"].append({"contour":ci,"lines":entries})
    line_log_data["max_committed_line_deviation_px"] = all_max
    line_log.write_text(json.dumps(line_log_data, indent=2), encoding="utf-8")

    corner_log_data = {
        "corner_angle_deg": args.corner_angle_deg,
        "corner_window_px": args.corner_window_px,
        "contours": [
            {"contour": ci + 1,
             "corners": [{"contour_point": [float(v) for v in raw], "vertex": [float(v) for v in vertex]}
                         for raw, vertex in zip(fit["corner_points"], fit["corner_vertices"])]}
            for ci, fit in enumerate(fits)
        ],
    }
    joint_log.write_text(json.dumps(corner_log_data, indent=2), encoding="utf-8")

    meta = {
        "input": str(args.input),
        "image_size": [w, h],
        "estimated_background_rgb_0_1": bg.tolist(),
        "estimated_foreground_rgb_0_1": fg.tolist(),
        "membership_source": membership_source,
        "contours_detected": [
            {k: v for k, v in r.items() if k != "points"} for r in contour_records
        ],
        "keep_contours": [int(v) for v in args.keep_contour],
        "object_contours": {name: ids for name, ids in object_groups},
        "threshold": args.threshold,
        "line_threshold": line_threshold,
        "bezier_threshold": bezier_threshold,
        "line_min_length": args.line_min_length,
        "line_min_points": args.line_min_points,
        "line_flatness_px": args.line_flatness_px,
        "corner_angle_deg": args.corner_angle_deg,
        "corner_window_px": args.corner_window_px,
        "max_committed_line_deviation_px": all_max,
        "min_contour_length": args.min_contour_length,
        "max_contours": args.max_contours,
        "blur_sigma": args.blur_sigma,
        "membership_mode": args.membership_mode,
        "orientation": orientation_stats,
        "segments": [
            {
                "contour": i+1,
                "line_only": len(f["line_only"]),
                "bezier_only": len(f["bezier_only"]),
                "combined_lines": sum(1 for s in f["combined"] if s[0] == "L"),
                "combined_beziers": sum(1 for s in f["combined"] if s[0] == "B"),
            }
            for i, f in enumerate(fits)
        ],
    }
    metadata.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Detected {len(contour_records)} contours; selected {len(contours)}")
    print(f"Output directory: {args.output_dir}")
    for p in [p0, p1, p2, p3, p4j, p4, p5, svg, objects_svg, contact, metadata, line_log, joint_log]:
        print(" ", p.name)


if __name__ == "__main__":
    main()
