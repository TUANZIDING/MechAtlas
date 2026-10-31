import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"


def load(rel: str):
    p = ROOT / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


C = load("scripts/common-v0.1.py")
V = load("scripts/validate-registry-v0.1.py")
RECORD_SCHEMA = json.loads((ROOT / "schema" / "record-v0.1.schema.json").read_text(encoding="utf-8"))
import copy

REG = json.loads((ROOT / "examples/demo/registry-demo-v0.1.json").read_text(encoding="utf-8"))
ST = json.loads((ROOT / "examples/demo/studies-demo-v0.1.json").read_text(encoding="utf-8"))
REG_SCHEMA = json.loads((ROOT / "schema/registry-v0.1.schema.json").read_text(encoding="utf-8"))
ST_SCHEMA = json.loads((ROOT / "schema/study-v0.1.schema.json").read_text(encoding="utf-8"))


class TestRegistry(unittest.TestCase):
    def test_demo_valid(self):
        self.assertEqual(V.validate(REG, REG_SCHEMA), [])
        self.assertEqual(V.validate(ST, ST_SCHEMA), [])
        self.assertEqual(V.contract(REG, ST), [])

    def test_demo_uses_real_ids(self):
        for s in REG["sources"]:
            self.assertRegex(s["pmid"], r"^\d{8}$")
            self.assertTrue(s["doi"].startswith("10."))

    def test_edge_without_included_source_fails(self):
        r = copy.deepcopy(REG)
        r["sources"].append({"id": "P99999999", "pmid": "99999999", "title": {"en": "not included"}})
        r["edges"][0]["refs"].append("P99999999")
        self.assertTrue(any("not an included study" in e for e in V.contract(r, ST)))

    def test_scaffold_with_edge_fails(self):
        r = copy.deepcopy(REG)
        r["edges"][0]["target"] = "gapSting"
        errs = V.contract(r, ST)
        self.assertTrue(any("scaffold node gapSting" in e for e in errs))

    def test_orphan_evidence_node_fails(self):
        r = copy.deepcopy(REG)
        r["nodes"].append({"id": "lonely", "status": "evidence", "label": {"zh": "孤立"}, "c": 1, "r": 0})
        self.assertTrue(any("lonely" in e for e in V.contract(r, ST)))

    def test_grade_a_abstract_only_fails(self):
        s = copy.deepcopy(ST)
        next(x for x in s if x["grade"] == "A")["abstract_only"] = True
        self.assertTrue(any("grade A requires full text" in e for e in V.contract(REG, s)))

    def test_schema_rejects_bad_direction(self):
        r = copy.deepcopy(REG)
        r["edges"][0]["direction"] = "up"
        self.assertTrue(V.validate(r, REG_SCHEMA))


class TestTopicConfigs(unittest.TestCase):
    def test_yaml_examples_parse(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        for p in ["config/topic-template-v0.1.yaml", "examples/radiation-muscle/topic-v0.1.yaml", "examples/ev-intervention/topic-v0.1.yaml"]:
            cfg = C.load_topic(ROOT / p)
            self.assertIn("databases", cfg["search"], p)
            self.assertEqual(set(cfg["grades"]), {"A", "B", "C"}, p)
            self.assertTrue(cfg["atlas"]["lanes"], p)


if __name__ == "__main__":
    unittest.main()
