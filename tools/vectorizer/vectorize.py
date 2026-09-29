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
    p.add_argument("--line-angle-deadband-deg", type=float, default=0.0,
                   help="Deprecated compatibility option; ignored by cumulative-rotation detection.")
    p.add_argument("--line-rotation-run", type=int, default=0,
                   help="Deprecated compatibility option; ignored by cumulative-rotation detection.")
    p.add_argument("--line-rotation-total-deg", type=float, default=2.5,
                   help="Cumulative same-sign chord rotation required to declare curvature. Smaller per-step turns naturally require more points.")
    p.add_argument("--line-min-length", type=float, default=5.0)
    p.add_argument("--line-min-points", type=int, default=5)
    p.add_argument("--joint-refine", choices=["on", "off"], default="on",
                   help="Refine line/Bézier joints by shortening lines to tangent-aligned points on the independent Bézier-only model.")
    p.add_argument("--joint-tangent-threshold-deg", type=float, default=6.0,
                   help="Maximum angle between line direction and Bézier tangent at a refined joint.")
    p.add_argument("--joint-max-trim-px", type=float, default=8.0,
                   help="Maximum amount a line endpoint may be shortened during tangent refinement.")
    p.add_argument("--joint-min-line-length", type=float, default=3.0,
                   help="Do not refine a line endpoint if the remaining line would be shorter than this.")
    return p.parse_args()


def estimate_membership(rgb, blur_sigma, foreground_quantile, mode="distance"):
    """Estimate a continuous 0..1 foreground-strength field from original RGB pixels."""
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    bg = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - bg[None, None, :], axis=2)
    q = np.clip(foreground_quantile, 50.0, 99.9)
    core = distance >= np.percentile(distance, q)
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


def bestfit_line_stats(points):
    """Orthogonal PCA line fit and deviation statistics."""
    c = points.mean(axis=0)
    _, _, vh = np.linalg.svd(points - c, full_matrices=False)
    d = vh[0]
    normal = np.array([-d[1], d[0]])
    errors = np.abs((points - c) @ normal)
    return {
        "center": c,
        "direction": d,
        "max_deviation": float(errors.max()) if len(errors) else 0.0,
        "mean_deviation": float(errors.mean()) if len(errors) else 0.0,
        "rms_deviation": float(np.sqrt(np.mean(errors**2))) if len(errors) else 0.0,
    }


def bestfit_line_max_error(points):
    return bestfit_line_stats(points)["max_deviation"]


def _angle_delta(a, b):
    """Shortest signed angular delta b-a in radians."""
    return float(np.arctan2(np.sin(b-a), np.cos(b-a)))


def _rotation_stop_offset(points, deadband_deg, run_required, total_deg):
    """Detect gentle or strong curvature by cumulative one-direction rotation.

    There is deliberately NO per-step angular deadband and NO fixed run-length
    requirement. Every non-zero angular change contributes evidence. While the
    sign stays the same, absolute angular changes accumulate. A sign reversal
    resets the evidence. Once cumulative same-sign rotation reaches
    ``total_deg``, the contour is considered curved.

    This gives the desired adaptive behavior: a strong curve is detected after
    only a few points, while a very gentle curve needs many points before enough
    evidence accumulates.

    ``deadband_deg`` and ``run_required`` remain accepted for compatibility with
    older YAML files but are ignored.

    Returns ``(stop_offset, evidence_deg, run_start_delta_index)`` or ``None``.
    """
    if len(points) < 4:
        return None

    total_req = np.deg2rad(max(float(total_deg), 1e-9))
    angles = []
    p0 = points[0]
    for k in range(1, len(points)):
        v = points[k] - p0
        if np.linalg.norm(v) < 1e-12:
            angles.append(angles[-1] if angles else 0.0)
        else:
            angles.append(float(np.arctan2(v[1], v[0])))

    deltas = [_angle_delta(angles[i], angles[i + 1]) for i in range(len(angles) - 1)]
    run_sign = 0
    run_total = 0.0
    run_start = None

    for di, d in enumerate(deltas):
        if d > 0:
            s = 1
        elif d < 0:
            s = -1
        else:
            s = 0

        if s == 0:
            run_sign = 0
            run_total = 0.0
            run_start = None
            continue

        if s == run_sign:
            run_total += abs(d)
        else:
            run_sign = s
            run_total = abs(d)
            run_start = di

        if run_total >= total_req:
            stop_offset = max(1, int(run_start) + 1)
            return stop_offset, float(np.rad2deg(run_total)), int(run_start)

    return None


