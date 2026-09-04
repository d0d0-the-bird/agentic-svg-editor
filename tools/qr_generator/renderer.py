#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import math
import mimetypes
from pathlib import Path
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

EC_MAP = {
    "L": ERROR_CORRECT_L,
    "M": ERROR_CORRECT_M,
    "Q": ERROR_CORRECT_Q,
    "H": ERROR_CORRECT_H,
}

MODULE_SHAPES = {
    "square",
    "rounded",
    "circle",
    "diamond",
    "squircle",
    "star",
    "petal",
    "soft_blob",
    "capsule_h",
    "capsule_v",
}
EYE_SHAPES = {"square", "rounded", "circle", "squircle", "diamond"}
PATTERNS = {"uniform", "checker", "diagonal", "rings"}
GRADIENT_TYPES = {"linear", "radial"}
BRIDGE_STYLES = {"full", "waisted"}

DEFAULT_SPEC: dict[str, Any] = {
    "value": "https://example.com",
    "error_correction": "H",
    "quiet_zone": 4,
    "module_size": 12,
    "modules": {
        "shape": "rounded",
        "secondary_shape": None,
        "pattern": "uniform",
        "scale": 0.86,
        "color": "#111111",
        "secondary_color": None,
        "radius": 0.32,
        "gradient": {
            "enabled": False,
            "type": "linear",
            "from": None,
            "to": None,
            "stops": None,
            "angle": 45,
            "mode": "sampled",
        },
        "accents": {
            "enabled": False,
            "seed": 1,
            "probability": 0.0,
            "colors": ["#25F4EE", "#FE2C55"],
            "shapes": ["circle"],
            "scale": None,
        },
        "connectivity": {
            "enabled": False,
            "horizontal_merge": 0.85,
            "vertical_merge": 0.80,
            "corner_merge": 0.35,
            "max_run": 4,
            "connector_overlap": 0.18,
            "selection": {
                "seed": 1,
                "horizontal_probability": 1.0,
                "vertical_probability": 1.0,
                "run_probability": 1.0,
            },
            "bridge": {
                "style": "waisted",
                "max_thickness": 0.62,
                "waist_ratio": 0.55,
                "overlap": 0.14,
                "curve": 0.65,
                "connect_mixed_styles": False,
            },
            "corner_fillers": {
                "enabled": False,
            },
        },
    },
    "eyes": {
        "frame_shape": "rounded",
        "pupil_shape": "circle",
        "color": "#111111",
        "pupil_color": None,
        "radius": 0.28,
        "frame_radius": None,
        "inner_radius": None,
        "pupil_radius": None,
        "radius_mode": "module",
        "gradient": {
            "enabled": False,
            "type": "linear",
            "from": None,
            "to": None,
            "stops": None,
            "angle": 45,
            "mode": "local",
        },
        "instances": {},
    },
    "background": {"color": "#FFFFFF"},
    "logo": {
        "enabled": False,
        "path": None,
        "scale": 0.18,
        "knockout": True,
        "padding_modules": 0.65,
        "radius_modules": 0.8,
        "clear_shape": "rounded",
        "clear_intersection_mode": "any_overlap",
        "remove_partial_modules": True,
        "remove_partial_bridges": True,
        "badge_enabled": False,
        "badge_shape": "circle",
        "badge_color": "#FFFFFF",
        "badge_stroke_color": None,
        "badge_stroke_width_modules": 0.0,
        "badge_padding_modules": 0.90,
        "badge_radius_modules": 1.0,
    },
    "decorations": {
        "enabled": False,
        "seed": 1,
        "edge_dots": {"enabled": False, "count": 8, "colors": ["#F58220"], "size_modules": 0.35},
        "sparkles": {"enabled": False, "count": 4, "colors": ["#F58220"], "size_modules": 0.55},
        "logo_halo": {"enabled": False, "color": "#F58220", "opacity": 0.14, "padding_modules": 0.6},
    },
    "output": {"validate": True},
}


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    out = {k: (deep_merge(v, {}) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    s = deep_merge(DEFAULT_SPEC, spec or {})
    s["error_correction"] = str(s["error_correction"]).upper()
    if s["error_correction"] not in EC_MAP:
        raise ValueError("error_correction must be L, M, Q, or H")
    s["quiet_zone"] = max(0, int(s["quiet_zone"]))
    s["module_size"] = max(2, int(s["module_size"]))

    mods = s["modules"]
    if mods["shape"] not in MODULE_SHAPES:
        raise ValueError(f"invalid modules.shape; expected one of {sorted(MODULE_SHAPES)}")
    if mods.get("secondary_shape") is not None and mods["secondary_shape"] not in MODULE_SHAPES:
        raise ValueError(f"invalid modules.secondary_shape; expected one of {sorted(MODULE_SHAPES)} or null")
    if mods.get("pattern", "uniform") not in PATTERNS:
        raise ValueError(f"invalid modules.pattern; expected one of {sorted(PATTERNS)}")
    mods["scale"] = _clamp(float(mods.get("scale", 0.86)), 0.35, 1.0)
    mods["radius"] = _clamp(float(mods.get("radius", 0.32)), 0.0, 0.5)
    grad = mods.setdefault("gradient", {})
    grad["enabled"] = bool(grad.get("enabled", False))
    grad["type"] = str(grad.get("type", "linear"))
    if grad["type"] not in GRADIENT_TYPES:
        raise ValueError(f"invalid modules.gradient.type; expected one of {sorted(GRADIENT_TYPES)}")
    grad["angle"] = float(grad.get("angle", 45))
    grad["mode"] = str(grad.get("mode", "sampled"))
    if grad["mode"] not in {"sampled", "continuous"}:
        raise ValueError("modules.gradient.mode must be sampled or continuous")
    stops = grad.get("stops")
    if stops is not None:
        if isinstance(stops, str):
            stops = [stops]
        stops = list(stops)
        if len(stops) < 2:
            stops = None
    grad["stops"] = stops
    accents = mods.setdefault("accents", {"enabled": False, "seed": 1, "probability": 0.0, "colors": ["#25F4EE", "#FE2C55"], "shapes": ["circle"], "scale": None})
    accents["enabled"] = bool(accents.get("enabled", False))
    accents["seed"] = int(accents.get("seed", 1))
    accents["probability"] = _clamp(float(accents.get("probability", 0.0)), 0.0, 1.0)
    colors = accents.get("colors") or ["#25F4EE", "#FE2C55"]
    if isinstance(colors, str): colors = [colors]
    accents["colors"] = list(colors)
    shapes = accents.get("shapes") or ["circle"]
    if isinstance(shapes, str): shapes = [shapes]
    shapes = [str(sh) for sh in shapes]
    if any(sh not in MODULE_SHAPES for sh in shapes):
        raise ValueError(f"invalid modules.accents.shapes; expected values from {sorted(MODULE_SHAPES)}")
    accents["shapes"] = shapes
    accents["scale"] = None if accents.get("scale") is None else _clamp(float(accents.get("scale")), 0.35, 1.0)
    conn = mods.setdefault(
        "connectivity",
        {
            "enabled": False,
            "horizontal_merge": 0.85,
            "vertical_merge": 0.80,
            "corner_merge": 0.35,
            "max_run": 4,
            "connector_overlap": 0.18,
            "bridge": {
                "style": "waisted",
                "max_thickness": 0.62,
                "waist_ratio": 0.55,
                "overlap": 0.14,
                "curve": 0.65,
                "connect_mixed_styles": False,
            },
            "corner_fillers": {
                "enabled": False,
            },
        },
    )
    conn["enabled"] = bool(conn.get("enabled", False))
    conn["horizontal_merge"] = _clamp(float(conn.get("horizontal_merge", 0.85)), 0.0, 1.0)
    conn["vertical_merge"] = _clamp(float(conn.get("vertical_merge", 0.80)), 0.0, 1.0)
    conn["corner_merge"] = _clamp(float(conn.get("corner_merge", 0.35)), 0.0, 1.0)
    conn["max_run"] = max(2, int(conn.get("max_run", 4)))
    conn["connector_overlap"] = _clamp(float(conn.get("connector_overlap", 0.18)), 0.0, 0.45)
    selection = conn.setdefault("selection", {"seed": 1, "horizontal_probability": 1.0, "vertical_probability": 1.0, "run_probability": 1.0})
    selection["seed"] = int(selection.get("seed", 1))
    selection["horizontal_probability"] = _clamp(float(selection.get("horizontal_probability", 1.0)), 0.0, 1.0)
    selection["vertical_probability"] = _clamp(float(selection.get("vertical_probability", 1.0)), 0.0, 1.0)
    selection["run_probability"] = _clamp(float(selection.get("run_probability", 1.0)), 0.0, 1.0)
    bridge = conn.setdefault(
        "bridge",
        {
            "style": "waisted",
            "max_thickness": 0.62,
            "waist_ratio": 0.55,
            "overlap": 0.14,
            "curve": 0.65,
        },
    )
    bridge["style"] = str(bridge.get("style", "waisted"))
    if bridge["style"] not in BRIDGE_STYLES:
        raise ValueError(f"invalid modules.connectivity.bridge.style; expected one of {sorted(BRIDGE_STYLES)}")
    bridge["max_thickness"] = _clamp(float(bridge.get("max_thickness", 0.62)), 0.10, 1.20)
    bridge["waist_ratio"] = _clamp(float(bridge.get("waist_ratio", 0.55)), 0.10, 1.0)
    bridge["overlap"] = _clamp(float(bridge.get("overlap", conn.get("connector_overlap", 0.18))), 0.0, 0.45)
    bridge["curve"] = _clamp(float(bridge.get("curve", 0.65)), 0.0, 1.0)
    bridge["connect_mixed_styles"] = bool(bridge.get("connect_mixed_styles", False))
    corner_fillers = conn.setdefault("corner_fillers", {"enabled": False})
    corner_fillers["enabled"] = bool(corner_fillers.get("enabled", False))

    eyes = s["eyes"]
    for key in ("frame_shape", "pupil_shape"):
        if eyes[key] not in EYE_SHAPES:
            raise ValueError(f"invalid eyes.{key}; expected one of {sorted(EYE_SHAPES)}")
    eyes["radius"] = _clamp(float(eyes.get("radius", 0.28)), 0.0, 0.5)
    for key in ("frame_radius", "inner_radius", "pupil_radius"):
        value = eyes.get(key)
        eyes[key] = eyes["radius"] if value is None else _clamp(float(value), 0.0, 0.5)
    eyes["radius_mode"] = str(eyes.get("radius_mode", "module"))
    if eyes["radius_mode"] not in {"module", "layer"}:
        raise ValueError("eyes.radius_mode must be module or layer")
    eye_grad = eyes.setdefault("gradient", {"enabled": False, "type": "linear", "from": None, "to": None, "stops": None, "angle": 45})
    eye_grad["enabled"] = bool(eye_grad.get("enabled", False))
    eye_grad["type"] = str(eye_grad.get("type", "linear"))
    if eye_grad["type"] not in GRADIENT_TYPES:
        raise ValueError(f"invalid eyes.gradient.type; expected one of {sorted(GRADIENT_TYPES)}")
    eye_grad["angle"] = float(eye_grad.get("angle", 45))
    eye_grad["mode"] = str(eye_grad.get("mode", "local"))
    if eye_grad["mode"] not in {"local", "continuous"}:
        raise ValueError("eyes.gradient.mode must be local or continuous")
    eye_stops = eye_grad.get("stops")
    if eye_stops is not None:
        if isinstance(eye_stops, str): eye_stops = [eye_stops]
        eye_stops = list(eye_stops)
        if len(eye_stops) < 2: eye_stops = None
    eye_grad["stops"] = eye_stops
    instances = eyes.setdefault("instances", {}) or {}
    normalized_instances = {}
    for name in ("top_left", "top_right", "bottom_left"):
        raw = instances.get(name) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"eyes.instances.{name} must be a mapping")
        item = {}
        for key in ("color", "pupil_color"):
            if raw.get(key) is not None: item[key] = str(raw[key])
        for key in ("frame_radius", "inner_radius", "pupil_radius"):
            if raw.get(key) is not None: item[key] = _clamp(float(raw[key]), 0.0, 0.5)
        normalized_instances[name] = item
    eyes["instances"] = normalized_instances

    logo = s["logo"]
    logo["scale"] = _clamp(float(logo.get("scale", 0.18)), 0.05, 0.30)
    logo["padding_modules"] = max(0.0, float(logo.get("padding_modules", 0.65)))
    logo["radius_modules"] = max(0.0, float(logo.get("radius_modules", 0.8)))
    logo["clear_shape"] = str(logo.get("clear_shape", "rounded"))
    if logo["clear_shape"] not in {"square", "rounded", "circle", "squircle"}:
        raise ValueError("invalid logo.clear_shape")
    logo["clear_intersection_mode"] = str(logo.get("clear_intersection_mode", "any_overlap"))
    if logo["clear_intersection_mode"] not in {"center", "any_overlap"}:
        raise ValueError("invalid logo.clear_intersection_mode")
    logo["remove_partial_modules"] = bool(logo.get("remove_partial_modules", True))
    logo["remove_partial_bridges"] = bool(logo.get("remove_partial_bridges", True))
    logo["badge_enabled"] = bool(logo.get("badge_enabled", False))
    if logo.get("badge_shape", "circle") not in {"square", "rounded", "circle", "squircle"}:
        raise ValueError("invalid logo.badge_shape")
    logo["badge_stroke_width_modules"] = max(0.0, float(logo.get("badge_stroke_width_modules", 0.0)))
    logo["badge_padding_modules"] = max(0.0, float(logo.get("badge_padding_modules", 0.90)))
    logo["badge_radius_modules"] = max(0.0, float(logo.get("badge_radius_modules", 1.0)))

    deco = s.setdefault("decorations", {})
    deco["enabled"] = bool(deco.get("enabled", False))
    deco["seed"] = int(deco.get("seed", 1))
    for key, defaults in {
        "edge_dots": {"enabled": False, "count": 8, "colors": ["#F58220"], "size_modules": 0.35},
        "sparkles": {"enabled": False, "count": 4, "colors": ["#F58220"], "size_modules": 0.55},
    }.items():
        block = deco.setdefault(key, defaults.copy())
        block["enabled"] = bool(block.get("enabled", False))
        block["count"] = max(0, int(block.get("count", defaults["count"])))
        colors = block.get("colors") or defaults["colors"]
        if isinstance(colors, str):
            colors = [colors]
        block["colors"] = list(colors)
        block["size_modules"] = max(0.05, float(block.get("size_modules", defaults["size_modules"])))
    halo = deco.setdefault("logo_halo", {"enabled": False, "color": "#F58220", "opacity": 0.14, "padding_modules": 0.6})
    halo["enabled"] = bool(halo.get("enabled", False))
    halo["opacity"] = _clamp(float(halo.get("opacity", 0.14)), 0.0, 1.0)
    halo["padding_modules"] = max(0.0, float(halo.get("padding_modules", 0.6)))
    return s


def qr_matrix(spec: dict[str, Any]) -> list[list[bool]]:
    s = normalize_spec(spec)
    qr = qrcode.QRCode(
        version=None,
        error_correction=EC_MAP[s["error_correction"]],
        box_size=1,
        border=0,
    )
    qr.add_data(str(s["value"]))
    qr.make(fit=True)
    return [[bool(v) for v in row] for row in qr.get_matrix()]


def finder_cells(n: int) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for ox, oy in [(0, 0), (n - 7, 0), (0, n - 7)]:
        out |= {(x, y) for y in range(oy, oy + 7) for x in range(ox, ox + 7)}
    return out


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = str(color).strip()
    if c.startswith("#"):
        c = c[1:]
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError(f"unsupported color format: {color!r}")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(_clamp(int(round(v)), 0, 255) for v in rgb)


def _lerp_color(a: str, b: str, t: float) -> str:
    t = _clamp(float(t), 0.0, 1.0)
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return _rgb_to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def _gradient_color(grad: dict[str, Any], cx: float, cy: float, px: float) -> str:
    stops = grad.get("stops")
    a = grad.get("from") or (stops[0] if stops else "#111111")
    b = grad.get("to") or (stops[-1] if stops else a)
    if not grad.get("enabled"):
        return a
    if grad.get("type") == "radial":
        center = px / 2.0
        maxd = math.hypot(center, center) or 1.0
        t = math.hypot(cx - center, cy - center) / maxd
    else:
        ang = math.radians(float(grad.get("angle", 45)))
        ux, uy = math.cos(ang), math.sin(ang)
        proj = cx * ux + cy * uy
        minproj = min(0.0, px * ux) + min(0.0, px * uy)
        maxproj = max(0.0, px * ux) + max(0.0, px * uy)
        span = (maxproj - minproj) or 1.0
        t = (proj - minproj) / span
    t = _clamp(t, 0.0, 1.0)
    if stops and len(stops) >= 2:
        scaled = t * (len(stops) - 1)
        idx = min(len(stops) - 2, int(math.floor(scaled)))
        local_t = scaled - idx
        return _lerp_color(stops[idx], stops[idx + 1], local_t)
    return _lerp_color(a, b, t)


def _squircle_path(x: float, y: float, size: float) -> str:
    k = 0.16 * size
    x2, y2 = x + size, y + size
    mx, my = x + size / 2.0, y + size / 2.0
    return (
        f"M {mx:.4f},{y:.4f} "
        f"C {x2-k:.4f},{y:.4f} {x2:.4f},{y+k:.4f} {x2:.4f},{my:.4f} "
        f"C {x2:.4f},{y2-k:.4f} {x2-k:.4f},{y2:.4f} {mx:.4f},{y2:.4f} "
        f"C {x+k:.4f},{y2:.4f} {x:.4f},{y2-k:.4f} {x:.4f},{my:.4f} "
        f"C {x:.4f},{y+k:.4f} {x+k:.4f},{y:.4f} {mx:.4f},{y:.4f} Z"
    )


def _star_path(x: float, y: float, size: float) -> str:
    cx, cy = x + size / 2.0, y + size / 2.0
    outer = size * 0.50
    inner = size * 0.22
    pts: list[tuple[float, float]] = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = outer if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return "M " + " L ".join(f"{px:.4f},{py:.4f}" for px, py in pts) + " Z"


def _petal_path(x: float, y: float, size: float) -> str:
    cx, cy = x + size / 2.0, y + size / 2.0
    r = size * 0.22
    tip = size * 0.50
    ctrl = size * 0.34
    return (
        f"M {cx:.4f},{cy-r:.4f} "
        f"C {cx+ctrl:.4f},{cy-tip:.4f} {cx+tip:.4f},{cy-ctrl:.4f} {cx+r:.4f},{cy:.4f} "
        f"C {cx+tip:.4f},{cy+ctrl:.4f} {cx+ctrl:.4f},{cy+tip:.4f} {cx:.4f},{cy+r:.4f} "
        f"C {cx-ctrl:.4f},{cy+tip:.4f} {cx-tip:.4f},{cy+ctrl:.4f} {cx-r:.4f},{cy:.4f} "
        f"C {cx-tip:.4f},{cy-ctrl:.4f} {cx-ctrl:.4f},{cy-tip:.4f} {cx:.4f},{cy-r:.4f} Z"
    )


def _soft_blob_path(x: float, y: float, size: float) -> str:
    pts = [
        (0.52, 0.04),
        (0.84, 0.10),
        (0.96, 0.42),
        (0.88, 0.82),
        (0.52, 0.96),
        (0.15, 0.88),
        (0.04, 0.50),
        (0.14, 0.16),
    ]
    p = [(x + px * size, y + py * size) for px, py in pts]
    d = [f"M {p[0][0]:.4f},{p[0][1]:.4f}"]
    n = len(p)
    for i in range(n):
        p0 = p[(i - 1) % n]
        p1 = p[i % n]
        p2 = p[(i + 1) % n]
        p3 = p[(i + 2) % n]
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        d.append(f"C {c1x:.4f},{c1y:.4f} {c2x:.4f},{c2y:.4f} {p2[0]:.4f},{p2[1]:.4f}")
    d.append("Z")
    return " ".join(d)


def shape_svg(shape: str, x: float, y: float, size: float, color: str, radius: float = 0.25) -> str:
    esc = html.escape(color)
    if shape == "circle":
        r = size / 2.0
        return f'<circle cx="{x+r:.4f}" cy="{y+r:.4f}" r="{r:.4f}" fill="{esc}"/>'
    if shape == "diamond":
        h = size / 2.0
        pts = f"{x+h:.4f},{y:.4f} {x+size:.4f},{y+h:.4f} {x+h:.4f},{y+size:.4f} {x:.4f},{y+h:.4f}"
        return f'<polygon points="{pts}" fill="{esc}"/>'
    if shape == "rounded":
        r = size * radius
        return f'<rect x="{x:.4f}" y="{y:.4f}" width="{size:.4f}" height="{size:.4f}" rx="{r:.4f}" ry="{r:.4f}" fill="{esc}"/>'
    if shape == "squircle":
        return f'<path d="{_squircle_path(x, y, size)}" fill="{esc}"/>'
    if shape == "star":
        return f'<path d="{_star_path(x, y, size)}" fill="{esc}"/>'
    if shape == "petal":
        return f'<path d="{_petal_path(x, y, size)}" fill="{esc}"/>'
    if shape == "soft_blob":
        return f'<path d="{_soft_blob_path(x, y, size)}" fill="{esc}"/>'
    if shape == "capsule_h":
        h = size * 0.62
        yy = y + (size - h) / 2.0
        return f'<rect x="{x:.4f}" y="{yy:.4f}" width="{size:.4f}" height="{h:.4f}" rx="{h/2:.4f}" ry="{h/2:.4f}" fill="{esc}"/>'
    if shape == "capsule_v":
        w = size * 0.62
        xx = x + (size - w) / 2.0
        return f'<rect x="{xx:.4f}" y="{y:.4f}" width="{w:.4f}" height="{size:.4f}" rx="{w/2:.4f}" ry="{w/2:.4f}" fill="{esc}"/>'
    return f'<rect x="{x:.4f}" y="{y:.4f}" width="{size:.4f}" height="{size:.4f}" fill="{esc}"/>'


def _rounded_rect_svg(x: float, y: float, w: float, h: float, color: str, radius: float) -> str:
    return (
        f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" '
        f'rx="{max(0.0, min(radius, min(w, h)/2)):.4f}" ry="{max(0.0, min(radius, min(w, h)/2)):.4f}" fill="{html.escape(color)}"/>'
    )


def _run_radius(shape: str, h: float, radius: float, strength: float) -> float:
    if shape in {"square", "diamond"}:
        return h * (0.08 + 0.08 * strength)
    if shape in {"circle", "capsule_h", "capsule_v", "soft_blob", "petal", "star"}:
        return h * 0.50
    if shape == "squircle":
        return h * 0.38
    return h * _clamp(radius, 0.10, 0.50)


def _bridge_half_thickness(t: float, max_thickness: float, style: str, waist_ratio: float, curve: float) -> float:
    max_half = max_thickness / 2.0
    if style == "full":
        return max_half
    t = _clamp(float(t), 0.0, 1.0)
    mid_half = max_half * _clamp(waist_ratio, 0.10, 1.0)
    easing = math.sin(math.pi * t)
    exponent = 2.35 - 1.85 * _clamp(curve, 0.0, 1.0)
    blend = easing ** exponent
    return max_half - (max_half - mid_half) * blend


def _bridge_path(x0: float, y0: float, x1: float, y1: float, max_thickness: float, style: str, waist_ratio: float, curve: float, samples: int = 16) -> str:
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        r = max_thickness / 2.0
        return f'M {x0-r:.4f},{y0:.4f} a {r:.4f},{r:.4f} 0 1,0 {2*r:.4f},0 a {r:.4f},{r:.4f} 0 1,0 {-2*r:.4f},0 Z'
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    top = []
    bottom = []
    samples = max(4, int(samples))
    for i in range(samples + 1):
        t = i / samples
        px = x0 + dx * t
        py = y0 + dy * t
        hh = _bridge_half_thickness(t, max_thickness, style, waist_ratio, curve)
        top.append((px + nx * hh, py + ny * hh))
        bottom.append((px - nx * hh, py - ny * hh))
    pts = top + list(reversed(bottom))
    return 'M ' + ' L '.join(f'{px:.4f},{py:.4f}' for px, py in pts) + ' Z'


def _bridge_svg(x0: float, y0: float, x1: float, y1: float, color: str, max_thickness: float, style: str, waist_ratio: float, curve: float) -> str:
    return f'<path d="{_bridge_path(x0, y0, x1, y1, max_thickness, style, waist_ratio, curve)}" fill="{html.escape(color)}"/>'


def _svg_gradient_def(gradient_id: str, grad: dict[str, Any], *, px: float | None = None, data_min: float | None = None, data_max: float | None = None) -> str:
    stops = grad.get("stops") or [grad.get("from") or "#111111", grad.get("to") or grad.get("from") or "#111111"]
    continuous = px is not None
    if grad.get("type") == "radial":
        if continuous:
            center = px / 2.0
            radius = (data_max - data_min) * 0.72 if data_min is not None and data_max is not None else px * 0.70
            head = f'<radialGradient id="{gradient_id}" gradientUnits="userSpaceOnUse" cx="{center:.4f}" cy="{center:.4f}" r="{radius:.4f}">'
        else:
            head = f'<radialGradient id="{gradient_id}" cx="50%" cy="50%" r="70%">'
    else:
        angle = math.radians(float(grad.get("angle", 45)))
        ux, uy = math.cos(angle), math.sin(angle)
        if continuous:
            lo = data_min if data_min is not None else 0.0
            hi = data_max if data_max is not None else px
            cx = (lo + hi) / 2.0
            cy = (lo + hi) / 2.0
            half = (hi - lo) / 2.0
            x1 = cx - half * ux
            y1 = cy - half * uy
            x2 = cx + half * ux
            y2 = cy + half * uy
            head = f'<linearGradient id="{gradient_id}" gradientUnits="userSpaceOnUse" x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}">'
        else:
            x1 = 50 - 50 * ux
            y1 = 50 - 50 * uy
            x2 = 50 + 50 * ux
            y2 = 50 + 50 * uy
            head = f'<linearGradient id="{gradient_id}" x1="{x1:.2f}%" y1="{y1:.2f}%" x2="{x2:.2f}%" y2="{y2:.2f}%">'
    nodes = []
    for i, col in enumerate(stops):
        off = 100 * i / max(1, len(stops) - 1)
        nodes.append(f'<stop offset="{off:.2f}%" stop-color="{html.escape(col)}"/>')
    return head + ''.join(nodes) + ("</radialGradient>" if grad.get("type") == "radial" else "</linearGradient>")


def _logo_layout_metrics(s: dict[str, Any], n: int, m: float, px: float) -> dict[str, float] | None:
    logo = s.get("logo", {})
    if not (logo.get("enabled") and logo.get("path")):
        return None
    logo_size = n * m * float(logo["scale"])
    lx = (px - logo_size) / 2.0
    ly = (px - logo_size) / 2.0
    badge_pad = (logo["badge_padding_modules"] if logo.get("badge_enabled") else logo["padding_modules"]) * m
    bs = logo_size + 2 * badge_pad
    bx = lx - badge_pad
    by = ly - badge_pad
    return {"logo_size": logo_size, "lx": lx, "ly": ly, "badge_pad": badge_pad, "bs": bs, "bx": bx, "by": by}


def _logo_clear_geometry(s: dict[str, Any], n: int, m: float, px: float) -> dict[str, Any] | None:
    logo = s.get("logo", {})
    if not (logo.get("enabled") and logo.get("path") and logo.get("knockout", True)):
        return None
    metrics = _logo_layout_metrics(s, n, m, px)
    if not metrics:
        return None
    shape = str(logo.get("clear_shape", "rounded"))
    geom = {"shape": shape, **metrics}
    if shape == "circle":
        geom.update({"cx": px / 2.0, "cy": px / 2.0, "r": metrics["bs"] / 2.0})
    else:
        geom.update({"x": metrics["bx"], "y": metrics["by"], "w": metrics["bs"], "h": metrics["bs"], "radius": logo["radius_modules"] * m})
    return geom


def _point_in_clear(px: float, py: float, geom: dict[str, Any] | None) -> bool:
    if not geom:
        return False
    if geom["shape"] == "circle":
        dx = px - geom["cx"]
        dy = py - geom["cy"]
        return dx * dx + dy * dy <= geom["r"] * geom["r"]
    return geom["x"] <= px <= geom["x"] + geom["w"] and geom["y"] <= py <= geom["y"] + geom["h"]


def _rect_intersects_clear(x: float, y: float, w: float, h: float, geom: dict[str, Any] | None) -> bool:
    if not geom:
        return False
    if geom["shape"] == "circle":
        nx = max(x, min(geom["cx"], x + w))
        ny = max(y, min(geom["cy"], y + h))
        dx = nx - geom["cx"]
        dy = ny - geom["cy"]
        return dx * dx + dy * dy <= geom["r"] * geom["r"]
    return not (x + w < geom["x"] or x > geom["x"] + geom["w"] or y + h < geom["y"] or y > geom["y"] + geom["h"])


def _module_cell_overlaps_clear(cell: tuple[int, int], qz: int, m: float, inset: float, draw_size: float, geom: dict[str, Any] | None, mode: str) -> bool:
    x, y = cell
    rx = (x + qz) * m + inset
    ry = (y + qz) * m + inset
    if mode == "center":
        return _point_in_clear(rx + draw_size / 2.0, ry + draw_size / 2.0, geom)
    return _rect_intersects_clear(rx, ry, draw_size, draw_size, geom)


def _clear_shape_svg(geom: dict[str, Any], color: str) -> str:
    if geom["shape"] == "circle":
        return f'<circle id="logo-knockout" cx="{geom["cx"]:.4f}" cy="{geom["cy"]:.4f}" r="{geom["r"]:.4f}" fill="{html.escape(color)}"/>'
    if geom["shape"] == "squircle":
        return f'<path id="logo-knockout" d="{_squircle_path(geom["x"], geom["y"], geom["w"])}" fill="{html.escape(color)}"/>'
    rr = 0.0 if geom["shape"] == "square" else geom.get("radius", 0.0)
    return f'<rect id="logo-knockout" x="{geom["x"]:.4f}" y="{geom["y"]:.4f}" width="{geom["w"]:.4f}" height="{geom["h"]:.4f}" rx="{rr:.4f}" ry="{rr:.4f}" fill="{html.escape(color)}"/>'


def eye_svg(shape: str, x: float, y: float, m: float, color: str, bg: str, pupil_shape: str, pupil_color: str, frame_radius: float, inner_radius: float, pupil_radius: float, radius_mode: str = "module") -> str:
    def p(sh: str, xx: float, yy: float, ss: float, fill: str, radius_value: float) -> str:
        if sh == "circle":
            r = ss / 2.0
            return f'<circle cx="{xx+r:.4f}" cy="{yy+r:.4f}" r="{r:.4f}" fill="{html.escape(fill)}"/>'
        if sh == "diamond":
            h = ss / 2.0
            pts = f"{xx+h:.4f},{yy:.4f} {xx+ss:.4f},{yy+h:.4f} {xx+h:.4f},{yy+ss:.4f} {xx:.4f},{yy+h:.4f}"
            return f'<polygon points="{pts}" fill="{html.escape(fill)}"/>'
        if sh == "squircle":
            return f'<path d="{_squircle_path(xx, yy, ss)}" fill="{html.escape(fill)}"/>'
        if sh == "rounded":
            rr = min(ss / 2.0, (ss if radius_mode == "layer" else m) * radius_value)
            return f'<rect x="{xx:.4f}" y="{yy:.4f}" width="{ss:.4f}" height="{ss:.4f}" rx="{rr:.4f}" ry="{rr:.4f}" fill="{html.escape(fill)}"/>'
        return f'<rect x="{xx:.4f}" y="{yy:.4f}" width="{ss:.4f}" height="{ss:.4f}" fill="{html.escape(fill)}"/>'

    return (
        p(shape, x, y, 7 * m, color, frame_radius)
        + p(shape, x + m, y + m, 5 * m, bg, inner_radius)
        + p(pupil_shape, x + 2 * m, y + 2 * m, 3 * m, pupil_color, pupil_radius)
    )


def data_uri(path: str | Path) -> str:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _module_accent(pattern: str, x: int, y: int, n: int) -> bool:
    if pattern == "checker":
        return (x + y) % 2 == 0
    if pattern == "diagonal":
        return (x - y) % 3 == 0
    if pattern == "rings":
        c = (n - 1) / 2.0
        d = math.hypot(x - c, y - c)
        return int(d / 2.25) % 2 == 0
    return False


def _safe_decoration_positions(px: float, qz_px: float, count: int, seed: int):
    import random
    rng = random.Random(seed)
    margin = max(2.0, qz_px * 0.18)
    outer0 = margin
    outer1 = px - margin
    inner0 = qz_px
    inner1 = px - qz_px
    positions = []
    sides = ["top", "right", "bottom", "left"]
    for i in range(count):
        side = sides[i % 4]
        if side in ("top", "bottom"):
            x = rng.uniform(outer0, outer1)
            y = rng.uniform(outer0, max(outer0 + 1, inner0 - margin)) if side == "top" else rng.uniform(min(inner1 + margin, outer1 - 1), outer1)
        else:
            y = rng.uniform(outer0, outer1)
            x = rng.uniform(outer0, max(outer0 + 1, inner0 - margin)) if side == "left" else rng.uniform(min(inner1 + margin, outer1 - 1), outer1)
        positions.append((x, y))
    return positions


def _sparkle_svg(cx: float, cy: float, size: float, color: str) -> str:
    r = size / 2.0
    d = (
        f"M {cx:.4f},{cy-r:.4f} "
        f"C {cx+r*0.16:.4f},{cy-r*0.16:.4f} {cx+r*0.16:.4f},{cy-r*0.16:.4f} {cx+r:.4f},{cy:.4f} "
        f"C {cx+r*0.16:.4f},{cy+r*0.16:.4f} {cx+r*0.16:.4f},{cy+r*0.16:.4f} {cx:.4f},{cy+r:.4f} "
        f"C {cx-r*0.16:.4f},{cy+r*0.16:.4f} {cx-r*0.16:.4f},{cy+r*0.16:.4f} {cx-r:.4f},{cy:.4f} "
        f"C {cx-r*0.16:.4f},{cy-r*0.16:.4f} {cx-r*0.16:.4f},{cy-r*0.16:.4f} {cx:.4f},{cy-r:.4f} Z"
    )
    return f'<path d="{d}" fill="{html.escape(color)}"/>'


def _accent_style(mods: dict[str, Any], x: int, y: int) -> dict[str, Any] | None:
    acc = mods.get("accents", {})
    if not acc.get("enabled") or acc.get("probability", 0.0) <= 0:
        return None
    if not _selected(acc["probability"], acc["seed"], x, y, 8):
        return None
    colors = acc.get("colors") or [mods.get("color", "#111111")]
    shapes = acc.get("shapes") or [mods.get("shape", "circle")]
    ci = int(_selection_value(acc["seed"], x, y, 9) * len(colors)) % len(colors)
    si = int(_selection_value(acc["seed"], x, y, 10) * len(shapes)) % len(shapes)
    return {"color": colors[ci], "shape": shapes[si], "scale": acc.get("scale")}


def _module_style(mods: dict[str, Any], x: int, y: int, n: int, qz: int, m: float, px: float) -> dict[str, Any]:
    accent = _module_accent(mods.get("pattern", "uniform"), x, y, n)
    shape = mods.get("secondary_shape") if accent and mods.get("secondary_shape") else mods["shape"]
    color = mods.get("secondary_color") if accent and mods.get("secondary_color") else mods["color"]
    cx = (x + qz) * m + m / 2.0
    cy = (y + qz) * m + m / 2.0
    if mods.get("gradient", {}).get("enabled"):
        if mods["gradient"].get("mode") == "continuous":
            color = "url(#qr-module-gradient)"
        else:
            color = _gradient_color(mods["gradient"], cx, cy, px)
    accent_override = _accent_style(mods, x, y)
    accent_selected = accent_override is not None
    scale_override = None
    if accent_override:
        shape = accent_override["shape"]
        color = accent_override["color"]
        scale_override = accent_override.get("scale")
    return {
        "accent": accent or accent_selected,
        "accent_selected": accent_selected,
        "shape": shape,
        "color": color,
        "scale": scale_override,
        "key": (shape, accent or accent_selected, color if accent_selected else None),
    }


def _mid_color(a: str, b: str) -> str:
    if a == b:
        return a
    if a.startswith("url("):
        return a
    if b.startswith("url("):
        return b
    return _lerp_color(a, b, 0.5)


def _selection_value(seed: int, x: int, y: int, axis: int, salt: int = 0) -> float:
    # Deterministic integer hash -> [0, 1). Stable across Python runs.
    v = (seed * 0x45D9F3B + x * 0x27D4EB2D + y * 0x165667B1 + axis * 0x9E3779B1 + salt * 0x85EBCA6B) & 0xFFFFFFFF
    v ^= (v >> 16)
    v = (v * 0x7FEB352D) & 0xFFFFFFFF
    v ^= (v >> 15)
    v = (v * 0x846CA68B) & 0xFFFFFFFF
    v ^= (v >> 16)
    return v / 4294967296.0


def _selected(probability: float, seed: int, x: int, y: int, axis: int, salt: int = 0) -> bool:
    if probability >= 1.0:
        return True
    if probability <= 0.0:
        return False
    return _selection_value(seed, x, y, axis, salt) < probability


def _find_linear_runs(data_cells: set[tuple[int, int]], style_cache: dict[tuple[int, int], dict[str, Any]], n: int, max_run: int, selection: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[tuple[int, int], int]]:
    runs: list[dict[str, Any]] = []
    run_map: dict[tuple[int, int], int] = {}

    def has_v_neighbors(xx: int, yy: int) -> bool:
        return (xx, yy - 1) in data_cells or (xx, yy + 1) in data_cells

    def has_h_neighbors(xx: int, yy: int) -> bool:
        return (xx - 1, yy) in data_cells or (xx + 1, yy) in data_cells

    # horizontal first
    for y in range(n):
        x = 0
        while x < n:
            if (x, y) not in data_cells or (x, y) in run_map:
                x += 1
                continue
            style_key = style_cache[(x, y)]["key"]
            x2 = x
            while (x2 + 1, y) in data_cells and style_cache[(x2 + 1, y)]["key"] == style_key and (x2 + 1, y) not in run_map:
                x2 += 1
            length = x2 - x + 1
            if length >= 2:
                start = x
                while start <= x2:
                    end = min(start + max_run - 1, x2)
                    if end - start + 1 >= 2 and all(not has_v_neighbors(cx, y) for cx in range(start, end + 1)) and _selected(selection["run_probability"], selection["seed"], start, y, 2, end-start):
                        run_id = len(runs)
                        cells = [(cx, y) for cx in range(start, end + 1)]
                        for cell in cells:
                            run_map[cell] = run_id
                        runs.append({"orientation": "h", "start": (start, y), "end": (end, y), "cells": cells, "style_key": style_key})
                    start = end + 1
            x = x2 + 1

    # vertical on remaining cells
    for x in range(n):
        y = 0
        while y < n:
            if (x, y) not in data_cells or (x, y) in run_map:
                y += 1
                continue
            style_key = style_cache[(x, y)]["key"]
            y2 = y
            while (x, y2 + 1) in data_cells and style_cache[(x, y2 + 1)]["key"] == style_key and (x, y2 + 1) not in run_map:
                y2 += 1
            length = y2 - y + 1
            if length >= 2:
                start = y
                while start <= y2:
                    end = min(start + max_run - 1, y2)
                    if end - start + 1 >= 2 and all(not has_h_neighbors(x, cy) for cy in range(start, end + 1)) and _selected(selection["run_probability"], selection["seed"], x, start, 3, end-start):
                        run_id = len(runs)
                        cells = [(x, cy) for cy in range(start, end + 1)]
                        for cell in cells:
                            run_map[cell] = run_id
                        runs.append({"orientation": "v", "start": (x, start), "end": (x, end), "cells": cells, "style_key": style_key})
                    start = end + 1
            y = y2 + 1
    return runs, run_map


def render_svg(spec: dict[str, Any], base_dir: str | Path | None = None) -> str:
    s = normalize_spec(spec)
    matrix = qr_matrix(s)
    n = len(matrix)
    qz = s["quiet_zone"]
    m = s["module_size"]
    px = (n + 2 * qz) * m

    bg = s["background"]["color"]
    mods = s["modules"]
    eye_color = s["eyes"]["color"] or mods["color"]
    pupil_color = s["eyes"]["pupil_color"] or eye_color
    finder = finder_cells(n)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{px}" height="{px}" viewBox="0 0 {px} {px}">',
    ]
    defs = []
    data_min = qz * m
    data_max = (qz + n) * m
    if mods.get("gradient", {}).get("enabled") and mods["gradient"].get("mode") == "continuous":
        defs.append(_svg_gradient_def('qr-module-gradient', mods["gradient"], px=px, data_min=data_min, data_max=data_max))
    if s["eyes"].get("gradient", {}).get("enabled"):
        if s["eyes"]["gradient"].get("mode") == "continuous":
            defs.append(_svg_gradient_def('qr-eye-gradient', s["eyes"]["gradient"], px=px, data_min=data_min, data_max=data_max))
        else:
            defs.append(_svg_gradient_def('qr-eye-gradient', s["eyes"]["gradient"]))
        eye_color = 'url(#qr-eye-gradient)'
    if defs:
        parts.append('<defs>' + ''.join(defs) + '</defs>')
    parts.append(f'<rect width="100%" height="100%" fill="{html.escape(bg)}"/>')

    deco = s.get("decorations", {})
    if deco.get("enabled"):
        qz_px = qz * m
        dots = deco.get("edge_dots", {})
        if dots.get("enabled") and dots.get("count", 0) > 0:
            parts.append('<g id="qr-decoration-edge-dots">')
            positions = _safe_decoration_positions(px, qz_px, dots["count"], deco.get("seed", 1))
            colors = dots.get("colors") or [mods["color"]]
            rr = dots.get("size_modules", 0.35) * m / 2.0
            for i, (dx, dy) in enumerate(positions):
                col = colors[i % len(colors)]
                parts.append(f'<circle cx="{dx:.4f}" cy="{dy:.4f}" r="{rr:.4f}" fill="{html.escape(col)}"/>')
            parts.append('</g>')
        sparks = deco.get("sparkles", {})
        if sparks.get("enabled") and sparks.get("count", 0) > 0:
            parts.append('<g id="qr-decoration-sparkles">')
            positions = _safe_decoration_positions(px, qz_px, sparks["count"], deco.get("seed", 1) + 911)
            colors = sparks.get("colors") or [mods["color"]]
            ss = sparks.get("size_modules", 0.55) * m
            for i, (sx, sy) in enumerate(positions):
                parts.append(_sparkle_svg(sx, sy, ss, colors[i % len(colors)]))
            parts.append('</g>')

    parts.append('<g id="qr-modules">')

    scale = mods["scale"]
    draw_size = m * scale
    inset = (m - draw_size) / 2.0

    clear_geom = _logo_clear_geometry(s, n, m, px)
    clear_mode = s.get("logo", {}).get("clear_intersection_mode", "any_overlap")

    data_cells = {(x, y) for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark and (x, y) not in finder}
    if clear_geom and s["logo"].get("remove_partial_modules", True):
        data_cells = {
            cell for cell in data_cells
            if not _module_cell_overlaps_clear(cell, qz, m, inset, draw_size, clear_geom, clear_mode)
        }
    style_cache = {cell: _module_style(mods, cell[0], cell[1], n, qz, m, px) for cell in data_cells}
    conn = mods.get("connectivity", {})
    conn_enabled = bool(conn.get("enabled"))
    runs: list[dict[str, Any]] = []
    run_map: dict[tuple[int, int], int] = {}
    if conn_enabled:
        runs, run_map = _find_linear_runs(data_cells, style_cache, n, conn["max_run"], conn["selection"])

    if conn_enabled and runs:
        parts.append('<g id="qr-module-runs">')
        for idx, run in enumerate(runs):
            cells = run["cells"]
            style = style_cache[cells[len(cells) // 2]]
            if run["orientation"] == "h":
                strength = conn["horizontal_merge"]
                h = draw_size * (0.52 + 0.48 * strength)
                y = (cells[0][1] + qz) * m + (m - h) / 2.0
                x0 = (cells[0][0] + qz) * m + inset
                x1 = (cells[-1][0] + qz) * m + inset + draw_size
                w = x1 - x0
                if clear_geom and s["logo"].get("remove_partial_modules", True) and _rect_intersects_clear(x0, y, w, h, clear_geom):
                    continue
                parts.append(_rounded_rect_svg(x0, y, w, h, style["color"], _run_radius(style["shape"], h, mods["radius"], strength)))
            else:
                strength = conn["vertical_merge"]
                w = draw_size * (0.52 + 0.48 * strength)
                x = (cells[0][0] + qz) * m + (m - w) / 2.0
                y0 = (cells[0][1] + qz) * m + inset
                y1 = (cells[-1][1] + qz) * m + inset + draw_size
                h = y1 - y0
                if clear_geom and s["logo"].get("remove_partial_modules", True) and _rect_intersects_clear(x, y0, w, h, clear_geom):
                    continue
                parts.append(_rounded_rect_svg(x, y0, w, h, style["color"], _run_radius(style["shape"], min(w, h), mods["radius"], strength)))
        parts.append('</g>')

    # connectors before single modules so singles visually sit on top.
    if conn_enabled:
        parts.append('<g id="qr-module-connectors">')
        bridge = conn.get("bridge", {})
        bridge_overlap = max((m - draw_size) * 0.45, draw_size * bridge.get("overlap", conn["connector_overlap"]))
        for (x, y) in sorted(data_cells):
            style = style_cache[(x, y)]
            # horizontal pair
            if (x + 1, y) in data_cells and (bridge.get("connect_mixed_styles", False) or style_cache[(x + 1, y)]["key"] == style["key"]):
                same_run = (x, y) in run_map and (x + 1, y) in run_map and run_map[(x, y)] == run_map[(x + 1, y)]
                if not same_run and conn["horizontal_merge"] > 0 and _selected(conn["selection"]["horizontal_probability"], conn["selection"]["seed"], x, y, 0):
                    left = (x + qz) * m + inset + draw_size - bridge_overlap
                    right = (x + 1 + qz) * m + inset + bridge_overlap
                    cy = (y + qz) * m + m / 2.0
                    thickness = draw_size * bridge.get("max_thickness", 0.62) * conn["horizontal_merge"]
                    thickness = max(0.75, thickness)
                    col = _mid_color(style["color"], style_cache[(x + 1, y)]["color"])
                    if clear_geom and s["logo"].get("remove_partial_bridges", True):
                        bx0 = min(left, right)
                        by0 = cy - thickness / 2.0
                        bw = abs(right - left)
                        bh = thickness
                        if _rect_intersects_clear(bx0, by0, bw, bh, clear_geom):
                            pass
                        else:
                            parts.append(
                                _bridge_svg(
                                    left,
                                    cy,
                                    right,
                                    cy,
                                    col,
                                    thickness,
                                    bridge.get("style", "waisted"),
                                    bridge.get("waist_ratio", 0.55),
                                    bridge.get("curve", 0.65),
                                )
                            )
                    else:
                        parts.append(
                            _bridge_svg(
                                left,
                                cy,
                                right,
                                cy,
                                col,
                                thickness,
                                bridge.get("style", "waisted"),
                                bridge.get("waist_ratio", 0.55),
                                bridge.get("curve", 0.65),
                            )
                        )
            # vertical pair
            if (x, y + 1) in data_cells and (bridge.get("connect_mixed_styles", False) or style_cache[(x, y + 1)]["key"] == style["key"]):
                same_run = (x, y) in run_map and (x, y + 1) in run_map and run_map[(x, y)] == run_map[(x, y + 1)]
                if not same_run and conn["vertical_merge"] > 0 and _selected(conn["selection"]["vertical_probability"], conn["selection"]["seed"], x, y, 1):
                    top = (y + qz) * m + inset + draw_size - bridge_overlap
                    bottom = (y + 1 + qz) * m + inset + bridge_overlap
                    cx = (x + qz) * m + m / 2.0
                    thickness = draw_size * bridge.get("max_thickness", 0.62) * conn["vertical_merge"]
                    thickness = max(0.75, thickness)
                    col = _mid_color(style["color"], style_cache[(x, y + 1)]["color"])
                    if clear_geom and s["logo"].get("remove_partial_bridges", True):
                        bx0 = cx - thickness / 2.0
                        by0 = min(top, bottom)
                        bw = thickness
                        bh = abs(bottom - top)
                        if _rect_intersects_clear(bx0, by0, bw, bh, clear_geom):
                            pass
                        else:
                            parts.append(
                                _bridge_svg(
                                    cx,
                                    top,
                                    cx,
                                    bottom,
                                    col,
                                    thickness,
                                    bridge.get("style", "waisted"),
                                    bridge.get("waist_ratio", 0.55),
                                    bridge.get("curve", 0.65),
                                )
                            )
                    else:
                        parts.append(
                            _bridge_svg(
                                cx,
                                top,
                                cx,
                                bottom,
                                col,
                                thickness,
                                bridge.get("style", "waisted"),
                                bridge.get("waist_ratio", 0.55),
                                bridge.get("curve", 0.65),
                            )
                        )

        if conn["corner_merge"] > 0 and conn.get("corner_fillers", {}).get("enabled", False):
            parts.append('<g id="qr-module-corners">')
            size = max(0.6, (m - draw_size) + draw_size * (0.10 + 0.32 * conn["corner_merge"]))
            rr = size * (0.24 + 0.26 * conn["corner_merge"])
            for y in range(n - 1):
                for x in range(n - 1):
                    block = {
                        "nw": (x, y),
                        "ne": (x + 1, y),
                        "sw": (x, y + 1),
                        "se": (x + 1, y + 1),
                    }
                    present = {k: cell for k, cell in block.items() if cell in data_cells}
                    if len(present) < 3:
                        continue
                    # Require all participating cells to share the same style key.
                    keys = {style_cache[cell]["key"] for cell in present.values()}
                    if len(keys) != 1:
                        continue
                    colors = [style_cache[cell]["color"] for cell in present.values()]
                    col = colors[len(colors) // 2]
                    cx = (x + 1 + qz) * m
                    cy = (y + 1 + qz) * m
                    parts.append(_rounded_rect_svg(cx - size / 2.0, cy - size / 2.0, size, size, col, rr))
            parts.append('</g>')

        parts.append('</g>')

    parts.append('<g id="qr-module-singles">')
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if not dark or (x, y) in finder:
                continue
            if (x, y) in run_map:
                continue
            if clear_geom and s["logo"].get("remove_partial_modules", True) and _module_cell_overlaps_clear((x, y), qz, m, inset, draw_size, clear_geom, clear_mode):
                continue
            style = style_cache[(x, y)]
            cell_draw_size = m * (style.get("scale") if style.get("scale") is not None else scale)
            cell_inset = (m - cell_draw_size) / 2.0
            parts.append(shape_svg(style["shape"], (x + qz) * m + cell_inset, (y + qz) * m + cell_inset, cell_draw_size, style["color"], mods["radius"]))
    parts.append('</g>')

    parts.append('</g><g id="qr-eyes">')
    eye_specs = [
        ("top_left", 0, 0),
        ("top_right", n - 7, 0),
        ("bottom_left", 0, n - 7),
    ]
    for eye_name, ox, oy in eye_specs:
        override = s["eyes"].get("instances", {}).get(eye_name, {})
        parts.append(
            eye_svg(
                s["eyes"]["frame_shape"],
                (ox + qz) * m,
                (oy + qz) * m,
                m,
                override.get("color", eye_color),
                bg,
                s["eyes"]["pupil_shape"],
                override.get("pupil_color", pupil_color),
                override.get("frame_radius", s["eyes"]["frame_radius"]),
                override.get("inner_radius", s["eyes"]["inner_radius"]),
                override.get("pupil_radius", s["eyes"]["pupil_radius"]),
                s["eyes"]["radius_mode"],
            )
        )
    parts.append("</g>")

    logo = s["logo"]
    if logo["enabled"] and logo["path"]:
        lp = Path(str(logo["path"]))
        if not lp.is_absolute() and base_dir:
            lp = Path(base_dir) / lp
        if not lp.exists():
            raise FileNotFoundError(f"logo.path not found: {lp}")
        metrics = _logo_layout_metrics(s, n, m, px) or {}
        logo_size = metrics.get("logo_size", n * m * logo["scale"])
        lx = metrics.get("lx", (px - logo_size) / 2.0)
        ly = metrics.get("ly", (px - logo_size) / 2.0)
        badge_pad = metrics.get("badge_pad", (logo["badge_padding_modules"] if logo.get("badge_enabled") else logo["padding_modules"]) * m)
        bs = metrics.get("bs", logo_size + 2 * badge_pad)
        bx = metrics.get("bx", lx - badge_pad)
        by = metrics.get("by", ly - badge_pad)
        halo = s.get("decorations", {}).get("logo_halo", {})
        if s.get("decorations", {}).get("enabled") and halo.get("enabled"):
            hpad = halo.get("padding_modules", 0.6) * m
            hs = logo_size + 2 * hpad
            parts.append(f'<circle id="logo-halo" cx="{px/2:.4f}" cy="{px/2:.4f}" r="{hs/2:.4f}" fill="{html.escape(halo.get("color") or mods["color"])}" opacity="{halo.get("opacity",0.14):.4f}"/>')
        if logo["knockout"] and clear_geom:
            parts.append(_clear_shape_svg(clear_geom, bg))
        if logo.get("badge_enabled"):
            sw = logo["badge_stroke_width_modules"] * m
            badge = shape_svg(
                logo["badge_shape"],
                bx,
                by,
                bs,
                logo.get("badge_color") or bg,
                _clamp(float(logo.get("badge_radius_modules", 1.0)) / max(bs / m, 1.0), 0.0, 0.5),
            )
            if sw > 0 and 'fill=' in badge:
                badge = badge.replace(
                    '/>',
                    f' stroke="{html.escape(logo.get("badge_stroke_color") or "#000000")}" stroke-width="{sw:.4f}"/>',
                )
            parts.append(badge)
        uri = data_uri(lp)
        parts.append(
            f'<image id="logo" x="{lx:.4f}" y="{ly:.4f}" width="{logo_size:.4f}" height="{logo_size:.4f}" preserveAspectRatio="xMidYMid meet" href="{uri}" xlink:href="{uri}"/>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def svg_to_png(svg_text: str, out_path: str | Path | None = None, scale: float = 1.0) -> bytes:
    import cairosvg

    data = cairosvg.svg2png(bytestring=svg_text.encode(), scale=scale)
    if out_path:
        Path(out_path).write_bytes(data)
    return data


def validate_png(png_bytes: bytes, expected: str) -> tuple[bool, str]:
    messages = []
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        value, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        if value == expected:
            return True, "decoded successfully (OpenCV)"
        messages.append(f"OpenCV decoded {value!r}" if value else "OpenCV could not decode")
    except Exception as e:
        messages.append(f"OpenCV unavailable: {e}")

    try:
        from io import BytesIO
        from PIL import Image
        from pyzbar.pyzbar import decode as zbar_decode

        results = zbar_decode(Image.open(BytesIO(png_bytes)))
        values = [item.data.decode("utf-8", errors="replace") for item in results]
        if expected in values:
            return True, "decoded successfully (pyzbar)"
        messages.append(f"pyzbar decoded {values!r}" if values else "pyzbar could not decode")
    except Exception as e:
        messages.append(f"pyzbar unavailable: {e}")

    return False, "; ".join(messages)


def render_outputs(spec: dict[str, Any], base_dir: str | Path | None = None, png_scale: float = 2.0) -> dict[str, Any]:
    s = normalize_spec(spec)
    svg = render_svg(s, base_dir)
    png = svg_to_png(svg, scale=png_scale)
    ok, msg = validate_png(png, str(s["value"])) if s["output"]["validate"] else (True, "validation disabled")
    return {
        "spec": s,
        "svg": svg,
        "png": png,
        "validation_ok": ok,
        "validation_message": msg,
    }
