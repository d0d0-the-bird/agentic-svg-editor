#!/usr/bin/env python3
"""Run a recipe YAML: an ordered list of repo tool steps with inline configs.

Each step names a tool and gives its config inline and/or extra CLI args. The
config is written to a temporary YAML next to the recipe and the tool runs with
the recipe directory as its working directory, so every relative path in a
recipe resolves against the recipe file, exactly as in a standalone tool config.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

TOOLS_DIR = Path(__file__).resolve().parents[1]
CONFIG = object()

# tool name -> (script relative to tools/, argv template; CONFIG marks the config path)
TOOLS = {
    "extract": ("asset_extractor/extract.py", ["batch", CONFIG]),
    "vectorize": ("vectorizer/batch.py", [CONFIG]),
    "compose": ("vectorizer/compose.py", [CONFIG]),
    "qr": ("qr_generator/generate.py", [CONFIG]),
    "caption": ("qr_generator/caption.py", []),
    "embed-images": ("svg_editor/embed_images.py", [CONFIG]),
    "svg-edit": ("svg_editor/edit.py", ["batch", CONFIG]),
    "export-pdf": ("svg_editor/export_pdf.py", []),
    "recipe": ("pipeline/run.py", []),
}


def load_recipe(path: Path) -> list[dict]:
    recipe = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SystemExit(f"{path}: recipe needs a non-empty 'steps' list")
    seen = set()
    for index, step in enumerate(steps):
        where = f"{path}: steps[{index}]"
        if not isinstance(step, dict) or not step.get("id") or not step.get("tool"):
            raise SystemExit(f"{where} needs 'id' and 'tool'")
        if step["id"] in seen:
            raise SystemExit(f"{where}: duplicate step id {step['id']!r}")
        seen.add(step["id"])
        if step["tool"] not in TOOLS:
            raise SystemExit(f"{where}: unknown tool {step['tool']!r}; known: {', '.join(TOOLS)}")
        takes_config = CONFIG in TOOLS[step["tool"]][1]
        if takes_config and not isinstance(step.get("config"), dict):
            raise SystemExit(f"{where}: tool {step['tool']!r} needs a 'config' mapping")
        if not takes_config and "config" in step:
            raise SystemExit(f"{where}: tool {step['tool']!r} takes 'args', not 'config'")
        if not isinstance(step.get("args", []), list):
            raise SystemExit(f"{where}: 'args' must be a list")
    return steps


def run_step(recipe_path: Path, step: dict) -> None:
    script, template = TOOLS[step["tool"]]
    base = recipe_path.parent
    config_path = base / f".{recipe_path.stem}.{step['id']}.yaml"
    argv = [sys.executable, str(TOOLS_DIR / script)]
    argv += [str(config_path) if part is CONFIG else part for part in template]
    argv += [str(arg) for arg in step.get("args", [])]
    print(f"\n=== {recipe_path.name} :: {step['id']} ({step['tool']})", flush=True)
    try:
        if CONFIG in template:
            config_path.write_text(yaml.safe_dump(step["config"], sort_keys=False, allow_unicode=True), encoding="utf-8")
        result = subprocess.run(argv, cwd=base)
    finally:
        config_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise SystemExit(f"{recipe_path}: step {step['id']!r} failed with exit code {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recipes", nargs="+", type=Path)
    parser.add_argument("--only", action="append", default=[], help="Run only this step id; repeatable.")
    parser.add_argument("--list", action="store_true", help="List step ids and tools without running.")
    args = parser.parse_args()

    for recipe_path in args.recipes:
        recipe_path = recipe_path.resolve()
        steps = load_recipe(recipe_path)
        unknown = set(args.only) - {step["id"] for step in steps}
        if unknown:
            raise SystemExit(f"{recipe_path}: no step(s) named {', '.join(sorted(unknown))}")
        for step in steps:
            if args.list:
                print(f"{recipe_path.name}: {step['id']} ({step['tool']})")
            elif not args.only or step["id"] in args.only:
                run_step(recipe_path, step)
    return 0


if __name__ == "__main__":
    sys.exit(main())