def grow_line_forward(points, start, threshold, angle_deadband_deg, rotation_run,
                      rotation_total_deg, min_points, min_length, stop_limit=None):
    """Grow one line candidate using BOTH fit-error and sustained-rotation stop rules."""
    n=len(points); limit=n-1 if stop_limit is None else min(stop_limit,n-1)
    if start>=limit: return None
    provisional_end=None; stop_reason="end"; rotation_start=None; rotation_evidence_deg=0.0
    for j in range(start+1,limit+1):
        span=points[start:j+1]
        if len(span)>=2:
            stats=bestfit_line_stats(span)
            if stats["max_deviation"]>threshold:
                provisional_end=j-1; stop_reason="fit_threshold"; break
        if len(span)>=max(min_points,4):
            rotation_hit=_rotation_stop_offset(span,angle_deadband_deg,rotation_run,rotation_total_deg)
            if rotation_hit is not None:
                off,evidence_deg,run_start_di=rotation_hit
                provisional_end=start+off
                rotation_start=start+off+1
                rotation_evidence_deg=evidence_deg
                stop_reason="rotation"
                break
    if provisional_end is None: provisional_end=limit
    if provisional_end<=start: return None
    span=points[start:provisional_end+1]
    geom=float(np.linalg.norm(np.diff(span,axis=0),axis=1).sum()) if len(span)>1 else 0.0
    if len(span)<int(min_points) or geom<float(min_length):
        return None
    stats=bestfit_line_stats(span)
    return {"start":start,"end":provisional_end,"stop_reason":stop_reason,
            "rotation_start":rotation_start,"rotation_evidence_deg":float(rotation_evidence_deg),
            "geometric_length":geom, **{k:v for k,v in stats.items() if k not in ('center','direction')}}


def detect_lines_rotation_fit(points, threshold, angle_deadband_deg=0.2, rotation_run=4,
                              rotation_total_deg=2.5, min_points=5, min_length=5.0):
    """Detect straight spans first; curves are left as gaps for Bézier fitting.

    First find any valid forward line seed. Extend only that first line backward to
    recover its true start. Rotate the closed contour to that fixed start. Thereafter
    line starts are never moved; scan forward, committing valid spans and skipping
    curved starts point-by-point.
    """
    n=len(points)
    if n<min_points: return [], points, {"first_seed":None}
    seed=None
    for s in range(n-min_points+1):
        cand=grow_line_forward(points,s,threshold,angle_deadband_deg,rotation_run,
                               rotation_total_deg,min_points,min_length)
        if cand is not None:
            seed=cand; break
    if seed is None:
        return [], points, {"first_seed":None}

    # Backward extension for the first line only. Use reversed prefix ending at seed start.
    first_start=seed["start"]
    if first_start>0:
        rev=points[:first_start+1][::-1]
        back=grow_line_forward(rev,0,threshold,angle_deadband_deg,rotation_run,
                               rotation_total_deg,min_points,min_length)
        if back is not None:
            first_start=first_start-back["end"]
    # Rotate so this immutable first start becomes index 0.
    rot=np.concatenate([points[first_start:],points[:first_start]],axis=0)
    lines=[]; cursor=0
    while cursor < n-1:
        cand=grow_line_forward(rot,cursor,threshold,angle_deadband_deg,rotation_run,
                               rotation_total_deg,min_points,min_length)
        if cand is None:
            cursor+=1
            continue
        # Commit raw endpoint geometry; PCA fit is only for detection/logging.
        cand["a"]=rot[cand["start"]].copy(); cand["b"]=rot[cand["end"]].copy()
        lines.append(cand)
        cursor=cand["end"]
    return lines, rot, {"first_seed_original_start":int(seed["start"]),"rotated_start_original_index":int(first_start)}


def line_only_fit(points, threshold, min_points=5, angle_deadband_deg=0.2,
                  rotation_run=0, rotation_total_deg=2.5, min_length=5.0):
    lines, rotated, info=detect_lines_rotation_fit(points,threshold,angle_deadband_deg,
        rotation_run,rotation_total_deg,min_points,min_length)
    return lines, rotated, info

def chord_parameters(points):
    ds = np.linalg.norm(np.diff(points, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(ds)])
    return np.linspace(0.0, 1.0, len(points)) if s[-1] < 1e-9 else s / s[-1]


