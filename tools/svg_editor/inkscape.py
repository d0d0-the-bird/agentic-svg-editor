"""Locate the Inkscape CLI: $INKSCAPE, then PATH, then the macOS app bundle."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

MACOS_INKSCAPE = Path("/Applications/Inkscape.app/Contents/MacOS/inkscape")


def inkscape_executable() -> str:
    explicit = os.environ.get("INKSCAPE")
    if explicit:
        return explicit
    found = shutil.which("inkscape")
    if found:
        return found
    if MACOS_INKSCAPE.exists():
        return str(MACOS_INKSCAPE)
    return "inkscape"
