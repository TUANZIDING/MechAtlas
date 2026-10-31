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
IMP = load("scripts/import-export-v0.1.py")


def imp(fmt, name, db=None):
    return IMP.import_files(fmt, [str(FX / name)], db)


class TestImporters(unittest.TestCase):
    def assertValid(self, recs):
        for r in recs:
            self.assertEqual(V.validate(r, RECORD_SCHEMA), [], r)

    def test_wos_plain(self):
        r = imp("wos", "wos-plain-sample-v0.1.txt")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["doi"], "10.99999/synthetic.001")
        self.assertEqual(r[0]["pmid"], "90000001")
        self.assertEqual(r[0]["authors"], ["Zhang, S", "Li, M"])
        self.assertIn("satellite cell signalling in a mouse model", r[0]["title"])  # continuation line joined
        self.assertEqual(r[0]["year"], 2024)
        self.assertEqual(r[0]["raw_id"], "WOS:000000000000001")
        self.assertIn("Early Access", r[1]["pub_types"])
        self.assertValid(r)

    def test_wos_tab_utf16(self):
        r = imp("wos", "wos-tab-sample-v0.1.txt")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["pmid"], "90000001")
        self.assertIsNone(r[1]["doi"])
        self.assertTrue(r[1]["id"].startswith("wos:"))
        self.assertValid(r)

    def test_scopus_csv(self):
        r = imp("scopus-csv", "scopus-sample-v0.1.csv")
        self.assertEqual([x["source_db"] for x in r], ["scopus", "scopus"])
        self.assertEqual(r[0]["pmid"], "90000001")
        self.assertIsNone(r[1]["abstract"])  # "[No abstract available]" dropped
        self.assertEqual(r[1]["raw_id"], "2-s2.0-0000000004")
        self.assertValid(r)

    def test_scopus_ris_pmid_in_note(self):
        r = imp("ris", "scopus-sample-v0.1.ris", "scopus")
        self.assertEqual(r[0]["pmid"], "90000001")
        self.assertEqual(r[0]["journal"], "Synthetic Journal of Radiation Biology")
        self.assertValid(r)

    def test_embase_ris(self):
        r = imp("ris", "embase-sample-v0.1.ris", "embase")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["pmid"], "90000001")  # Embase C5 field
        self.assertEqual(r[0]["year"], 2024)
        self.assertEqual(r[1]["abstract"], "Synthetic conference abstract.")
        self.assertIn("Conference Abstract", r[1]["pub_types"])
        self.assertValid(r)

    def test_cnki_refworks(self):
        r = imp("cnki", "cnki-refworks-sample-v0.1.txt", "cnki")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["authors"], ["张三", "李四"])
        self.assertEqual(r[0]["doi"], "10.99999/synthetic.cn1")
        self.assertEqual(r[0]["language"], "zh")
        self.assertEqual(r[1]["year"], 2022)
        self.assertValid(r)

    def test_cnki_endnote(self):
        r = imp("cnki", "cnki-endnote-sample-v0.1.txt", "cnki")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["journal"], "合成样例放射医学杂志")
        self.assertValid(r)

    def test_wanfang_noteexpress(self):
        r = imp("cnki", "wanfang-noteexpress-sample-v0.1.txt", "wanfang")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[1]["authors"], ["赵六", "钱七"])
        self.assertEqual(r[0]["source_db"], "wanfang")
        self.assertValid(r)

    def test_detect_formats(self):
        cn = load("importers/cnki-wanfang-v0.1.py")
        self.assertEqual(cn.detect("RT Journal Article\nT1 x"), "refworks")
        self.assertEqual(cn.detect("%0 Journal Article\n%T x"), "endnote")
        self.assertEqual(cn.detect("{Reference Type}: Journal Article"), "noteexpress")
        self.assertEqual(cn.detect("TY  - JOUR\nTI  - x"), "ris")
        with self.assertRaises(ValueError):
            cn.detect("hello")


if __name__ == "__main__":
    unittest.main()