def _unit(v):
    v=np.asarray(v,dtype=float)
    n=float(np.linalg.norm(v))
    return v/max(n,1e-12)


def fit_cubic(points, tangent_start=None, tangent_end=None):
    """Fit a cubic with optional *forward* endpoint tangent constraints.

    tangent_start/tangent_end point in contour travel direction. If omitted,
    local data tangents are used. The endpoint positions remain fixed.
    """
    p0, p3 = points[0], points[-1]
    u = chord_parameters(points)
    if len(points) < 4:
        d = (p3 - p0) / 3.0
        if tangent_start is None and tangent_end is None:
            return (p0, p0 + d, p0 + 2 * d, p3), u

    if tangent_start is None:
        t0 = _unit(points[1] - points[0])
    else:
        t0 = _unit(tangent_start)
    if tangent_end is None:
        # Backward control direction; final forward derivative is the opposite.
        t1_back = _unit(points[-2] - points[-1])
    else:
        t1_back = -_unit(tangent_end)

    b0 = (1-u)**3
    b1 = 3*(1-u)**2*u
    b2 = 3*(1-u)*u**2
    b3 = u**3

    const = (b0+b1)[:, None] * p0 + (b2+b3)[:, None] * p3
    rhs = points - const
    M = np.zeros((2 * len(points), 2))
    y = rhs.reshape(-1)
    M[0::2, 0] = b1 * t0[0]
    M[1::2, 0] = b1 * t0[1]
    M[0::2, 1] = b2 * t1_back[0]
    M[1::2, 1] = b2 * t1_back[1]

    sol, *_ = np.linalg.lstsq(M, y, rcond=None)
    chord = max(float(np.linalg.norm(p3 - p0)), 1e-6)
    cap = max(2.0 * chord, 1.0)
    a = float(np.clip(sol[0], 0.0, cap))
    b = float(np.clip(sol[1], 0.0, cap))
    return (p0, p0 + a*t0, p3 + b*t1_back, p3), u


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
    p0,p1,p2,p3=ctrl
    t=float(t)
    return 3*(1-t)**2*(p1-p0) + 6*(1-t)*t*(p2-p1) + 3*t**2*(p3-p2)


def _angle_between_deg(a,b):
    ua=_unit(a); ub=_unit(b)
    dot=float(np.clip(np.dot(ua,ub),-1.0,1.0))
    return float(np.degrees(np.arccos(dot)))


def _model_at_index(points, bezier_model, k):
    """Evaluate independent Bézier-only model and tangent at contour index k."""
    k=int(np.clip(k,0,len(points)-1))
    for ctrl,i,j in bezier_model:
        if i <= k <= j:
            span=points[i:j+1]
            u=chord_parameters(span)
            local=k-i
            t=float(u[local])
            return cubic_eval(ctrl,np.array([t]))[0], cubic_derivative(ctrl,t), ctrl, t, i, j
    # Fallback to closest model segment endpoint.
    ctrl,i,j=min(bezier_model,key=lambda s:min(abs(k-s[1]),abs(k-s[2])))
    t=0.0 if abs(k-i)<=abs(k-j) else 1.0
    return cubic_eval(ctrl,np.array([t]))[0], cubic_derivative(ctrl,t), ctrl, t, i, j


