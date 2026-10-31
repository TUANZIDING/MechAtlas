#!/usr/bin/env python3
"""Validate a registry + studies JSON against schema/ and the evidence contract (stdlib only).

Evidence contract (same as build-atlas-v0.1.mjs, checked before any HTML is built):
  * every edge has >= 1 ref, every ref is a source card, every source is an included study (PMID/DOI)
  * every evidence node has >= 1 edge; scaffold nodes (conflicting / hypothesis-only) have none
  * grades are A/B/C (keys of registry.grades); direction is + or -; causal is boolean
  * included studies graded A must have been read in full (abstract_only = false)
Also validates JSONL records against schema/record-v0.1.schema.json with --records.

Examples:
  python3 scripts/validate-registry-v0.1.py --registry examples/demo/registry-demo-v0.1.json --studies examples/demo/studies-demo-v0.1.json
  python3 scripts/validate-registry-v0.1.py --records runs/demo/deduped-v0.1.jsonl
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int, "boolean": bool, "null": type(None)}


def validate(inst, schema, path="$") -> list[str]:
    """Tiny JSON-Schema subset: type, required, properties, additionalProperties, items, enum, pattern,
    minimum, maximum, minItems, anyOf."""
    errs = []
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        ok = any(isinstance(inst, TYPES[x]) and not (x in ("number", "integer") and isinstance(inst, bool)) for x in ts)
        if not ok:
            return [f"{path}: expected {t}, got {type(inst).__name__}"]
    if "anyOf" in schema and not any(not validate(inst, s, path) for s in schema["anyOf"]):
        errs.append(f"{path}: matches none of anyOf")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if isinstance(inst, str) and "pattern" in schema and not re.search(schema["pattern"], inst):
        errs.append(f"{path}: {inst!r} does not match {schema['pattern']}")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: {inst} < {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: {inst} > {schema['maximum']}")
    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing required '{k}'")
        props = schema.get("properties", {})
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs += validate(v, schema["additionalProperties"], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected '{k}'")
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, v in enumerate(inst):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def load_schema(name):
    return json.loads((ROOT / "schema" / name).read_text(encoding="utf-8"))


def contract(reg: dict, studies: list[dict]) -> list[str]:
    errs = []
    grades = set((reg.get("grades") or {}).keys()) or {"A", "B", "C"}
    nodes = {n["id"]: n for n in reg["nodes"]}
    srcs = {s["id"]: s for s in reg["sources"]}
    norm = lambda d: (d or "").lower().replace("https://doi.org/", "")
    inc_p = {str(s["pmid"]) for s in studies if s.get("pmid")}
    inc_d = {norm(s["doi"]) for s in studies if s.get("doi")}
    for s in srcs.values():
        if not ((s.get("pmid") and str(s["pmid"]) in inc_p) or (s.get("doi") and norm(s["doi"]) in inc_d)):
            errs.append(f"source {s['id']} is not an included study")
    linked = set()
    for e in reg["edges"]:
        linked |= {e["source"], e["target"]}
        if e["source"] not in nodes or e["target"] not in nodes:
            errs.append(f"edge {e['id']}: dangling endpoint")
        if e["grade"] not in grades:
            errs.append(f"edge {e['id']}: grade {e['grade']} undefined")
        for r in e.get("refs") or []:
            if r not in srcs:
                errs.append(f"edge {e['id']}: ref {r} has no source card")
    for n in nodes.values():
        if n["status"] == "evidence" and n["id"] not in linked:
            errs.append(f"evidence node {n['id']} has no edge")
        if n["status"] == "scaffold" and n["id"] in linked:
            errs.append(f"scaffold node {n['id']} carries an evidence edge")
    for s in studies:
        if s.get("grade") == "A" and s.get("abstract_only"):
            errs.append(f"study {s.get('pmid') or s.get('doi')}: grade A requires full text (abstract_only=true)")
    used = {r for e in reg["edges"] for r in e.get("refs") or []}
    for sid in srcs:
        if sid not in used:
            errs.append(f"source {sid} is not cited by any edge")
    return errs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry")
    ap.add_argument("--studies")
    ap.add_argument("--records", help="JSONL of records to validate against record schema")
    a = ap.parse_args()
    errs = []
    if a.registry:
        reg = json.loads(Path(a.registry).read_text(encoding="utf-8"))
        errs += [f"registry {e}" for e in validate(reg, load_schema("registry-v0.1.schema.json"))]
    if a.studies:
        st = json.loads(Path(a.studies).read_text(encoding="utf-8"))
        errs += [f"studies {e}" for e in validate(st, load_schema("study-v0.1.schema.json"))]
    if a.registry and a.studies:
        errs += contract(reg, st)
    if a.records:
        sch = load_schema("record-v0.1.schema.json")
        for i, line in enumerate(Path(a.records).read_text(encoding="utf-8").splitlines()):
            if line.strip():
                errs += [f"record {i + 1} {e}" for e in validate(json.loads(line), sch)]
    for e in errs[:200]:
        print("ERROR", e)
    print(json.dumps({"errors": len(errs), "ok": not errs}))
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
