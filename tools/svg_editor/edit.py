#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
NS = {"svg": SVG_NS, "xlink": XLINK_NS}
RENDERABLE = {
    "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "image", "use"
}


def local_name(el) -> str:
    if not isinstance(el.tag, str):
        return ""
    return etree.QName(el).localname


def parse_svg(path: Path):
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False, recover=False)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    if local_name(root) != "svg":
        raise ValueError(f"{path} does not contain an SVG root element")
    return tree, root


def write_svg(tree, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output), encoding="utf-8", xml_declaration=True, pretty_print=False)


def render_preview(svg_path: Path, png_path: Path, max_size: int = 1800):
    """Render SVG with Inkscape, our canonical SVG renderer."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "inkscape", str(svg_path),
        "--export-type=png",
        f"--export-filename={png_path}",
        f"--export-width={int(max_size)}",
        "--export-area-page",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def apply_text_specs(root, specs):
    """Apply declarative text values/styles before export.

    The YAML text section is the semantic source of truth. The final SVG may
    subsequently convert these live text objects to paths.
    """
    results = []
    for index, raw in enumerate(specs or [], 1):
        if not isinstance(raw, dict):
            raise ValueError(f"text entry #{index} must be a mapping")
        eid = str(raw.get("id", "")).strip()
        if not eid:
            raise ValueError(f"text entry #{index} requires id")
        el = find_id(root, eid)
        if local_name(el) not in {"text", "tspan"}:
            raise ValueError(f"text entry #{index} target #{eid} is <{local_name(el)}>, expected text/tspan")
        if "value" in raw:
            # Keep child tspans only when explicitly requested; otherwise the YAML
            # value replaces the whole text object's textual contents.
            for child in list(el):
                el.remove(child)
            el.text = str(raw.get("value", ""))
        attr_map = dict(raw.get("attributes") or {})
        aliases = {
            "font_family": "font-family",
            "font_size": "font-size",
            "font_weight": "font-weight",
            "font_style": "font-style",
            "text_anchor": "text-anchor",
            "fill": "fill",
        }
        for src, dst in aliases.items():
            if src in raw:
                attr_map[dst] = raw[src]
        for key, value in attr_map.items():
            el.set(str(key), str(value))
        results.append({"id": eid, "value": raw.get("value"), "attributes": attr_map})
    return results


def export_svg(tree, output_path: Path, text_to_path: bool = False):
    """Write the single requested SVG output, optionally pathifying all text."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not text_to_path:
        write_svg(tree, output_path)
        return
    with tempfile.TemporaryDirectory(prefix="svg-editor-export-") as tmpdir:
        intermediate = Path(tmpdir) / "live-text.svg"
        write_svg(tree, intermediate)
        cmd = [
            "inkscape", str(intermediate),
            "--export-type=svg",
            "--export-text-to-path",
            f"--export-filename={output_path}",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def all_ids(root):
    return {el.get("id") for el in root.iter() if isinstance(el.tag, str) and el.get("id")}


def find_id(root, element_id: str):
    matches = root.xpath('//*[@id=$wanted]', wanted=element_id)
    if not matches:
        raise KeyError(f"SVG id not found: #{element_id}")
    if len(matches) > 1:
        raise ValueError(f"Duplicate SVG id found: #{element_id} ({len(matches)} matches)")
    return matches[0]


def prepend_transform(el, transform: str):
    old = (el.get("transform") or "").strip()
    el.set("transform", f"{transform} {old}".strip())


def replace_references(root, old_id: str, new_id: str, scope=None):
    """Update common href/url(#id) references after an id rename."""
    target_root = scope if scope is not None else root
    href_attrs = {"href", f"{{{XLINK_NS}}}href"}
    for el in target_root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr, value in list(el.attrib.items()):
            if attr in href_attrs and value == f"#{old_id}":
                el.set(attr, f"#{new_id}")
            elif isinstance(value, str) and f"url(#{old_id})" in value:
                el.set(attr, value.replace(f"url(#{old_id})", f"url(#{new_id})"))


def ensure_ids(root, prefix="svg", include_tspans=True):
    """Assign deterministic readable IDs to renderable elements that lack one.

    IDs are assigned once and should then be committed to source control. Descendant
    elements use the nearest parent id as a prefix when practical.
    """
    existing = all_ids(root)
    counters: dict[tuple[str, str], int] = {}
    assigned = []

    allowed = set(RENDERABLE)
    if not include_tspans:
        allowed.discard("tspan")

    for el in root.iter():
        tag = local_name(el)
        if tag not in allowed or el.get("id"):
            continue
        parent = el.getparent()
        parent_id = parent.get("id") if parent is not None and isinstance(parent.tag, str) else None
        base = parent_id or prefix
        key = (base, tag)
        n = counters.get(key, 0) + 1
        while True:
            candidate = f"{base}__{tag}-{n:03d}"
            if candidate not in existing:
                break
            n += 1
        counters[key] = n
        el.set("id", candidate)
        existing.add(candidate)
        assigned.append({"id": candidate, "tag": tag})
    return assigned


def inspect_svg(root):
    ids = []
    missing = []
    duplicate_map: dict[str, list[str]] = {}
    for el in root.iter():
        tag = local_name(el)
        if not tag:
            continue
        eid = el.get("id")
        if eid:
            ids.append({"id": eid, "tag": tag})
            duplicate_map.setdefault(eid, []).append(tag)
        elif tag in RENDERABLE:
            missing.append({"tag": tag})
    duplicates = [
        {"id": eid, "count": len(tags), "tags": tags}
        for eid, tags in duplicate_map.items() if len(tags) > 1
    ]
    by_tag = {}
    for item in missing:
        by_tag[item["tag"]] = by_tag.get(item["tag"], 0) + 1
    return {
        "id_count": len(ids),
        "ids": ids,
        "missing_id_count": len(missing),
        "missing_ids_by_tag": by_tag,
        "duplicates": duplicates,
    }


def op_move(root, op):
    eid = required_id(op)
    el = find_id(root, eid)
    dx = float(op.get("dx", 0))
    dy = float(op.get("dy", 0))
    prepend_transform(el, f"translate({fmt(dx)} {fmt(dy)})")
    return {"type": "move", "id": eid, "dx": dx, "dy": dy}


def op_scale(root, op):
    eid = required_id(op)
    el = find_id(root, eid)
    sx = float(op.get("sx", op.get("scale", op.get("factor", 1))))
    sy = float(op.get("sy", sx))
    cx = float(op.get("cx", 0))
    cy = float(op.get("cy", 0))
    if cx or cy:
        transform = f"translate({fmt(cx)} {fmt(cy)}) scale({fmt(sx)} {fmt(sy)}) translate({fmt(-cx)} {fmt(-cy)})"
    else:
        transform = f"scale({fmt(sx)} {fmt(sy)})"
    prepend_transform(el, transform)
    return {"type": "scale", "id": eid, "sx": sx, "sy": sy, "cx": cx, "cy": cy}


def op_rotate(root, op):
    eid = required_id(op)
    el = find_id(root, eid)
    angle = float(op.get("angle", 0))
    cx = op.get("cx")
    cy = op.get("cy")
    if (cx is None) != (cy is None):
        raise ValueError("rotate requires both cx and cy when a center is supplied")
    transform = f"rotate({fmt(angle)})" if cx is None else f"rotate({fmt(angle)} {fmt(float(cx))} {fmt(float(cy))})"
    prepend_transform(el, transform)
    return {"type": "rotate", "id": eid, "angle": angle, "cx": cx, "cy": cy}


def op_set(root, op):
    eid = required_id(op)
    el = find_id(root, eid)
    attrs = op.get("attributes") or op.get("attrs")
    if not isinstance(attrs, dict) or not attrs:
        raise ValueError("set operation requires attributes: {name: value}")
    for key, value in attrs.items():
        if value is None:
            el.attrib.pop(str(key), None)
        else:
            el.set(str(key), str(value))
    return {"type": "set", "id": eid, "attributes": attrs}


def op_text(root, op):
    eid = required_id(op)
    el = find_id(root, eid)
    value = str(op.get("value", op.get("text", "")))
    tag = local_name(el)
    if tag not in {"text", "tspan"}:
        raise ValueError(f"replace_text target #{eid} is <{tag}>, expected <text> or <tspan>")
    children = [c for c in el if isinstance(c.tag, str)]
    if tag == "text" and children:
        tspans = [c for c in children if local_name(c) == "tspan"]
        if len(tspans) == 1:
            tspans[0].text = value
        elif len(tspans) > 1:
            raise ValueError(
                f"#{eid} contains {len(tspans)} tspans. Give the intended tspan an id and target it directly."
            )
        else:
            raise ValueError(f"#{eid} has structured children; target a child id instead")
    else:
        el.text = value
    return {"type": "replace_text", "id": eid, "value": value}


def op_delete(root, op):
    eid = required_id(op)
    el = find_id(root, eid)
    parent = el.getparent()
    if parent is None:
        raise ValueError("Cannot delete the SVG root")
    parent.remove(el)
    return {"type": "delete", "id": eid}


def op_rename_id(root, op):
    old = required_id(op)
    new = str(op.get("new_id") or op.get("to") or "").strip()
    if not new:
        raise ValueError("rename_id requires new_id")
    if new in all_ids(root):
        raise ValueError(f"Cannot rename #{old} to #{new}: id already exists")
    el = find_id(root, old)
    el.set("id", new)
    replace_references(root, old, new)
    return {"type": "rename_id", "id": old, "new_id": new}


def op_duplicate(root, op):
    eid = required_id(op)
    new_id = str(op.get("new_id") or "").strip()
    if not new_id:
        raise ValueError("duplicate requires new_id")
    if new_id in all_ids(root):
        raise ValueError(f"duplicate new_id already exists: #{new_id}")
    src = find_id(root, eid)
    parent = src.getparent()
    if parent is None:
        raise ValueError("Cannot duplicate SVG root")
    clone = copy.deepcopy(src)
    old_to_new = {}
    root_old = clone.get("id")
    if root_old:
        old_to_new[root_old] = new_id
        clone.set("id", new_id)
    # Re-ID descendants to prevent collisions.
    counter = 0
    for desc in clone.iterdescendants():
        old = desc.get("id")
        if old:
            counter += 1
            candidate = f"{new_id}__{sanitize_id(old)}"
            suffix = 2
            base = candidate
            while candidate in all_ids(root) or candidate in old_to_new.values():
                candidate = f"{base}-{suffix}"
                suffix += 1
            old_to_new[old] = candidate
            desc.set("id", candidate)
    for old, new in old_to_new.items():
        replace_references(root, old, new, scope=clone)
    parent.insert(parent.index(src) + 1, clone)
    if op.get("dx") is not None or op.get("dy") is not None:
        dx, dy = float(op.get("dx", 0)), float(op.get("dy", 0))
        prepend_transform(clone, f"translate({fmt(dx)} {fmt(dy)})")
    return {"type": "duplicate", "id": eid, "new_id": new_id, "descendant_ids_remapped": len(old_to_new)-1}


def op_group(root, op):
    ids = op.get("ids")
    group_id = str(op.get("group_id") or op.get("id") or "").strip()
    if not isinstance(ids, list) or len(ids) < 1:
        raise ValueError("group requires ids: [id1, id2, ...]")
    if not group_id:
        raise ValueError("group requires group_id")
    if group_id in all_ids(root):
        raise ValueError(f"group id already exists: #{group_id}")
    els = [find_id(root, str(eid)) for eid in ids]
    parents = {id(el.getparent()): el.getparent() for el in els}
    if len(parents) != 1:
        raise ValueError("group currently requires all target elements to share the same parent")
    parent = els[0].getparent()
    first_idx = min(parent.index(el) for el in els)
    group = etree.Element(f"{{{SVG_NS}}}g")
    group.set("id", group_id)
    parent.insert(first_idx, group)
    # Preserve document order.
    for el in sorted(els, key=lambda e: parent.index(e)):
        group.append(el)
    return {"type": "group", "ids": [str(x) for x in ids], "group_id": group_id}


def op_ungroup(root, op):
    eid = required_id(op)
    group = find_id(root, eid)
    if local_name(group) != "g":
        raise ValueError(f"ungroup target #{eid} is not a <g>")
    parent = group.getparent()
    if parent is None:
        raise ValueError("Cannot ungroup SVG root")
    idx = parent.index(group)
    group_transform = (group.get("transform") or "").strip()
    children = list(group)
    for child in children:
        if group_transform and isinstance(child.tag, str):
            prepend_transform(child, group_transform)
        parent.insert(idx, child)
        idx += 1
    parent.remove(group)
    return {"type": "ungroup", "id": eid, "children_moved": len(children), "transform_propagated": bool(group_transform)}


def op_ensure_ids(root, op):
    prefix = str(op.get("prefix", "svg"))
    assigned = ensure_ids(root, prefix=prefix, include_tspans=bool(op.get("include_tspans", True)))
    return {"type": "ensure_ids", "prefix": prefix, "assigned_count": len(assigned), "assigned": assigned}


OP_HANDLERS = {
    "move": op_move,
    "scale": op_scale,
    "rotate": op_rotate,
    "set": op_set,
    "set_attribute": op_set,
    "replace_text": op_text,
    "text": op_text,
    "delete": op_delete,
    "rename_id": op_rename_id,
    "duplicate": op_duplicate,
    "group": op_group,
    "ungroup": op_ungroup,
    "ensure_ids": op_ensure_ids,
}


def required_id(op):
    eid = str(op.get("id") or "").strip()
    if not eid:
        raise ValueError(f"{op.get('type', 'operation')} requires id")
    return eid.lstrip("#")


def fmt(v):
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return s if s not in {"-0", ""} else "0"


def sanitize_id(value: str):
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-")
    if not value:
        value = "item"
    if value[0].isdigit():
        value = "id-" + value
    return value




# -----------------------------------------------------------------------------
# Layout tokens, anchors, and rendered bounding boxes
# -----------------------------------------------------------------------------

BUILTIN_ANCHORS = {
    "left", "right", "top", "bottom", "center_x", "center_y", "width", "height"
}
LAYOUT_X_KEYS = {"left", "right", "center_x"}
LAYOUT_Y_KEYS = {"top", "bottom", "center_y"}
REF_RE = re.compile(r"\$\{([^}]+)\}")


def _parse_svg_number(value: str | None) -> float | None:
    if value is None:
        return None
    m = re.match(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", str(value))
    return float(m.group(1)) if m else None


def page_bbox(root):
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    if len(vb) == 4:
        x, y, w, h = map(float, vb)
        return {"x": x, "y": y, "width": w, "height": h}
    w = _parse_svg_number(root.get("width"))
    h = _parse_svg_number(root.get("height"))
    if w is None or h is None:
        raise ValueError("SVG page needs a viewBox or numeric width/height for layout anchors")
    return {"x": 0.0, "y": 0.0, "width": w, "height": h}


def bbox_anchors(bbox):
    x = float(bbox["x"]); y = float(bbox["y"])
    w = float(bbox["width"]); h = float(bbox["height"])
    return {
        "left": x,
        "right": x + w,
        "top": y,
        "bottom": y + h,
        "center_x": x + w / 2.0,
        "center_y": y + h / 2.0,
        "width": w,
        "height": h,
    }


def query_bbox_with_inkscape(tree, element_id: str):
    """Return rendered bbox for an SVG id using Inkscape's geometry engine."""
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tree.write(str(tmp_path), encoding="utf-8", xml_declaration=True, pretty_print=False)
        cmd = [
            "inkscape", str(tmp_path),
            f"--query-id={element_id}",
            "--query-x", "--query-y", "--query-width", "--query-height",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("Layout/anchor features require the Inkscape CLI (`inkscape`) on PATH") from exc
        if proc.returncode != 0:
            raise RuntimeError(f"Inkscape bbox query failed for #{element_id}: {proc.stderr.strip()}")
        vals = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if len(vals) != 4:
            raise RuntimeError(
                f"Inkscape did not return a bbox for #{element_id}; got {proc.stdout!r}"
            )
        x, y, w, h = map(float, vals)
        return {"x": x, "y": y, "width": w, "height": h}
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


class SafeExpressionEvaluator(ast.NodeVisitor):
    """Tiny arithmetic evaluator for already-substituted token expressions."""
    ALLOWED_BINOPS = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a ** b,
    }
    ALLOWED_UNARY = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}

    def __init__(self, names):
        self.names = names

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("Only numeric constants are allowed in layout expressions")

    def visit_Name(self, node):
        if node.id not in self.names:
            raise KeyError(node.id)
        return float(self.names[node.id])

    def visit_BinOp(self, node):
        fn = self.ALLOWED_BINOPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return fn(self.visit(node.left), self.visit(node.right))

    def visit_UnaryOp(self, node):
        fn = self.ALLOWED_UNARY.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return fn(self.visit(node.operand))

    def generic_visit(self, node):
        raise ValueError(f"Unsupported expression syntax: {type(node).__name__}")


def _safe_numeric_eval(expr: str, names: dict[str, float]):
    tree = ast.parse(expr, mode="eval")
    return float(SafeExpressionEvaluator(names).visit(tree))


def _anchor_ref_value(tree, root, ref: str, tokens, custom_anchors, stack=None):
    """Resolve ${element.anchor}, ${page.anchor}, or ${element.custom_anchor}."""
    ref = ref.strip()
    stack = list(stack or [])
    if ref in stack:
        raise ValueError(f"Circular anchor reference: {' -> '.join(stack + [ref])}")
    if "." not in ref:
        if ref in tokens:
            return float(tokens[ref])
        raise KeyError(f"Unknown reference ${{{ref}}}")
    obj, anchor = ref.rsplit(".", 1)
    if obj == "page":
        anchors = bbox_anchors(page_bbox(root))
        if anchor not in anchors:
            raise KeyError(f"Unknown page anchor: {anchor}")
        return anchors[anchor]

    # Custom anchors live under anchors: <id>: <name>: <expression>
    spec = (custom_anchors or {}).get(obj, {})
    if anchor in spec:
        return evaluate_layout_value(
            spec[anchor], tree, root, tokens, custom_anchors, stack=stack + [ref]
        )

    if anchor not in BUILTIN_ANCHORS:
        raise KeyError(f"Unknown anchor {anchor!r} for #{obj}")
    find_id(root, obj)  # clean error before invoking Inkscape
    return bbox_anchors(query_bbox_with_inkscape(tree, obj))[anchor]


def evaluate_layout_value(value, tree, root, tokens, custom_anchors=None, stack=None):
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Layout value must be numeric or expression string, got {value!r}")

    expr = value.strip()
    # Substitute explicit ${id.anchor} references first. This syntax permits SVG ids
    # containing dashes, colons, and dots without turning them into arithmetic syntax.
    def repl(match):
        v = _anchor_ref_value(tree, root, match.group(1), tokens, custom_anchors, stack=stack)
        return repr(float(v))
    expr = REF_RE.sub(repl, expr)
    return _safe_numeric_eval(expr, tokens)


def resolve_tokens(raw_tokens, tree, root, custom_anchors=None):
    raw_tokens = raw_tokens or {}
    if not isinstance(raw_tokens, dict):
        raise ValueError("tokens must be a mapping")
    resolved = {}
    pending = dict(raw_tokens)
    # Iterative resolution allows tokens to reference tokens declared later.
    while pending:
        progressed = False
        failures = {}
        for name, value in list(pending.items()):
            try:
                resolved[name] = evaluate_layout_value(value, tree, root, resolved, custom_anchors)
                pending.pop(name)
                progressed = True
            except (KeyError, NameError) as exc:
                failures[name] = str(exc)
        if not progressed:
            raise ValueError(f"Could not resolve tokens {sorted(pending)}; unresolved references: {failures}")
    return resolved


def _current_anchor_value(anchors, key):
    if key not in BUILTIN_ANCHORS:
        raise ValueError(f"Unsupported layout property {key!r}")
    return float(anchors[key])


def apply_layout_rule(tree, root, rule, tokens, custom_anchors):
    if not isinstance(rule, dict):
        raise ValueError("Each layout rule must be a mapping")
    eid = str(rule.get("id") or "").strip().lstrip("#")
    if not eid:
        raise ValueError("layout rule requires id")
    el = find_id(root, eid)

    x_keys = [k for k in LAYOUT_X_KEYS if k in rule]
    y_keys = [k for k in LAYOUT_Y_KEYS if k in rule]
    if len(x_keys) > 1:
        raise ValueError(f"layout #{eid}: use only one horizontal constraint for now, got {x_keys}")
    if len(y_keys) > 1:
        raise ValueError(f"layout #{eid}: use only one vertical constraint for now, got {y_keys}")
    if not x_keys and not y_keys:
        raise ValueError(f"layout #{eid}: no position constraint supplied")

    before_bbox = query_bbox_with_inkscape(tree, eid)
    before = bbox_anchors(before_bbox)
    resolved = {}
    dx = 0.0; dy = 0.0
    if x_keys:
        key = x_keys[0]
        target = evaluate_layout_value(rule[key], tree, root, tokens, custom_anchors)
        dx = target - _current_anchor_value(before, key)
        resolved[key] = target
    if y_keys:
        key = y_keys[0]
        target = evaluate_layout_value(rule[key], tree, root, tokens, custom_anchors)
        dy = target - _current_anchor_value(before, key)
        resolved[key] = target

    prepend_transform(el, f"translate({fmt(dx)} {fmt(dy)})")
    # Query after applying transform so the log records resolved rendered geometry.
    after_bbox = query_bbox_with_inkscape(tree, eid)
    after = bbox_anchors(after_bbox)
    return {
        "id": eid,
        "constraints": {k: rule[k] for k in x_keys + y_keys},
        "resolved_targets": resolved,
        "dx": dx,
        "dy": dy,
        "before": before,
        "after": after,
    }


def execute_layout(tree, root, layout_rules, tokens, custom_anchors):
    results = []
    for index, rule in enumerate(layout_rules or [], start=1):
        result = apply_layout_rule(tree, root, rule, tokens, custom_anchors)
        result["index"] = index
        results.append(result)
    return results

def execute_operations(root, operations):
    results = []
    for index, raw in enumerate(operations, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"operation #{index} must be a mapping")
        # Accept compact YAML like: - move: {id: star, dx: 5}
        if "type" not in raw and len(raw) == 1:
            typ, payload = next(iter(raw.items()))
            payload = {} if payload is None else payload
            if not isinstance(payload, dict):
                raise ValueError(f"operation #{index} compact payload must be a mapping")
            op = {"type": typ, **payload}
        else:
            op = dict(raw)
        typ = str(op.get("type", "")).strip().lower()
        if typ not in OP_HANDLERS:
            raise ValueError(f"unsupported SVG operation #{index}: {typ!r}")
        result = OP_HANDLERS[typ](root, op)
        result["index"] = index
        results.append(result)
    return results


def save_job(tree, root, input_path: Path, output_path: Path, operations, preview=True, preview_max_size=1800,
             tokens=None, anchors=None, layout=None, text=None, export=None):
    before = inspect_svg(parse_svg(input_path)[1])
    text_results = apply_text_specs(root, text or [])
    results = execute_operations(root, operations)
    resolved_tokens = resolve_tokens(tokens or {}, tree, root, anchors or {})
    layout_results = execute_layout(tree, root, layout or [], resolved_tokens, anchors or {})
    export_cfg = export or {}
    text_to_path = bool(export_cfg.get("text_to_path", False))
    export_svg(tree, output_path, text_to_path=text_to_path)
    final_tree, final_root = parse_svg(output_path)
    after = inspect_svg(final_root)
    preview_path = output_path.with_suffix(".preview.png")
    if preview:
        render_preview(output_path, preview_path, max_size=int(preview_max_size))
    log_path = output_path.with_suffix(".edit_log.json")
    log = {
        "input": str(input_path),
        "output": str(output_path),
        "preview": str(preview_path) if preview else None,
        "before": {
            "id_count": before["id_count"],
            "missing_id_count": before["missing_id_count"],
            "missing_ids_by_tag": before["missing_ids_by_tag"],
            "duplicates": before["duplicates"],
        },
        "tokens": resolved_tokens,
        "anchors": anchors or {},
        "text": text_results,
        "export": {"text_to_path": text_to_path},
        "operations": results,
        "layout": layout_results,
        "after": {
            "id_count": after["id_count"],
            "missing_id_count": after["missing_id_count"],
            "missing_ids_by_tag": after["missing_ids_by_tag"],
            "duplicates": after["duplicates"],
        },
    }
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    return preview_path if preview else None, log_path, log


def run_batch(job_path: Path):
    job_path = job_path.resolve()
    cfg = yaml.safe_load(job_path.read_text(encoding="utf-8")) or {}
    base = job_path.parent
    input_path = resolve_path(base, cfg.get("input"))
    output_path = resolve_path(base, cfg.get("output", "edited.svg"))
    tree, root = parse_svg(input_path)
    operations = cfg.get("operations") or []
    if cfg.get("ensure_ids"):
        spec = cfg.get("ensure_ids")
        if spec is True:
            spec = {"prefix": cfg.get("id_prefix", "svg")}
        operations = [{"type": "ensure_ids", **(spec or {})}] + list(operations)
    preview, log_path, log = save_job(
        tree, root, input_path, output_path, operations,
        preview=bool(cfg.get("preview", True)),
        preview_max_size=int(cfg.get("preview_max_size", 1800)),
        tokens=cfg.get("tokens") or {},
        anchors=cfg.get("anchors") or {},
        layout=cfg.get("layout") or [],
        text=cfg.get("text") or [],
        export=cfg.get("export") or {},
    )
    print(output_path)
    if preview:
        print(preview)
    print(log_path)
    return log


def resolve_path(base: Path, value):
    if value is None:
        raise ValueError("missing required path")
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def single_write(input_path, output_path, op, preview=True):
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    tree, root = parse_svg(input_path)
    return save_job(tree, root, input_path, output_path, [op], preview=preview)


def parse_attr_pairs(pairs):
    attrs = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"--attr must be NAME=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        attrs[k] = v
    return attrs


def add_io_args(parser):
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-preview", action="store_true")


def main():
    parser = argparse.ArgumentParser(description="Agent-friendly SVG editor: ID-first CLI operations and YAML jobs.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="List IDs and report renderable elements missing IDs.")
    p.add_argument("input", type=Path)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("anchors", help="Print rendered bbox anchors for an element id or the SVG page.")
    p.add_argument("input", type=Path)
    p.add_argument("--id", help="Element id. Omit for page anchors.")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("ensure-ids", help="Assign IDs to renderable elements that do not have them.")
    add_io_args(p)
    p.add_argument("--prefix", default="svg")

    p = sub.add_parser("move")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--dx", type=float, default=0); p.add_argument("--dy", type=float, default=0)

    p = sub.add_parser("scale")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--sx", type=float, required=True); p.add_argument("--sy", type=float); p.add_argument("--cx", type=float, default=0); p.add_argument("--cy", type=float, default=0)

    p = sub.add_parser("rotate")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--angle", type=float, required=True); p.add_argument("--cx", type=float); p.add_argument("--cy", type=float)

    p = sub.add_parser("set")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--attr", action="append", default=[], help="NAME=VALUE; repeatable")

    p = sub.add_parser("text")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--value", required=True)

    p = sub.add_parser("delete")
    add_io_args(p); p.add_argument("--id", required=True)

    p = sub.add_parser("rename-id")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--new-id", required=True)

    p = sub.add_parser("duplicate")
    add_io_args(p); p.add_argument("--id", required=True); p.add_argument("--new-id", required=True); p.add_argument("--dx", type=float, default=0); p.add_argument("--dy", type=float, default=0)

    p = sub.add_parser("group")
    add_io_args(p); p.add_argument("--ids", nargs="+", required=True); p.add_argument("--group-id", required=True)

    p = sub.add_parser("ungroup")
    add_io_args(p); p.add_argument("--id", required=True)

    p = sub.add_parser("batch", help="Apply an ordered YAML edit job.")
    p.add_argument("job", type=Path)

    args = parser.parse_args()

    if args.command == "inspect":
        _, root = parse_svg(args.input.resolve())
        report = inspect_svg(root)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"IDs: {report['id_count']}")
            for item in report["ids"]:
                print(f"  #{item['id']} <{item['tag']}>")
            print(f"Missing renderable IDs: {report['missing_id_count']}")
            for tag, count in sorted(report["missing_ids_by_tag"].items()):
                print(f"  {tag}: {count}")
            if report["duplicates"]:
                print("Duplicate IDs:")
                for item in report["duplicates"]:
                    print(f"  #{item['id']}: {item['count']}")
        return

    if args.command == "anchors":
        tree, root = parse_svg(args.input.resolve())
        if args.id:
            eid = str(args.id).lstrip("#")
            find_id(root, eid)
            result = {"id": eid, **bbox_anchors(query_bbox_with_inkscape(tree, eid))}
        else:
            result = {"id": "page", **bbox_anchors(page_bbox(root))}
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"{result['id']} anchors:")
            for key in ["left","right","top","bottom","center_x","center_y","width","height"]:
                print(f"  {key}: {fmt(result[key])}")
        return

    if args.command == "batch":
        run_batch(args.job)
        return

    preview = not args.no_preview
    if args.command == "ensure-ids":
        op = {"type": "ensure_ids", "prefix": args.prefix}
    elif args.command == "move":
        op = {"type": "move", "id": args.id, "dx": args.dx, "dy": args.dy}
    elif args.command == "scale":
        op = {"type": "scale", "id": args.id, "sx": args.sx, "sy": args.sy if args.sy is not None else args.sx, "cx": args.cx, "cy": args.cy}
    elif args.command == "rotate":
        op = {"type": "rotate", "id": args.id, "angle": args.angle, "cx": args.cx, "cy": args.cy}
    elif args.command == "set":
        op = {"type": "set", "id": args.id, "attributes": parse_attr_pairs(args.attr)}
    elif args.command == "text":
        op = {"type": "replace_text", "id": args.id, "value": args.value}
    elif args.command == "delete":
        op = {"type": "delete", "id": args.id}
    elif args.command == "rename-id":
        op = {"type": "rename_id", "id": args.id, "new_id": args.new_id}
    elif args.command == "duplicate":
        op = {"type": "duplicate", "id": args.id, "new_id": args.new_id, "dx": args.dx, "dy": args.dy}
    elif args.command == "group":
        op = {"type": "group", "ids": args.ids, "group_id": args.group_id}
    elif args.command == "ungroup":
        op = {"type": "ungroup", "id": args.id}
    else:
        raise AssertionError(args.command)

    preview_path, log_path, _ = single_write(args.input, args.output, op, preview=preview)
    print(args.output.resolve())
    if preview_path:
        print(preview_path)
    print(log_path)


if __name__ == "__main__":
    main()