def refine_lines_with_bezier_tangents(points, lines, bezier_model, tangent_threshold_deg=6.0,
                                      max_trim_px=8.0, min_line_length=3.0):
    """Shorten detected lines until independent Bézier tangents align at each endpoint.

    Start refinement searches forward into a line; end refinement searches backward.
    Thus lines can only shorten. Join points are taken from the independent Bézier-only
    model, not projected onto the line.
    """
    refined=[]; logs=[]
    for li,line in enumerate(lines,start=1):
        orig_a=np.asarray(line["a"],float); orig_b=np.asarray(line["b"],float)
        line_dir=_unit(orig_b-orig_a)
        start_i=int(line["start"]); end_i=int(line["end"])
        new_start_i=start_i; new_end_i=end_i
        new_a=orig_a.copy(); new_b=orig_b.copy()
        start_log={"status":"unchanged","angle_before_deg":None,"angle_after_deg":None,"trim_px":0.0}
        end_log={"status":"unchanged","angle_before_deg":None,"angle_after_deg":None,"trim_px":0.0}

        # Angle at original endpoints, for diagnostics.
        _,tan0,*_=_model_at_index(points,bezier_model,start_i)
        _,tan1,*_=_model_at_index(points,bezier_model,end_i)
        start_log["angle_before_deg"]=_angle_between_deg(line_dir,tan0)
        end_log["angle_before_deg"]=_angle_between_deg(line_dir,tan1)

        # Start: walk forward, shortening the line from its start.
        for k in range(start_i,end_i):
            p,tan,*_=_model_at_index(points,bezier_model,k)
            trim=float(np.linalg.norm(p-orig_a))
            if trim>max_trim_px: break
            if _angle_between_deg(line_dir,tan)<=tangent_threshold_deg:
                remain=float(np.linalg.norm(orig_b-p))
                if remain>=min_line_length:
                    new_start_i=k; new_a=p
                    start_log={"status":"refined" if k!=start_i else "already_aligned",
                               "angle_before_deg":start_log["angle_before_deg"],
                               "angle_after_deg":_angle_between_deg(line_dir,tan),
                               "trim_px":trim,"contour_index":int(k),
                               "join_point":[float(p[0]),float(p[1])]}
                break

        # End: walk backward, shortening the line from its end.
        for k in range(end_i,new_start_i,-1):
            p,tan,*_=_model_at_index(points,bezier_model,k)
            trim=float(np.linalg.norm(orig_b-p))
            if trim>max_trim_px: break
            if _angle_between_deg(line_dir,tan)<=tangent_threshold_deg:
                remain=float(np.linalg.norm(p-new_a))
                if remain>=min_line_length:
                    new_end_i=k; new_b=p
                    end_log={"status":"refined" if k!=end_i else "already_aligned",
                             "angle_before_deg":end_log["angle_before_deg"],
                             "angle_after_deg":_angle_between_deg(line_dir,tan),
                             "trim_px":trim,"contour_index":int(k),
                             "join_point":[float(p[0]),float(p[1])]}
                break

        new_line=dict(line)
        new_line.update({"start":int(new_start_i),"end":int(new_end_i),"a":new_a,"b":new_b,
                         "joint_refined":True})
        refined.append(new_line)
        logs.append({"line":li,"start":start_log,"end":end_log,
                     "original_start":[float(x) for x in orig_a],
                     "original_end":[float(x) for x in orig_b],
                     "refined_start":[float(x) for x in new_a],
                     "refined_end":[float(x) for x in new_b]})
    return refined,logs


def bezier_only_fit(points, threshold, min_points=5):
    n = len(points)
    out = []
    i = 0
    while i < n - 1:
        best = None
        for j in range(i + min_points - 1, n):
            span = points[i:j + 1]
            ctrl, u = fit_cubic(span)
            err = np.linalg.norm(span - cubic_eval(ctrl, u), axis=1)
            if float(err.max()) <= threshold:
                best = (j, ctrl)
            else:
                break
        if best is None:
            j = min(i + 2, n - 1)
            ctrl, _ = fit_cubic(points[i:j + 1])
            best = (j, ctrl)
        out.append((best[1], i, best[0]))
        i = best[0]
    return out


def point_to_segment_distance(points, a, b):
    ab = b - a
    ab2 = float(ab @ ab)
    if ab2 < 1e-12:
        return np.linalg.norm(points - a, axis=1)
    u = np.clip(((points-a) @ ab) / ab2, 0.0, 1.0)
    q = a + u[:, None] * ab
    return np.linalg.norm(points - q, axis=1)


def bezier_gap_fit(points, threshold, min_points=5, tangent_start=None, tangent_end=None):
    """Fit cubic Béziers to a gap, constraining only the external joint tangents."""
    n=len(points); out=[]; i=0
    while i<n-1:
        best=None
        for j in range(i+min_points-1,n):
            span=points[i:j+1]
            ts=tangent_start if i==0 else None
            te=tangent_end if j==n-1 else None
            ctrl,u=fit_cubic(span, tangent_start=ts, tangent_end=te)
            err=np.linalg.norm(span-cubic_eval(ctrl,u),axis=1)
            if float(err.max())<=threshold:
                best=(j,ctrl)
            else:
                break
        if best is None:
            j=min(i+2,n-1)
            ts=tangent_start if i==0 else None
            te=tangent_end if j==n-1 else None
            ctrl,_=fit_cubic(points[i:j+1], tangent_start=ts, tangent_end=te)
            best=(j,ctrl)
        out.append((best[1],i,best[0])); i=best[0]
    return out


