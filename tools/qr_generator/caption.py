#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import cairosvg


def display_handle(value: str) -> str:
    path = urlparse(value).path.rstrip("/")
    handle = unquote(path.rsplit("/", 1)[-1]) if path else value
    return handle if handle.startswith("@") else f"@{handle}"


def caption_svg(svg_text: str, caption: str, accent: str, secondary_accent: str) -> str:
    root_match = re.search(r"<svg\b[^>]*>", svg_text)
    if not root_match:
        raise ValueError("Input does not contain an SVG root element")

    width_match = re.search(r'\bwidth="([0-9.]+)"', root_match.group(0))
    height_match = re.search(r'\bheight="([0-9.]+)"', root_match.group(0))
    if not width_match or not height_match:
        raise ValueError("SVG root must have numeric width and height attributes")

    width = float(width_match.group(1))
    height = float(height_match.group(1))
    font_size = min(width * 0.042, (width * 0.82) / max(1.0, len(caption) * 0.58))
    font_size = max(14.0, font_size)
    footer_height = font_size * 3.4
    total_height = height + footer_height

    root = root_match.group(0)
    root = re.sub(r'\bheight="[0-9.]+"', f'height="{total_height:.4f}"', root, count=1)
    root = re.sub(
        r'\bviewBox="[^"]+"',
        f'viewBox="0 0 {width:.4f} {total_height:.4f}"',
        root,
        count=1,
    )
    svg_text = svg_text[: root_match.start()] + root + svg_text[root_match.end() :]

    footer = (
        f'<g id="qr-caption">'
        f'<rect x="0" y="{height:.4f}" width="{width:.4f}" height="{footer_height:.4f}" fill="#FFFFFF"/>'
        f'<text x="{width / 2.0:.4f}" y="{height + footer_height * 0.46:.4f}" '
        f'text-anchor="middle" dominant-baseline="middle" fill="#171B20" '
        f'font-family="Avenir Next,Helvetica Neue,Arial,sans-serif" font-size="{font_size:.4f}" font-weight="700" '
        f'letter-spacing="{font_size * 0.012:.4f}">{html.escape(caption)}</text>'
        f'<rect x="{width / 2.0 - font_size * 1.75:.4f}" y="{height + footer_height * 0.75:.4f}" '
        f'width="{font_size * 3.2:.4f}" height="{font_size * 0.18:.4f}" rx="{font_size * 0.09:.4f}" fill="{html.escape(accent)}"/>'
        f'<rect x="{width / 2.0 + font_size * 1.05:.4f}" y="{height + footer_height * 0.75:.4f}" '
        f'width="{font_size * 0.7:.4f}" height="{font_size * 0.18:.4f}" rx="{font_size * 0.09:.4f}" fill="{html.escape(secondary_accent)}"/>'
        f'</g>'
    )
    return svg_text.replace("</svg>", footer + "</svg>", 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Add a separate readable caption below a generated QR SVG.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--text", help="Caption text; defaults to the value in the sibling .qr.json file")
    parser.add_argument("--accent", default="#171B20")
    parser.add_argument("--secondary-accent", default="#777777")
    parser.add_argument("--png-scale", type=float, default=2.0)
    parser.add_argument("--no-png", action="store_true")
    args = parser.parse_args()

    caption = args.text
    if caption is None:
        metadata_path = args.input.with_suffix(".qr.json")
        caption = display_handle(str(json.loads(metadata_path.read_text(encoding="utf-8"))["value"]))

    output = args.output if args.output.suffix.lower() == ".svg" else Path(str(args.output) + ".svg")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = caption_svg(args.input.read_text(encoding="utf-8"), caption, args.accent, args.secondary_accent)
    output.write_text(result, encoding="utf-8")
    print(output)

    if not args.no_png:
        png = output.with_suffix(".png")
        cairosvg.svg2png(bytestring=result.encode("utf-8"), write_to=str(png), scale=args.png_scale)
        print(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
