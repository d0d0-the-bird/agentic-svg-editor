#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import itertools
import json
from pathlib import Path
from typing import Any

import yaml

from renderer import MODULE_SHAPES, normalize_spec, render_outputs

PRIMARY_SHAPE_WEIGHTS = {
    "square": 0.0,
    "rounded": 0.4,
    "circle": 0.7,
    "diamond": 0.9,
    "squircle": 1.0,
    "soft_blob": 1.3,
    "petal": 1.7,
    "star": 1.9,
    "capsule_h": 1.2,
    "capsule_v": 1.2,
}
PATTERN_WEIGHTS = {"uniform": 0.0, "checker": 0.45, "diagonal": 0.70, "rings": 0.90}

DEFAULT_AUTOTUNE = {
    "primary_shapes": ["star", "petal", "soft_blob", "rounded"],
    "secondary_shapes": ["circle", "diamond", None],
    "patterns": ["rings", "diagonal", "checker"],
    "module_scales": [0.96, 0.94, 0.92, 0.90],
    "radii": [0.34, 0.30],
    "gradient_enabled": [True, False],
    "connectivity_enabled": [True, False],
    "horizontal_merges": [0.90, 0.78],
    "vertical_merges": [0.82, 0.68],
    "corner_merges": [0.42, 0.26],
    "max_runs": [4, 3],
    "logo_scales": [0.12, 0.10, 0.08, 0.06],
    "padding_modules": [0.10, 0.15, 0.20, 0.30],
    "badge_enabled": [True, False],
    "badge_padding_modules": [0.25, 0.35, 0.50],
    "badge_stroke_width_modules": [0.04, 0.08],
    "max_candidates": 140,
}