def _cyclic_gap_points(points, start_idx, end_idx, start_point, end_point):
    """Points from a line end to the next line start, wrapping around the contour."""
    n=len(points); start_idx=int(start_idx); end_idx=int(end_idx)
    if end_idx >= start_idx:
        core=points[start_idx:end_idx+1].copy()
        index_base=start_idx
    else:
        core=np.concatenate([points[start_idx:],points[:end_idx+1]],axis=0).copy()
        index_base=start_idx
    if len(core)<2:
        core=np.vstack([start_point,end_point])
    else:
        core[0]=start_point; core[-1]=end_point
    return core,index_base


def hybrid_fit(points, line_threshold, bezier_threshold, angle_deadband_deg=0.2, rotation_run=4,
               rotation_total_deg=2.5, line_min_points=5, line_min_length=5.0,
               joint_refine=True, joint_tangent_threshold_deg=6.0,
               joint_max_trim_px=8.0, joint_min_line_length=3.0):
    lines, rot, detect_info = line_only_fit(points, line_threshold, line_min_points,
        angle_deadband_deg, rotation_run, rotation_total_deg, line_min_length)
    beziers_full = bezier_only_fit(rot, bezier_threshold)
    original_lines=[dict(x) for x in lines]
    joint_logs=[]
    if joint_refine and lines and beziers_full:
        lines,joint_logs=refine_lines_with_bezier_tangents(
            rot,lines,beziers_full,joint_tangent_threshold_deg,joint_max_trim_px,joint_min_line_length)

    # Assemble the closed contour in strict cyclic order:
    # line -> tangent-constrained Bézier gap -> next line -> ... -> first line.
    # This also handles the wraparound joint rather than leaving SVG Z to make a
    # hidden straight closing segment.
    final=[]
    if lines:
        lines=sorted(lines,key=lambda x:x["start"])
        m=len(lines)
        for idx,line in enumerate(lines):
            i,j=line["start"],line["end"]
            final.append(("L",(line["a"],line["b"]),i,j))
            nxt=lines[(idx+1)%m]
            gap,base_idx=_cyclic_gap_points(rot,j,nxt["start"],line["b"],nxt["a"])
            # If two lines touch exactly, there is no geometric gap to fit.
            if len(gap)>=2 and float(np.linalg.norm(gap[-1]-gap[0]))>1e-7:
                ts=_unit(line["b"]-line["a"])
                te=_unit(nxt["b"]-nxt["a"])
                for ctrl,gi,gj in bezier_gap_fit(gap,bezier_threshold,tangent_start=ts,tangent_end=te):
                    final.append(("B",ctrl,(base_idx+gi)%len(rot),(base_idx+gj)%len(rot)))
    else:
        # No straight spans: fit the whole loop as Béziers. Include the first point
        # again so the closing geometry is also modeled instead of delegated to SVG Z.
        closed=np.vstack([rot,rot[0]])
        for ctrl,gi,gj in bezier_gap_fit(closed,bezier_threshold):
            final.append(("B",ctrl,gi%len(rot),gj%len(rot)))

    return {"line_only":original_lines,"refined_lines":lines,"bezier_only":beziers_full,
            "line_threshold":float(line_threshold),"bezier_threshold":float(bezier_threshold),
            "kept_lines":lines,"combined":final,"rotated_points":rot,
            "line_detection":detect_info,"joint_refinement":joint_logs}


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


