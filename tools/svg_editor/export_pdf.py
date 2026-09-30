#!/usr/bin/env python3
"""Export one or more SVGs as a multi-page vector PDF, one page per SVG, via Inkscape."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import etree
from pypdf import PdfReader, PdfWriter

from inkscape import inkscape_executable
from inline_svg_images import inline


def find_inkscape(explicit: str | None) -> str:
    inkscape = explicit or inkscape_executable()
    if not shutil.which(inkscape):
        raise SystemExit("Inkscape not found; install it, set $INKSCAPE, or pass --inkscape PATH")
    return inkscape


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="SVG files, one page each, in page order")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output PDF path")
    parser.add_argument("--inkscape", help="Inkscape executable (default: $INKSCAPE, PATH, then the macOS app bundle)")
    parser.add_argument("--keep-text", action="store_true", help="keep text as text instead of converting it to paths")
    args = parser.parse_args()

    inkscape = find_inkscape(args.inkscape)
    writer = PdfWriter()
    with tempfile.TemporaryDirectory(prefix="export_pdf_") as tmp:
        for index, source in enumerate(args.inputs, 1):
            # Embedded data-URI SVG images become nested SVG so they stay vector in the PDF.
            tree = etree.parse(str(source))
            inline(tree.getroot())
            vector_source = Path(tmp) / f"page-{index}.svg"
            tree.write(str(vector_source), encoding="UTF-8", xml_declaration=True)

            page_pdf = Path(tmp) / f"page-{index}.pdf"
            cmd = [inkscape, str(vector_source), "--export-type=pdf", f"--export-filename={page_pdf}"]
            if not args.keep_text:
                cmd.append("--export-text-to-path")
            subprocess.run(cmd, check=True)

            reader = PdfReader(page_pdf)
            if len(reader.pages) != 1:
                raise SystemExit(f"Expected one page from {source}, got {len(reader.pages)}")
            writer.append(reader)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("wb") as handle:
            writer.write(handle)
        writer.close()

    reader = PdfReader(args.output)
    for number, (source, page) in enumerate(zip(args.inputs, reader.pages), 1):
        print(f"Page {number}: {float(page.mediabox.width):.2f} × {float(page.mediabox.height):.2f} pt  ({source})")
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