def deep_update(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


def load_spec(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
    return data


def extract_autotune(spec: dict[str, Any]) -> dict[str, Any]:
    at = copy.deepcopy(DEFAULT_AUTOTUNE)
    custom = spec.get("autotune") or {}
    deep_update(at, custom)
    at["primary_shapes"] = [s for s in at["primary_shapes"] if s in MODULE_SHAPES]
    at["secondary_shapes"] = [s for s in at["secondary_shapes"] if s is None or s in MODULE_SHAPES]
    return at


def candidate_specs(base_spec: dict[str, Any], at: dict[str, Any]) -> list[dict[str, Any]]:
    base = normalize_spec({k: v for k, v in base_spec.items() if k != "autotune"})
    has_logo = bool(base.get("logo", {}).get("enabled") and base.get("logo", {}).get("path"))
    combos = []
    for primary, secondary, pattern, scale, radius, grad_enabled, conn_enabled, h_merge, v_merge, c_merge, max_run in itertools.product(
        at["primary_shapes"],
        at["secondary_shapes"],
        at["patterns"],
        at["module_scales"],
        at["radii"],
        at["gradient_enabled"],
        at["connectivity_enabled"],
        at["horizontal_merges"],
        at["vertical_merges"],
        at["corner_merges"],
        at["max_runs"],
    ):
        sec = None if primary == secondary or pattern == "uniform" else secondary
        logo_scales = at["logo_scales"] if has_logo else [base["logo"]["scale"]]
        padding_values = at["padding_modules"] if has_logo else [base["logo"]["padding_modules"]]
        badge_enabled_values = at["badge_enabled"] if has_logo else [base["logo"]["badge_enabled"]]
        badge_padding_values = at["badge_padding_modules"] if has_logo else [base["logo"]["badge_padding_modules"]]
        stroke_values = at["badge_stroke_width_modules"] if has_logo else [base["logo"]["badge_stroke_width_modules"]]
        for logo_scale, pad, badge_enabled, badge_pad, stroke_w in itertools.product(
            logo_scales, padding_values, badge_enabled_values, badge_padding_values, stroke_values
        ):
            spec = copy.deepcopy(base)
            spec["modules"]["shape"] = primary
            spec["modules"]["secondary_shape"] = sec
            spec["modules"]["pattern"] = pattern
            spec["modules"]["scale"] = float(scale)
            spec["modules"]["radius"] = float(radius)
            spec["modules"]["gradient"]["enabled"] = bool(grad_enabled)
            spec["modules"]["connectivity"]["enabled"] = bool(conn_enabled)
            spec["modules"]["connectivity"]["horizontal_merge"] = float(h_merge)
            spec["modules"]["connectivity"]["vertical_merge"] = float(v_merge)
            spec["modules"]["connectivity"]["corner_merge"] = float(c_merge)
            spec["modules"]["connectivity"]["max_run"] = int(max_run)
            if has_logo:
                spec["logo"]["scale"] = float(logo_scale)
                spec["logo"]["padding_modules"] = float(pad)
                spec["logo"]["badge_enabled"] = bool(badge_enabled)
                spec["logo"]["badge_padding_modules"] = float(badge_pad)
                spec["logo"]["badge_stroke_width_modules"] = float(stroke_w)
            combos.append(spec)
    return combos


def playfulness_score(spec: dict[str, Any]) -> float:
    mods = spec["modules"]
    logo = spec["logo"]
    conn = mods.get("connectivity", {})
    score = 0.0
    score += PRIMARY_SHAPE_WEIGHTS.get(mods["shape"], 0.0)
    score += 0.55 * PRIMARY_SHAPE_WEIGHTS.get(mods.get("secondary_shape") or "square", 0.0)
    score += PATTERN_WEIGHTS.get(mods.get("pattern", "uniform"), 0.0)
    score += (mods.get("scale", 0.86) - 0.80) * 4.0
    score += mods.get("radius", 0.30) * 1.2
    if mods.get("gradient", {}).get("enabled"):
        score += 0.75
    if conn.get("enabled"):
        score += 1.2
        score += conn.get("horizontal_merge", 0.0) * 0.55
        score += conn.get("vertical_merge", 0.0) * 0.40
        score += conn.get("corner_merge", 0.0) * 0.45
        score += min(conn.get("max_run", 0), 5) * 0.06
    if logo.get("enabled") and logo.get("path"):
        score += 0.7 + logo.get("scale", 0.0) * 8.0
        score -= logo.get("padding_modules", 0.0) * 1.6
        if logo.get("badge_enabled"):
            score += 0.35
            score -= logo.get("badge_padding_modules", 0.0) * 0.8
            score += logo.get("badge_stroke_width_modules", 0.0) * 1.5
    return score


def tune(base_spec: dict[str, Any], base_dir: Path, png_scale: float) -> dict[str, Any]:
    at = extract_autotune(base_spec)
    candidates = candidate_specs(base_spec, at)
    scored = sorted(candidates, key=playfulness_score, reverse=True)
    max_candidates = int(at.get("max_candidates", 300))
    attempts = []
    best_valid = None
    best_valid_render = None

    for idx, spec in enumerate(scored[:max_candidates], start=1):
        result = render_outputs(spec, base_dir, png_scale)
        entry = {
            "rank": idx,
            "score": round(playfulness_score(spec), 4),
            "validation_ok": result["validation_ok"],
            "validation_message": result["validation_message"],
            "modules": {
                "shape": result["spec"]["modules"]["shape"],
                "secondary_shape": result["spec"]["modules"]["secondary_shape"],
                "pattern": result["spec"]["modules"]["pattern"],
                "scale": result["spec"]["modules"]["scale"],
                "radius": result["spec"]["modules"]["radius"],
                "gradient_enabled": result["spec"]["modules"]["gradient"]["enabled"],
                "connectivity": result["spec"]["modules"]["connectivity"],
            },
            "logo": {
                "scale": result["spec"]["logo"]["scale"],
                "padding_modules": result["spec"]["logo"]["padding_modules"],
                "badge_enabled": result["spec"]["logo"]["badge_enabled"],
                "badge_padding_modules": result["spec"]["logo"]["badge_padding_modules"],
                "badge_stroke_width_modules": result["spec"]["logo"]["badge_stroke_width_modules"],
            },
        }
        attempts.append(entry)
        if result["validation_ok"]:
            best_valid = result["spec"]
            best_valid_render = result
            break

    return {
        "best_spec": best_valid,
        "best_render": best_valid_render,
        "attempts": attempts,
        "searched_candidates": min(len(scored), max_candidates),
        "autotune": at,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-tune QR styling to the most playful valid design.")
    ap.add_argument("config", type=Path, help="Input YAML/JSON config")
    ap.add_argument("-o", "--output", type=Path, help="Output stem or SVG path")
    ap.add_argument("--png-scale", type=float, default=2.0)
    ap.add_argument("--allow-unreadable", action="store_true", help="Write best attempt even if unreadable")
    args = ap.parse_args()

    raw_spec = load_spec(args.config)
    tuned = tune(raw_spec, args.config.parent, args.png_scale)
    out = args.output or args.config.with_name(args.config.stem + ".autotuned")
    svg_path = out if out.suffix.lower() == ".svg" else Path(str(out) + ".svg")
    stem = svg_path.with_suffix("")
    png_path = Path(str(stem) + ".png")
    yaml_path = Path(str(stem) + ".yaml")
    json_path = Path(str(stem) + ".autotune.json")
    svg_path.parent.mkdir(parents=True, exist_ok=True)

    if tuned["best_render"] is None:
        if not args.allow_unreadable:
            json_path.write_text(json.dumps(tuned, indent=2), encoding="utf-8")
            print(json_path)
            print("validation: FAIL - no valid candidate found")
            return 2
        render = render_outputs(raw_spec, args.config.parent, args.png_scale)
        best_spec = render["spec"]
        best_render = render
    else:
        best_spec = tuned["best_spec"]
        best_render = tuned["best_render"]

    svg_path.write_text(best_render["svg"], encoding="utf-8")
    png_path.write_bytes(best_render["png"])
    yaml_path.write_text(yaml.safe_dump(best_spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
    report = {
        "validation_ok": best_render["validation_ok"],
        "validation_message": best_render["validation_message"],
        "output_svg": str(svg_path),
        "output_png": str(png_path),
        "output_yaml": str(yaml_path),
        "best_score": round(playfulness_score(best_spec), 4),
        "best_spec": best_spec,
        "searched_candidates": tuned["searched_candidates"],
        "attempts": tuned["attempts"],
        "autotune": tuned["autotune"],
    }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(svg_path)
    print(png_path)
    print(yaml_path)
    print(json_path)
    print(f"validation: {'OK' if best_render['validation_ok'] else 'FAIL'} - {best_render['validation_message']}")
    return 0 if best_render["validation_ok"] or args.allow_unreadable else 2


if __name__ == "__main__":
    raise SystemExit(main())
