#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import mimetypes
from pathlib import Path

import yaml
from lxml import etree


XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace SVG image elements with embedded files by semantic ID.")
    parser.add_argument("config", type=Path)
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    input_path = (config_path.parent / config["input"]).resolve()
    output_path = (config_path.parent / config["output"]).resolve()
    tree = etree.parse(str(input_path))
    root = tree.getroot()

    for image_id, image in (config.get("images") or {}).items():
        matches = root.xpath(f'.//*[@id="{image_id}"]')
        if len(matches) != 1:
            raise ValueError(f"Expected one SVG element with id {image_id!r}, found {len(matches)}")
        element = matches[0]
        if etree.QName(element).localname != "image":
            raise ValueError(f"Element {image_id!r} is not an SVG image")
        asset_path = (config_path.parent / image["path"]).resolve()
        mime = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        uri = f"data:{mime};base64,{base64.b64encode(asset_path.read_bytes()).decode('ascii')}"
        element.set("href", uri)
        element.set(XLINK_HREF, uri)
        for attribute in ("x", "y", "width", "height", "preserveAspectRatio"):
            if attribute in image:
                element.set(attribute, str(image[attribute]))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output_path), encoding="UTF-8", xml_declaration=True)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