def save_joint_refinement(path, rgb, fits, dpi):
    fig, ax = plt.subplots(figsize=(7, 7))
    prepare_axis(ax, rgb, "Joint tangent refinement")
    for fit in fits:
        # Independent Bézier-only model is the guide used to choose shortened line endpoints.
        for ctrl,_,_ in fit["bezier_only"]:
            c=cubic_eval(ctrl,np.linspace(0,1,120))
            ax.plot(c[:,0],c[:,1],color=BEZIER_COLOR,linewidth=1.6,alpha=0.32)
        # Original detector lines, faint dashed.
        for line in fit["line_only"]:
            a,b=line["a"],line["b"]
            ax.plot([a[0],b[0]],[a[1],b[1]],color=LINE_COLOR,linewidth=2.0,alpha=0.23,linestyle="--")
        # Refined lines, joint points, and tangent comparison arrows.
        rot=fit["rotated_points"]
        model=fit["bezier_only"]
        for line in fit.get("refined_lines",[]):
            a,b=line["a"],line["b"]; ld=_unit(b-a)
            draw_visible(ax,[a[0],b[0]],[a[1],b[1]],LINE_COLOR,3.3)
            for point,k in ((a,line["start"]),(b,line["end"])):
                _,guide_tan,*_=_model_at_index(rot,model,k)
                gt=_unit(guide_tan)
                ax.scatter([point[0]],[point[1]],s=44,facecolor="white",edgecolor="black",zorder=8)
                scale=5.0
                ax.arrow(point[0],point[1],ld[0]*scale,ld[1]*scale,width=0.10,
                         head_width=1.0,head_length=1.3,color=LINE_COLOR,length_includes_head=True,zorder=9)
                ax.arrow(point[0],point[1],gt[0]*scale,gt[1]*scale,width=0.08,
                         head_width=0.9,head_length=1.2,color=BEZIER_COLOR,length_includes_head=True,zorder=9,alpha=0.9)
    fig.savefig(path,dpi=dpi,bbox_inches="tight")
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
    fits = [hybrid_fit(points, line_threshold, bezier_threshold, args.line_angle_deadband_deg,
                       args.line_rotation_run, args.line_rotation_total_deg,
                       args.line_min_points, args.line_min_length,
                       args.joint_refine == "on", args.joint_tangent_threshold_deg,
                       args.joint_max_trim_px, args.joint_min_line_length) for points in contours]

    p0 = args.output_dir / "00_all_contours.png"
    p1 = args.output_dir / "01_points_normals.png"
    p2 = args.output_dir / "02_line_only.png"
    p3 = args.output_dir / "03_bezier_only.png"
    p4j = args.output_dir / "04_joint_refinement.png"
    p4 = args.output_dir / "05_combined.png"
    p5 = args.output_dir / "06_final_filled.png"
    svg = args.output_dir / "final.svg"
    objects_svg = args.output_dir / "final_objects.svg"
    contact = args.output_dir / "pipeline_contact_sheet.png"
    metadata = args.output_dir / "metadata.json"
    line_log = args.output_dir / "line_fit_log.json"
    joint_log = args.output_dir / "joint_refinement_log.json"

    save_all_contours(p0, rgb, contour_records, args.keep_contour, args.diagnostic_dpi)
    save_points(p1, rgb, contours, normals, args.diagnostic_dpi)
    save_line_only(p2, rgb, fits, args.diagnostic_dpi)
    save_bezier_only(p3, rgb, fits, args.diagnostic_dpi)
    save_joint_refinement(p4j, rgb, fits, args.diagnostic_dpi)
    save_combined(p4, rgb, fits, args.diagnostic_dpi)
    save_final(svg, p5, w, h, fits, args.fill)
    save_objects(objects_svg, w, h, selected_records, contours, fits, args.fill, object_groups)
    save_contact_sheet([p0, p1, p2, p3, p4j, p4, p5], contact)

    line_log_data = {
        "legacy_threshold_px": args.threshold,
        "line_threshold_px": line_threshold,
        "bezier_threshold_px": bezier_threshold,
        "angle_deadband_deg": args.line_angle_deadband_deg,
        "rotation_run": args.line_rotation_run,
        "rotation_total_deg": args.line_rotation_total_deg,
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

    joint_log_data = {
        "enabled": args.joint_refine == "on",
        "tangent_threshold_deg": args.joint_tangent_threshold_deg,
        "max_trim_px": args.joint_max_trim_px,
        "min_remaining_line_length_px": args.joint_min_line_length,
        "contours": [
            {"contour": ci+1, "joints": fit.get("joint_refinement", [])}
            for ci, fit in enumerate(fits)
        ],
    }
    joint_log.write_text(json.dumps(joint_log_data, indent=2), encoding="utf-8")

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
        "line_angle_deadband_deg": args.line_angle_deadband_deg,
        "line_rotation_run": args.line_rotation_run,
        "line_rotation_total_deg": args.line_rotation_total_deg,
        "line_min_length": args.line_min_length,
        "line_min_points": args.line_min_points,
        "joint_refine": args.joint_refine,
        "joint_tangent_threshold_deg": args.joint_tangent_threshold_deg,
        "joint_max_trim_px": args.joint_max_trim_px,
        "joint_min_line_length": args.joint_min_line_length,
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
