#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose vectorized SVG layers into one self-contained SVG.")
    parser.add_argument("config", type=Path)
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    width = float(config["width"])
    height = float(config["height"])
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{width:g}",
        "height": f"{height:g}",
        "viewBox": f"0 0 {width:g} {height:g}",
    })
    gradients = config.get("gradients") or {}
    if gradients:
        defs = ET.SubElement(root, f"{{{SVG_NS}}}defs")
        for gradient_id, gradient in gradients.items():
            kind = str(gradient.get("type", "linear"))
            if kind != "linear":
                raise ValueError(f"Gradient {gradient_id!r} has unsupported type {kind!r}")
            node = ET.SubElement(defs, f"{{{SVG_NS}}}linearGradient", {
                "id": str(gradient_id),
                "gradientUnits": "userSpaceOnUse",
                "x1": str(gradient.get("x1", 0)),
                "y1": str(gradient.get("y1", 0)),
                "x2": str(gradient.get("x2", width)),
                "y2": str(gradient.get("y2", 0)),
            })
            stops = gradient.get("stops") or []
            if len(stops) < 2:
                raise ValueError(f"Gradient {gradient_id!r} requires at least two stops")
            for stop in stops:
                ET.SubElement(node, f"{{{SVG_NS}}}stop", {
                    "offset": str(stop["offset"]),
                    "stop-color": str(stop["color"]),
                })
    background = config.get("background")
    if background:
        ET.SubElement(root, f"{{{SVG_NS}}}rect", {
            "width": f"{width:g}", "height": f"{height:g}", "fill": str(background)
        })

    for layer in config.get("layers", []):
        source_path = (config_path.parent / layer["input"]).resolve()
        source_root = ET.parse(source_path).getroot()
        x = float(layer.get("x", 0))
        y = float(layer.get("y", 0))
        scale = float(layer.get("scale", 1.0))
        group = ET.SubElement(root, f"{{{SVG_NS}}}g", {
            "id": str(layer.get("id", source_path.stem)),
            "transform": f"translate({x:g} {y:g}) scale({scale:g})",
        })
        fill = layer.get("fill")
        include_ids = set(map(str, layer.get("include_ids") or []))
        exclude_ids = set(map(str, layer.get("exclude_ids") or []))
        available_ids = {child.get("id") for child in source_root if child.get("id")}
        unknown_ids = (include_ids | exclude_ids) - available_ids
        if unknown_ids:
            raise ValueError(f"Layer {layer.get('id')!r} references unknown object IDs: {sorted(unknown_ids)}")
        for child in source_root:
            child_id = child.get("id")
            is_defs = child.tag == f"{{{SVG_NS}}}defs"
            if include_ids and child_id not in include_ids and not is_defs:
                continue
            if child_id in exclude_ids:
                continue
            node = copy.deepcopy(child)
            if fill is not None and node.get("fill") not in {None, "none"}:
                node.set("fill", str(fill))
            group.append(node)

    output = (config_path.parent / config.get("output", "composed.svg")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="unicode", xml_declaration=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
