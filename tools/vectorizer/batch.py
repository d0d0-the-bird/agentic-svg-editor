#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml


def resolve(base: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def main():
    parser = argparse.ArgumentParser(description="Batch vectorize assets described by YAML.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--only", action="append", default=[], help="Vectorize only this named asset; repeatable.")
    args = parser.parse_args()

    config_path = args.config.resolve()
    base = config_path.parent
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    defaults = cfg.get("defaults", {})
    output_root = resolve(base, cfg.get("output_dir", "vectorized"))
    output_root.mkdir(parents=True, exist_ok=True)

    vectorize_py = Path(__file__).with_name("vectorize.py")
    completed = 0
    only_set = set(args.only)
    known_names = {str(a.get("name")) for a in cfg.get("assets", []) if a.get("enabled", True) is not False}
    missing = only_set - known_names
    if missing:
        raise ValueError(f"Unknown asset name(s) in --only: {sorted(missing)}")

    for asset in cfg.get("assets", []):
        if asset.get("enabled", True) is False:
            continue

        name = str(asset["name"])
        if only_set and name not in only_set:
            continue
        input_path = resolve(base, asset["input"])
        outdir = output_root / name

        params = dict(defaults)
        params.update({k: v for k, v in asset.items() if k not in {"name", "input", "enabled", "notes"}})

        cmd = [sys.executable, str(vectorize_py), str(input_path), "--output-dir", str(outdir)]
        mapping = {
            "threshold": "--threshold",
            "line_error_px": "--line-threshold",
            "bezier_error_px": "--bezier-threshold",
            "line_threshold": "--line-threshold",
            "bezier_threshold": "--bezier-threshold",
            "min_contour_length": "--min-contour-length",
            "max_contours": "--max-contours",
            "blur_sigma": "--blur-sigma",
            "foreground_quantile": "--foreground-quantile",
            "membership_mode": "--membership-mode",
            "normal_sample_distance": "--normal-sample-distance",
            "fill": "--fill",
            "diagnostic_dpi": "--diagnostic-dpi",
            "line_angle_deadband_deg": "--line-angle-deadband-deg",
            "line_rotation_run": "--line-rotation-run",
            "line_rotation_total_deg": "--line-rotation-total-deg",
            "line_min_length": "--line-min-length",
            "line_min_points": "--line-min-points",
            "joint_refine": "--joint-refine",
            "joint_tangent_threshold_deg": "--joint-tangent-threshold-deg",
            "joint_max_trim_px": "--joint-max-trim-px",
            "joint_min_line_length": "--joint-min-line-length",
        }
        for key, flag in mapping.items():
            if key in params and params[key] is not None:
                cmd.extend([flag, str(params[key])])
        for contour_id in params.get("keep_contours", []) or []:
            cmd.extend(["--keep-contour", str(int(contour_id))])
        objects = params.get("objects") or {}
        if not isinstance(objects, dict):
            raise ValueError(f"Asset {name!r} objects must be a mapping of object names to contour ID lists")
        for object_name, contour_ids in objects.items():
            ids = ",".join(str(int(contour_id)) for contour_id in contour_ids)
            cmd.extend(["--object-contours", f"{object_name}={ids}"])

        print(f"\n=== {name} ===")
        print(" ".join(cmd))
        subprocess.run(cmd, check=True)
        completed += 1

    print(f"\nVectorized {completed} assets into {output_root}")


if __name__ == "__main__":
    main()
