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
DD = load("scripts/dedupe-v0.1.py")
PR = load("scripts/prisma-v0.1.py")
SH = load("scripts/screen-sheet-v0.1.py")
PM = load("scripts/search-pubmed-v0.1.py")
OA = load("scripts/search-openalex-v0.1.py")


def all_fixture_records():
    recs = IMP.import_files("wos", [str(FX / "wos-plain-sample-v0.1.txt")])
    recs += IMP.import_files("scopus-csv", [str(FX / "scopus-sample-v0.1.csv")])
    recs += IMP.import_files("ris", [str(FX / "embase-sample-v0.1.ris")], "embase")
    recs += IMP.import_files("cnki", [str(FX / "cnki-refworks-sample-v0.1.txt")], "cnki")
    recs += IMP.import_files("cnki", [str(FX / "wanfang-noteexpress-sample-v0.1.txt")], "wanfang")
    return recs


class TestNormalise(unittest.TestCase):
    def test_doi_pmid_title(self):
        self.assertEqual(C.norm_doi("https://doi.org/10.1000/ABC."), "10.1000/abc")
        self.assertEqual(C.norm_doi("doi: 10.1/x"), "10.1/x")
        self.assertIsNone(C.norm_doi("not a doi"))
        self.assertEqual(C.norm_pmid("PMID: 12345"), "12345")
        self.assertEqual(C.norm_title("Café <i>effects</i> — of IR!"), "cafe effects of ir")
        self.assertEqual(C.norm_title("合成样例：外泌体"), "合成样例 外泌体")

    def test_pubmed_xml_parser(self):
        r = PM.parse_pubmed_xml((FX / "pubmed-efetch-sample-v0.1.xml").read_bytes())[0]
        self.assertEqual((r["pmid"], r["doi"], r["pmcid"], r["year"]), ("90000001", "10.99999/synthetic.001", "PMC0000001", 2024))
        self.assertEqual(r["authors"], ["Zhang S", "Synthetic Consortium"])
        self.assertIn("RESULTS: Synthetic results.", r["abstract"])
        self.assertEqual(V.validate(r, RECORD_SCHEMA), [])

    def test_openalex_abstract(self):
        self.assertEqual(OA.abstract_from_index({"radiation": [0], "injures": [1], "muscle": [2]}), "radiation injures muscle")
        self.assertIsNone(OA.abstract_from_index(None))


class TestDedupe(unittest.TestCase):
    def test_merge_doi_pmid_title(self):
        recs = all_fixture_records()
        merged, st = DD.dedupe(recs)
        self.assertEqual(st["input_records"], 10)
        # WoS#1 + Scopus#1 (DOI) + Embase#1 (PMID only) -> 1 ; CNKI#2 + Wanfang#1 (same Chinese title) -> 1
        self.assertEqual(st["unique_records"], 7)
        one = next(m for m in merged if m.get("doi") == "10.99999/synthetic.001")
        self.assertEqual(one["source_dbs"], ["embase", "scopus", "wos"])
        cn = [m for m in merged if m.get("title") == "合成样例：外泌体与放射性肌纤维化"]
        self.assertEqual(len(cn), 1)
        self.assertEqual(cn[0]["source_dbs"], ["cnki", "wanfang"])


class TestScreenAndPrisma(unittest.TestCase):
    def test_sheet_roundtrip_and_counts(self):
        merged, st = DD.dedupe(all_fixture_records())
        with tempfile.TemporaryDirectory() as d:
            sheet = Path(d) / "s.csv"
            rows = SH.make(merged, None, ["radiat|irradiat|辐射|放射", "muscle|肌"])
            self.assertTrue(any(r["triage"] == "topic-terms-missing" for r in rows))
            for i, r in enumerate(rows):
                r["ta_decision"] = "exclude" if i < 2 else "include"
                r["ta_reason"] = "synthetic exclusion" if i < 2 else ""
                if i >= 2:
                    r["ft_decision"] = {2: "not_retrieved", 3: "exclude", 6: "exclude"}.get(i, "include")
                    r["ft_reason"] = "no target tissue" if i == 3 else ""
                    r["grade"] = "A" if i == 4 else "B"
                    r["abstract_only"] = "yes" if i == 5 else "no"
            SH.write_sheet(sheet, rows)
            # judgments survive a refresh
            again = SH.make(merged, SH.read_sheet(sheet))
            self.assertEqual([r["ta_decision"] for r in again], [r["ta_decision"] for r in rows])
            log = [{"db": "wos", "retrieved": 2}, {"db": "scopus", "retrieved": 2}, {"db": "embase", "retrieved": 2},
                   {"db": "cnki", "retrieved": 2}, {"db": "wanfang", "retrieved": 2}]
            c = PR.counts(log, st, SH.read_sheet(sheet))
            self.assertEqual(c["identified_total"], 10)
            self.assertEqual(c["duplicates_removed"], 3)
            self.assertEqual(c["records_screened"], 7)
            self.assertEqual(c["records_excluded_title_abstract"], 2)
            self.assertEqual((c["reports_sought"], c["reports_not_retrieved"], c["reports_assessed"], c["reports_excluded"]), (5, 1, 4, 2))
            self.assertEqual(c["included"], 2)
            self.assertEqual(c["included_by_grade"], {"A": 1, "B": 1})
            self.assertEqual(c["included_abstract_only"], 1)
            inc = SH.included(SH.read_sheet(sheet))
            self.assertEqual(len(inc), 2)
            svg = PR.to_svg(c)
            self.assertTrue(svg.startswith("<svg") and "纳入 Included" in svg)
            self.assertIn("| **纳入 / included** | **2**", PR.to_markdown(c))


if __name__ == "__main__":
    unittest.main()
