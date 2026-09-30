#!/usr/bin/env python3
"""Local viewer for recipe outputs.

Scans a recipe (following nested `recipe` steps) into a graph of units, steps,
inputs and outputs, and serves a browser UI that displays generated SVGs and
highlights where each input asset sits in them. Only files referenced by the
graph are served. It listens on 127.0.0.1 by default; with any other --host
(e.g. 0.0.0.0 for LAN/VPN access) every request must carry an access token.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from run import load_recipe

VIEWABLE = {".svg", ".png", ".jpg", ".jpeg", ".pdf"}
VIEWER_HTML = Path(__file__).with_name("viewer.html")
OUTPUT_FLAGS = {"-o", "--output"}
VALUE_FLAGS = OUTPUT_FLAGS | {"--text", "--accent", "--secondary-accent", "--png-scale", "--inkscape"}


def arg_value(args: list[str], flags: set[str]) -> str | None:
    for i, arg in enumerate(args[:-1]):
        if arg in flags:
            return args[i + 1]
    return None


def positional(args: list[str]) -> list[str]:
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in VALUE_FLAGS:
            skip = True
        elif not arg.startswith("-"):
            out.append(arg)
    return out


def with_suffix_added(path: Path, suffix: str) -> Path:
    return path if path.suffix.lower() == suffix else Path(str(path) + suffix)


def dir_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in VIEWABLE)


def step_io(step: dict, base: Path) -> tuple[list[dict], list[Path], Path | None]:
    """Return (inputs, outputs, primary output) for one step."""
    tool, cfg = step["tool"], step.get("config") or {}
    args = [str(a) for a in step.get("args", [])]
    inputs: list[dict] = []
    outputs: list[Path] = []
    primary = None

    def add_input(value, element=None, role="asset"):
        if value:
            inputs.append({"path": (base / value).resolve(), "element": element, "role": role})

    if tool == "extract":
        add_input(cfg.get("source"), role="source")
        outputs = dir_files(base / cfg.get("output_dir", "extracted"))
    elif tool == "vectorize":
        for asset in cfg.get("assets") or []:
            add_input(asset.get("input"))
        root = base / cfg.get("output_dir", "vectorized")
        outputs = [p for p in dir_files(root) if p.suffix == ".svg"] + [p for p in dir_files(root) if p.suffix != ".svg"]
    elif tool == "compose":
        for layer in cfg.get("layers") or []:
            add_input(layer.get("input"), element=layer.get("id"))
        primary = (base / cfg.get("output", "composed.svg")).resolve()
        outputs = [primary]
    elif tool == "embed-images":
        add_input(cfg.get("input"), role="base")
        for image_id, image in (cfg.get("images") or {}).items():
            add_input(image.get("path"), element=image_id)
        primary = (base / cfg["output"]).resolve()
        outputs = [primary]
    elif tool == "svg-edit":
        add_input(cfg.get("input"), role="base")
        primary = (base / cfg["output"]).resolve()
        outputs = [primary, primary.with_suffix(".preview.png")]
    elif tool == "qr":
        logo = cfg.get("logo") or {}
        if logo.get("enabled") and logo.get("path"):
            add_input(logo["path"], element="logo")
        for image in (cfg.get("decorations") or {}).get("images") or []:
            add_input(image.get("path"), element=image.get("id"))
        out = arg_value(args, OUTPUT_FLAGS)
        if out:
            primary = with_suffix_added((base / out).resolve(), ".svg")
            outputs = [primary, primary.with_suffix(".png")]
    elif tool == "caption":
        for value in positional(args)[:1]:
            add_input(value, role="base")
        out = arg_value(args, OUTPUT_FLAGS)
        if out:
            primary = with_suffix_added((base / out).resolve(), ".svg")
            outputs = [primary, primary.with_suffix(".png")]
    elif tool == "export-pdf":
        for value in positional(args):
            add_input(value, role="page")
        out = arg_value(args, OUTPUT_FLAGS)
        if out:
            primary = (base / out).resolve()
            outputs = [primary]
    return inputs, [p.resolve() for p in outputs], primary


def scan(recipe_path: Path) -> dict:
    units: dict[Path, dict] = {}

    def visit(path: Path) -> Path:
        path = path.resolve()
        if path in units:
            return path
        recipe = load_recipe_meta(path)
        unit = {"path": path, "description": recipe.get("description"), "notes": recipe.get("notes") or [],
                "steps": [], "children": []}
        units[path] = unit
        for step in load_recipe(path):
            entry = {"id": step["id"], "tool": step["tool"], "children": []}
            if step["tool"] == "recipe":
                for child in step.get("args", []):
                    child_path = visit(path.parent / child)
                    entry["children"].append(child_path)
                    unit["children"].append(child_path)
                entry["inputs"], entry["outputs"], entry["primary"] = [], [], None
            else:
                entry["inputs"], entry["outputs"], entry["primary"] = step_io(step, path.parent)
            unit["steps"].append(entry)
        return path

    roots = [visit(recipe_path)]
    # Follow inputs produced by other recipes, by the `<unit>/generated/` -> `<unit>/<unit>.yaml` convention.
    while True:
        produced = {o for u in units.values() for s in u["steps"] for o in s["outputs"]}
        found = set()
        for unit in list(units.values()):
            for step in unit["steps"]:
                for inp in step["inputs"]:
                    if inp["path"] in produced or "generated" not in inp["path"].parts:
                        continue
                    parts = inp["path"].parts
                    unit_dir = Path(*parts[:len(parts) - parts[::-1].index("generated") - 1])
                    candidate = (unit_dir / f"{unit_dir.name}.yaml").resolve()
                    if candidate.is_file() and candidate not in units:
                        found.add(candidate)
        if not found:
            break
        for candidate in sorted(found):
            roots.append(visit(candidate))

    files: set[Path] = set(units)
    for unit in units.values():
        for step in unit["steps"]:
            files.update(i["path"] for i in step["inputs"])
            files.update(step["outputs"])
    common = Path(os.path.commonpath([str(p.parent) for p in files]))

    def key(path: Path) -> str:
        return path.relative_to(common).as_posix()

    producers, consumers = {}, {}
    for unit_path, unit in units.items():
        for step in unit["steps"]:
            for out in step["outputs"]:
                producers[out] = {"unit": key(unit_path), "step": step["id"]}
            for inp in step["inputs"]:
                consumers.setdefault(inp["path"], []).append({"unit": key(unit_path), "step": step["id"]})

    def file_info(path: Path) -> dict:
        exists = path.is_file()
        return {"exists": exists, "size": path.stat().st_size if exists else 0,
                "mtime": path.stat().st_mtime if exists else 0, "ext": path.suffix.lower(),
                "producer": producers.get(path), "consumers": consumers.get(path, [])}

    graph = {
        "root": key(roots[0]),
        "roots": [key(r) for r in roots],
        "base": str(common),
        "units": {},
        "files": {key(p): file_info(p) for p in sorted(files)},
    }
    for unit_path, unit in units.items():
        graph["units"][key(unit_path)] = {
            "key": key(unit_path),
            "name": unit_path.stem,
            "description": unit["description"],
            "notes": unit["notes"],
            "children": [key(c) for c in unit["children"]],
            "steps": [{
                "id": s["id"], "tool": s["tool"],
                "children": [key(c) for c in s["children"]],
                "inputs": [{"file": key(i["path"]), "element": i["element"], "role": i["role"]} for i in s["inputs"]],
                "outputs": [key(o) for o in s["outputs"]],
                "primary": key(s["primary"]) if s["primary"] else None,
            } for s in unit["steps"]],
        }
    return {"graph": graph, "allowed": {key(p): p for p in files}}


def load_recipe_meta(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


COOKIE = "pv_token"


def make_handler(recipe_path: Path, token: str | None):
    state = {"allowed": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status: int, body: bytes, content_type: str, cookie: str | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", f"{COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self, query: dict) -> tuple[bool, str | None]:
            """Return (allowed, cookie to set). The token may come from ?token= or the cookie."""
            if token is None:
                return True, None
            supplied = (query.get("token") or [""])[0]
            if supplied and hmac.compare_digest(supplied, token):
                return True, token
            match = re.search(rf"(?:^|;\s*){COOKIE}=([^;]+)", self.headers.get("Cookie", ""))
            return bool(match and hmac.compare_digest(match.group(1), token)), None

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            ok, set_cookie = self.authorized(parse_qs(url.query))
            if not ok:
                self.send(403, b"Access token required: open the URL printed by view.py.", "text/plain")
                return
            if path == "/":
                self.send(200, VIEWER_HTML.read_bytes(), "text/html; charset=utf-8", cookie=set_cookie)
            elif path == "/api/graph":
                try:
                    result = scan(recipe_path)
                except (SystemExit, Exception) as exc:  # recipe errors are reported, not fatal
                    self.send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
                    return
                state["allowed"] = result["allowed"]
                self.send(200, json.dumps(result["graph"]).encode(), "application/json")
            elif path.startswith("/file/"):
                if not state["allowed"]:
                    state["allowed"] = scan(recipe_path)["allowed"]
                target = state["allowed"].get(unquote(path[len("/file/"):]))
                if target is None or not target.is_file():
                    self.send(404, b"not found", "text/plain")
                    return
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.send(200, target.read_bytes(), ctype)
            else:
                self.send(404, b"not found", "text/plain")

    return Handler


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def local_addresses() -> list[str]:
    """IPv4 addresses of this machine's interfaces (LAN, VPN), excluding loopback."""
    found = set()
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        found.update(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; picks the default-route address
            found.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if not ipaddress.ip_address(a).is_loopback)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to listen on; 0.0.0.0 allows other devices on the LAN/VPN (token required).")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token", help="Access token for non-local hosts (default: random per run).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window.")
    args = parser.parse_args()

    recipe = args.recipe.resolve()
    scan(recipe)  # fail fast on a broken recipe
    token = None if is_loopback(args.host) else (args.token or secrets.token_urlsafe(16))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(recipe, token))
    suffix = f"?token={token}" if token else ""
    local_url = f"http://127.0.0.1:{args.port}/{suffix}"
    print(f"Viewing {recipe}", flush=True)
    if token:
        hosts = local_addresses() if args.host in ("0.0.0.0", "") else [args.host]
        print("Reachable from other devices at:")
        for host in hosts:
            print(f"  http://{host}:{args.port}/{suffix}")
        print("Anyone with this URL can view the recipe's files while the server runs.")
    print(f"Local: {local_url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        webbrowser.open(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
