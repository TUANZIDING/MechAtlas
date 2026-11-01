#!/usr/bin/env python3
"""Automatic re-layout for the render gate: move nodes, never drop or merge them.

Only the row (r) of a node changes; its column (causal layer), lane, edges and evidence stay as they are.
For every node involved in an overlap / edge-through-node / outside-lane problem (from the static geometry check
in gates-v0.1.py plus, optionally, the browser verifier's validation JSON) the nearest free row inside its lane is
chosen. If the lane has no free row, the lane grows by one row and every lane below shifts down.

Examples:
  python3 scripts/relayout-v0.1.py --registry runs/x/registry-v0.1.json --out runs/x/registry-v0.1.json
  python3 scripts/relayout-v0.1.py --registry R.json --validation runs/x/validation-v0.1.json --out R.json
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path


def _load(rel: str):
    p = Path(__file__).resolve().parent.parent / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


G = _load("scripts/gates-v0.1.py")
STEP = 0.25


def problem_nodes(geo: dict, validation: dict | None = None) -> list[str]:
    """Nodes to move, in a stable order: the second node of an overlapping pair, the node an edge runs through,
    and nodes outside their lane. Browser findings (validation JSON details) are added when given."""
    out = []
    add = lambda n: out.append(n) if n and n not in out else None
    for p in geo.get("nodeOverlap", []):
        add(p.split("/")[-1])
    for p in geo.get("edgeThroughNode", []):
        add(p.split(" x ")[-1])
    for n in geo.get("nodeOutsideLane", []):
        add(n)
    for c in (validation or {}).get("checks", []):
        if c.get("pass"):
            continue
        name, detail = c.get("name", ""), c.get("detail", "")
        for part in filter(None, (x.strip() for x in detail.split(";"))):
            if "nodeOverlap" in name:
                add(part.split("/")[-1])
            elif "edgeThroughNode" in name:
                add(part.split(" x ")[-1].strip())
            elif "badgeOnNode" in name:
                add(part.split(" on ")[-1].strip())
            elif "nodeOutsideLane" in name:
                add(part)
    return out


def _score(reg: dict) -> int:
    geo = G.geometry(reg)
    return sum(len(geo[k]) for k in ("nodeOverlap", "edgeThroughNode", "nodeOutsideLane"))


def _best_row(reg: dict, nid: str, lane: dict, allow_equal: bool = False) -> bool:
    """Move node nid to the nearest row in its lane that lowers the total problem count (or keeps it, for
    browser-only findings such as a badge on a node). Returns True when the node moved."""
    n = next(x for x in reg["nodes"] if x["id"] == nid)
    lo, hi = G.lane_range(lane)
    k = int((hi - lo) / STEP) + 1
    r0 = n["r"]
    cands = sorted({round(min(hi, lo + i * STEP), 3) for i in range(k)} | {round(lo, 3), round(hi, 3)},
                   key=lambda r: (abs(r - r0), r))
    base = _score(reg)
    best = (base, r0)
    for r in cands:
        if abs(r - r0) < 1e-9:
            continue
        n["r"] = r
        s = _score(reg)
        if s < best[0] or (allow_equal and best[1] == r0 and s <= base):
            best = (s, r)
            if s == 0:
                break
    n["r"] = best[1]
    return best[1] != r0


def _grow_lane(reg: dict, lane: dict, rows: float = 1.0) -> None:
    old_r1 = lane["r1"]
    for L in reg["lanes"]:
        if L is not lane and L["r0"] >= old_r1 - 1e-9:
            L["r0"] += rows
            L["r1"] += rows
    for n in reg["nodes"]:
        if n["r"] >= old_r1 - 1e-9:
            n["r"] += rows
    lane["r1"] += rows
    reg["rows"] = reg.get("rows", old_r1) + rows
    if isinstance(reg.get("scaffoldTop"), (int, float)) and reg["scaffoldTop"] >= old_r1 - 1e-9:
        reg["scaffoldTop"] += rows


def relayout(reg: dict, validation: dict | None = None) -> tuple[dict, list[dict]]:
    """Return (new registry, moves). Nodes and edges are the same objects/ids; only r and lane bounds change."""
    reg = copy.deepcopy(reg)
    moves = []
    browser = set(problem_nodes({}, validation)) if validation else set()
    lane_idx = lambda r, nid: next(i for i, L in enumerate(r["lanes"]) if L is G.lane_of(r, _node(r, nid)["r"]))
    for nid in problem_nodes(G.geometry(reg), validation):
        if not any(n["id"] == nid for n in reg["nodes"]) or (_score(reg) == 0 and nid not in browser):
            continue
        if not G.lane_of(reg, _node(reg, nid)["r"]):
            continue
        orig = _node(reg, nid)["r"]
        before = _score(reg)
        if _best_row(reg, nid, G.lane_of(reg, orig), allow_equal=nid in browser):
            moves.append({"node": nid, "from": orig, "to": _node(reg, nid)["r"], "lane_grown": False})
            continue
        trial = copy.deepcopy(reg)
        _grow_lane(trial, trial["lanes"][lane_idx(reg, nid)])
        if _best_row(trial, nid, trial["lanes"][lane_idx(reg, nid)]) and _score(trial) < before:
            reg = trial
            moves.append({"node": nid, "from": orig, "to": _node(reg, nid)["r"], "lane_grown": True})
    return reg, moves


def _node(reg: dict, nid: str) -> dict:
    return next(n for n in reg["nodes"] if n["id"] == nid)


def same_evidence(a: dict, b: dict) -> bool:
    """Re-layout must not touch evidence: identical edges, sources and node ids/columns/labels/status."""
    strip = lambda r: {"edges": r["edges"], "sources": r["sources"],
                       "nodes": [{k: v for k, v in n.items() if k != "r"} for n in r["nodes"]]}
    return G.canonical(strip(a)) == G.canonical(strip(b))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--validation", help="validation JSON from verify-atlas-v0.1.mjs (browser layout findings)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    reg = json.loads(Path(a.registry).read_text(encoding="utf-8"))
    val = json.loads(Path(a.validation).read_text(encoding="utf-8")) if a.validation else None
    new, moves = relayout(reg, val)
    assert same_evidence(reg, new)
    Path(a.out).write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    geo = G.geometry(new)
    print(json.dumps({"moves": moves, "geometry_pass": geo["pass"], "out": a.out}, ensure_ascii=False))
    sys.exit(0 if geo["pass"] else 1)


if __name__ == "__main__":
    main()
