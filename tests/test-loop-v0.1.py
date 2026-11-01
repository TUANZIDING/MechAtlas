"""Gated loops and human-approval checkpoints (offline; search, build, full text and ref checks are injected)."""
import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
G = load("scripts/gates-v0.1.py")
RL = load("scripts/relayout-v0.1.py")
FT = load("scripts/fulltext-oa-v0.1.py")
RS = load("scripts/run-search-v0.1.py")
SH = load("scripts/screen-sheet-v0.1.py")
RP = load("scripts/report-v0.1.py")
V = load("scripts/validate-registry-v0.1.py")
PL = load("scripts/pipeline-v0.1.py")

REG = json.loads((ROOT / "examples/demo/registry-demo-v0.1.json").read_text(encoding="utf-8"))
ST = json.loads((ROOT / "examples/demo/studies-demo-v0.1.json").read_text(encoding="utf-8"))
STATE_SCHEMA = json.loads((ROOT / "schema/run-state-v0.1.schema.json").read_text(encoding="utf-8"))
REG_SCHEMA = json.loads((ROOT / "schema/registry-v0.1.schema.json").read_text(encoding="utf-8"))


def fake_html(reg, drop=(), repoint=None) -> str:
    """Atlas HTML with DATA built like build-atlas-v0.1.mjs (parallel relations grouped), with optional defects."""
    groups = {}
    for e in reg["edges"]:
        if e["id"] in drop:
            continue
        s, t = (repoint or {}).get(e["id"], (e["source"], e["target"]))
        g = groups.setdefault(f"{s}>{t}", {"key": f"{s}>{t}", "source": s, "target": t, "items": []})
        g["items"].append({"id": e["id"], "grade": e["grade"], "causal": e["causal"], "sign": e["direction"], "refs": e["refs"]})
    data = {"nodes": reg["nodes"], "edges": list(groups.values()), "sources": {s["id"]: s for s in reg["sources"]},
            "stats": {"nodes": len(reg["nodes"]), "relations": len(reg["edges"])}}
    return "<script>\nconst DATA=" + json.dumps(data, ensure_ascii=False) + ";\nconst S={};</script>"


# ------------------------------------------------------------------ gates ----


class TestRetrievalGate(unittest.TestCase):
    def test_error_and_zero_hits_fail_whole_database(self):
        log = [{"db": "pubmed", "query_id": "a", "status": "ok", "retrieved": 5},
               {"db": "pubmed", "query_id": "b", "status": "ok", "retrieved": 0},
               {"db": "openalex", "query_id": "a", "status": "error", "retrieved": 0, "error": "HTTP 429"},
               {"db": "crossref", "query_id": "a", "status": "ok", "retrieved": 0},
               {"db": "crossref", "query_id": "b", "status": "ok", "retrieved": 0}]
        f = G.retrieval_failures(log)
        self.assertEqual(sorted((x["db"], x["query_id"], x["reason"]) for x in f),
                         [("crossref", "a", "zero_hits"), ("crossref", "b", "zero_hits"), ("openalex", "a", "error")])
        self.assertEqual(G.retrieval_failures(log, excused={"openalex", "crossref"}), [])
        self.assertEqual(len(G.retrieval_failures(log, fail_on_zero_hits=False)), 1)

    def test_missing_manual_export_is_logged_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = {"search": {"manual_exports": [{"id": "wos1", "db": "wos", "format": "wos", "path": f"{d}/nope/*.txt"}]}}
            log = RS.import_exports(cfg, Path(d))
            self.assertEqual(log[0]["status"], "error")
            self.assertTrue(G.retrieval_failures(log)[0]["manual"])

    def test_merge_log_replaces_rerun_queries(self):
        old = [{"db": "a", "query_id": "1", "v": 0}, {"db": "b", "query_id": "1", "v": 0}]
        self.assertEqual(RS.merge_log(old, [{"db": "b", "query_id": "1", "v": 1}])[1]["v"], 1)


def drop_study_33316945():
    """Demo registry where study 33316945 is no longer cited by any edge (contract still valid)."""
    r = copy.deepcopy(REG)
    for e in r["edges"]:
        if e["id"] in ("ED10", "ED11"):
            e["refs"] = ["P32754983"]
    r["sources"] = [s for s in r["sources"] if s["id"] != "P33316945"]
    return r


def oa_fakes(oa_pmids):
    """Europe PMC / Unpaywall stand-ins: PMIDs in oa_pmids are open access in PMC, others only have an Unpaywall link."""
    calls = []

    def get_json(url, params=None):
        calls.append(url)
        if "unpaywall" in url:
            return {"is_oa": True, "best_oa_location": {"url": "https://example.org/oa"}}
        pm = params["query"].split(":")[1].split()[0]
        return {"resultList": {"result": [{"pmcid": f"PMC{pm}", "isOpenAccess": "Y" if pm in oa_pmids else "N", "doi": "10.99999/x"}]}}
    return calls, dict(get_json=get_json, get_bytes=lambda url, params=None: b"<article><body>Synthetic</body></article>")


class TestCoverageGate(unittest.TestCase):
    def test_demo_fully_mapped(self):
        c = G.coverage(ST, REG)
        self.assertTrue(c["pass"])
        self.assertEqual((c["mapped"], c["unaccounted"]), (9, 0))

    def test_unaccounted_study_fails(self):
        r = drop_study_33316945()
        self.assertEqual(V.contract(r, ST), [])
        c = G.coverage(ST, r)
        self.assertFalse(c["pass"])
        self.assertEqual([x["study"] for x in c["unaccounted_items"]], ["pmid:33316945"])

    def test_layout_is_never_a_reason(self):
        r = drop_study_33316945()
        r["unmapped"] = [{"pmid": "33316945", "category": "insufficient", "reason": {"zh": "总图几何核查与凋亡枢纽冲突，布局放不下"}}]
        c = G.coverage(ST, r)
        self.assertFalse(c["pass"])
        self.assertIn("layout", c["invalid_items"][0]["why"])

    def test_valid_reasons_and_their_requirements(self):
        r = drop_study_33316945()
        r["unmapped"] = [{"pmid": "33316945", "category": "scaffold", "node": "mpcEV", "reason": "方向矛盾，只支持骨架"}]
        self.assertIn("scaffold node", G.coverage(ST, r)["invalid_items"][0]["why"])
        r["unmapped"][0]["node"] = "gapSting"
        c = G.coverage(ST, r)
        self.assertTrue(c["pass"])
        self.assertEqual(c["excused_by_category"], {"scaffold": 1})
        self.assertEqual(V.validate(r, REG_SCHEMA), [])
        r["unmapped"] = [{"pmid": "33316945", "category": "redundant", "see": "P99", "reason": "同一模型同一结论"}]
        self.assertFalse(G.coverage(ST, r)["pass"])

    def test_threshold(self):
        r = drop_study_33316945()
        r["unmapped"] = [{"pmid": "33316945", "category": "background", "reason": "细胞系背景描述，无机制读出"}]
        self.assertTrue(G.coverage(ST, r, 0.85)["pass"])
        self.assertFalse(G.coverage(ST, r, 0.95)["pass"])


class TestRenderGate(unittest.TestCase):
    def test_built_demo_matches_registry(self):
        res = G.render_consistency(REG, G.atlas_data((ROOT / "docs/index.html").read_text(encoding="utf-8")))
        self.assertTrue(res["pass"], res["mismatches"])
        self.assertEqual(res["atlas"]["edges"], len(REG["edges"]))

    def test_dropped_edge_fails(self):
        res = G.render_consistency(REG, G.atlas_data(fake_html(REG, drop={"ED10"})))
        self.assertFalse(res["pass"])
        self.assertIn("edge ED10 (esExo>pyro) missing from atlas", res["mismatches"])

    def test_merged_or_repointed_edge_fails(self):
        res = G.render_consistency(REG, G.atlas_data(fake_html(REG, repoint={"EF8": ("hdacFap", "mir206")})))
        self.assertFalse(res["pass"])
        self.assertTrue(any("EF8: drawn hdacFap>mir206" in m for m in res["mismatches"]))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_real_builder_output_is_one_to_one(self):
        with tempfile.TemporaryDirectory() as d:
            html = Path(d) / "a.html"
            ok, msg = PL.Hooks._build(ROOT / "examples/demo/registry-demo-v0.1.json", ROOT / "examples/demo/studies-demo-v0.1.json", html)
            self.assertTrue(ok, msg)
            self.assertTrue(G.render_consistency(REG, G.atlas_data(html.read_text(encoding="utf-8")))["pass"])
            self.assertIn('data-ids', html.read_text(encoding="utf-8"))

    def test_node_and_grade_drift_fail(self):
        data = G.atlas_data(fake_html(REG))
        data["nodes"] = data["nodes"][:-1]
        data["edges"][0]["items"][0]["grade"] = "C"
        mm = G.render_consistency(REG, data)["mismatches"]
        self.assertTrue(any("missing from atlas" in m and m.startswith("node") for m in mm))
        self.assertTrue(any("grade C vs A" in m for m in mm))


class TestGeometryAndRelayout(unittest.TestCase):
    def test_demo_layout_clean(self):
        self.assertTrue(G.geometry(REG)["pass"])

    def test_overlap_resolved_without_touching_evidence(self):
        r = copy.deepcopy(REG)
        next(n for n in r["nodes"] if n["id"] == "radPred")["c"] = 1
        self.assertFalse(G.geometry(r)["pass"])
        new, moves = RL.relayout(r)
        self.assertTrue(G.geometry(new)["pass"])
        self.assertTrue(RL.same_evidence(r, new))
        self.assertTrue(moves)
        self.assertEqual([n["c"] for n in new["nodes"]], [n["c"] for n in r["nodes"]])

    def test_lane_grows_when_full(self):
        r = {"lanes": [{"id": "a", "zh": "A", "en": "A", "r0": 0, "r1": 1}, {"id": "b", "zh": "B", "en": "B", "r0": 1, "r1": 2}],
             "rows": 2, "nodes": [{"id": "x", "status": "evidence", "c": 0, "r": 0}, {"id": "y", "status": "evidence", "c": 0, "r": 0},
                                  {"id": "z", "status": "evidence", "c": 0, "r": 1}],
             "edges": [], "sources": []}
        self.assertEqual(G.geometry(r)["nodeOverlap"], ["x/y"])
        new, moves = RL.relayout(r)
        self.assertTrue(G.geometry(new)["pass"], G.geometry(new))
        self.assertTrue(moves[0]["lane_grown"])
        self.assertEqual((new["lanes"][0]["r1"], new["lanes"][1]["r0"], new["rows"]), (2, 2, 3))
        self.assertEqual(next(n for n in new["nodes"] if n["id"] == "z")["r"], 2)


class TestRegistryDiff(unittest.TestCase):
    def test_grade_change_deletion_merge(self):
        r = copy.deepcopy(REG)
        next(e for e in r["edges"] if e["id"] == "EM1")["grade"] = "A"
        r["edges"] = [e for e in r["edges"] if e["id"] != "EA2"]
        for e in r["edges"]:
            if e["target"] == "mir206":
                e["target"] = "muscExp" if e["source"] != "mir206" else e["target"]
        ef8 = next(e for e in r["edges"] if e["id"] == "EF8")
        r["edges"].remove(ef8)
        r["nodes"] = [n for n in r["nodes"] if n["id"] != "mir206"]
        st = copy.deepcopy(ST)
        st[1]["grade"], st[1]["abstract_only"] = "A", False
        ids = {i["id"] for i in G.registry_diff(REG, r, ST, st)}
        self.assertIn("grade:edge:EM1:B->A", ids)
        self.assertIn("delete:edge:EA2", ids)
        self.assertIn("delete:edge:EF8", ids)
        self.assertIn("merge:node:mir206->muscExp", ids)
        self.assertIn("grade:study:pmid:32754983:B->A", ids)

    def test_explicit_merge_and_removal(self):
        r = copy.deepcopy(REG)
        r["nodes"] = [n for n in r["nodes"] if n["id"] != "gapFap"]
        next(n for n in r["nodes"] if n["id"] == "gapSting")["mergedFrom"] = ["gapFap"]
        ids = [i["id"] for i in G.registry_diff(REG, r)]
        self.assertEqual(ids, ["merge:node:gapFap->gapSting"])
        r2 = copy.deepcopy(REG)
        r2["nodes"] = [n for n in r2["nodes"] if n["id"] != "gapFap"]
        self.assertEqual([i["id"] for i in G.registry_diff(REG, r2)], ["remove:node:gapFap"])
        self.assertEqual(G.registry_diff(REG, copy.deepcopy(REG)), [])


class TestFulltext(unittest.TestCase):
    def test_upgrade_pass_and_rerun_keeps_reviews(self):
        with tempfile.TemporaryDirectory() as d:
            calls, fx = oa_fakes({"32754983"})
            items = FT.run(ST, Path(d), None, "t@example.org", **fx)
            by = {i["study"]: i for i in items}
            self.assertEqual(len(items), 3)
            self.assertEqual(by["pmid:32754983"]["status"], "retrieved")
            self.assertTrue(Path(by["pmid:32754983"]["path"]).exists())
            self.assertIn("B → A", by["pmid:32754983"]["proposal"]["grade"])
            self.assertEqual(by["pmid:33316945"]["status"], "oa_link_only")
            by["pmid:32754983"]["review"] = {"decision": "upgraded", "grade": "A"}
            st = copy.deepcopy(ST)
            st[1]["abstract_only"] = False
            n = len(calls)
            again = FT.run(st, Path(d), items, "t@example.org", only=set(), **fx)
            self.assertEqual(len(calls), n)
            self.assertEqual({i["study"]: i.get("review") for i in again}["pmid:32754983"]["decision"], "upgraded")


# --------------------------------------------------------------- pipeline ----


class Harness:
    """A run directory with a JSON topic config and injected side effects."""

    def __init__(self, d, fail_dbs=(), backoff=(60, 300, 900), browser="off", oa=("32754983",)):
        self.d = Path(d)
        self.fail_dbs = set(fail_dbs)
        self.sleeps = []
        self.t = 1_000_000.0
        self.drop = set()
        self.cfg = {"topic": {"id": "t", "title": {"zh": "合成课题"}}, "contact_email": "t@example.org",
                    "pico": {"population": "synthetic"}, "eligibility": {"include": ["原始研究"], "exclude": ["综述"]},
                    "search": {"databases": {"pubmed": {"queries": [{"id": "q1", "query": "synthetic"}]},
                                             "openalex": {"queries": [{"id": "q1", "query": "synthetic"}]}}},
                    "loop": {"retrieval": {"backoff_seconds": list(backoff)}, "render": {"browser": browser}},
                    "report": {"not_searched_note": "合成测试"}}
        self.save_cfg()
        calls, fx = oa_fakes(set(oa))
        self.hooks = PL.Hooks(
            sleep=self.sleeps.append, now=lambda: self.t,
            search=lambda cfg, run, cap, targets: RS.run_queries(cfg, run, cap, None, targets, runner=self.runner),
            build=self.build, browser=lambda *a: None,
            fulltext=lambda studies, outdir, old, only, email: FT.run(studies, outdir, old, email, only, **fx),
            verify_refs=lambda p, email: [{"id": s["id"], "status": "OK"} for s in json.loads(Path(p).read_text())["sources"]])
        self.run_dir = self.d / "run"

    def save_cfg(self):
        self.cfg_path = self.d / "topic-v0.1.json"
        self.cfg_path.write_text(json.dumps(self.cfg, ensure_ascii=False), encoding="utf-8")

    def runner(self, db, q, cap, email):
        if db in self.fail_dbs:
            raise C.HttpError(429, "https://api.openalex.org/works", "rate limited")
        recs = [C.make_record(db, pmid=s["pmid"], doi=s["doi"], title=s["title"], year=s["year"], source_query="q1") for s in ST]
        recs.append(C.make_record(db, pmid="99999991", doi="10.99999/synthetic.x", title="Synthetic unrelated record", year=2020))
        return len(recs), recs

    def build(self, reg_p, st_p, html_p):
        Path(html_p).write_text(fake_html(json.loads(Path(reg_p).read_text()), drop=self.drop), encoding="utf-8")
        return True, ""

    def go(self, **kw):
        return PL.advance(str(self.cfg_path), str(self.run_dir), self.hooks, **kw)

    def ok(self, cid, **kw):
        return PL.approve(str(self.run_dir), cid, kw.pop("by", "测试审批人"), **kw)

    def pending(self, st):
        return [c["id"] for c in st["checkpoints"] if c["status"] == "pending"]

    def fill_sheet(self):
        sheet = self.run_dir / "screening-v0.1.csv"
        by = {s["pmid"]: s for s in ST}
        rows = SH.read_sheet(sheet)
        for r in rows:
            s = by.get(r["pmid"])
            if s:
                r.update(ta_decision="include", ft_decision="include", grade=s["grade"], abstract_only="yes" if s["abstract_only"] else "no")
            else:
                r.update(ta_decision="exclude", ta_reason="合成无关记录")
        SH.write_sheet(sheet, rows)

    def to_extraction(self):
        self.ok(self.pending(self.go())[0], decision="approve")
        self.go()
        self.fill_sheet()
        return self.go()

    def path(self, name):
        return self.run_dir / name

    def rw(self, name, fn):
        p = self.path(name)
        obj = json.loads(p.read_text(encoding="utf-8"))
        obj = fn(obj) or obj
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


class TestPipelineRetrieval(unittest.TestCase):
    def test_freeze_then_bounded_retries_then_human_decision(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, fail_dbs={"openalex"})
            st = h.go()
            self.assertEqual((st["status"], st["stage"]), ("needs_approval", "freeze"))
            self.assertTrue(h.path("pending-approval-v0.1.md").exists())
            self.assertIn("pipeline-v0.1.py approve", h.path("pending-approval-v0.1.md").read_text(encoding="utf-8"))
            self.assertFalse(h.path("search-log-v0.1.json").exists(), "nothing is searched before the freeze")
            h.ok("freeze-1", decision="approve")
            st = h.go()
            self.assertEqual(h.sleeps, [60, 300, 900])
            r = st["gates"]["retrieval"]
            self.assertEqual((r["status"], r["retries_used"], len(r["attempts"])), ("escalated", 3, 4))
            self.assertEqual(h.pending(st), ["gate-retrieval-1"])
            cp = st["checkpoints"][-1]
            self.assertEqual(cp["options"], ["skip", "change_query", "switch_source"])
            self.assertIn("HTTP 429", cp["items"][0]["zh"])
            with self.assertRaises(SystemExit):
                h.ok("gate-retrieval-1", decision="approve")
            h.ok("gate-retrieval-1", item=["openalex=skip"], note="无 OpenAlex key")
            st = h.go()
            self.assertEqual(st["gates"]["retrieval"]["status"], "pass_with_skips")
            self.assertEqual((st["status"], st["stage"]), ("needs_agent_work", "screening"))
            self.assertEqual(len(h.sleeps), 3, "no further retries after the decision")
            self.assertIn("openalex", RP.gates_section(st))

    def test_change_query_requires_new_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, fail_dbs={"openalex"}, backoff=(0,))
            h.go()
            h.ok("freeze-1", decision="approve")
            h.go()
            h.ok("gate-retrieval-1", item=["openalex=change_query"])
            st = h.go()
            self.assertEqual(st["status"], "needs_agent_work")
            self.assertIn("openalex", st["todo"][0])
            h.cfg["search"]["databases"]["openalex"]["queries"][0]["query"] = "synthetic AND better"
            h.save_cfg()
            h.fail_dbs.clear()
            st = h.go()
            self.assertEqual(h.pending(st), ["freeze-2"])
            h.ok("freeze-2", decision="approve")
            st = h.go()
            self.assertEqual(st["gates"]["retrieval"]["status"], "pass")
            self.assertEqual(st["gates"]["retrieval"]["retries_used"], 0)

    def test_no_wait_resumes_at_due_time(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, fail_dbs={"openalex"})
            h.go()
            h.ok("freeze-1", decision="approve")
            st = h.go(no_wait=True)
            self.assertEqual(st["status"], "waiting_retry")
            self.assertEqual(h.sleeps, [])
            st = h.go(no_wait=True)
            self.assertEqual((st["status"], st["gates"]["retrieval"]["retries_used"]), ("waiting_retry", 0))
            h.t += 61
            st = h.go(no_wait=True)
            self.assertEqual(st["gates"]["retrieval"]["retries_used"], 1)
            self.assertEqual(st["status"], "waiting_retry")


class TestPipelineEndToEnd(unittest.TestCase):
    def test_full_run_with_fulltext_loop_and_approvals(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d)
            st = h.to_extraction()
            self.assertEqual((st["stage"], st["status"]), ("extraction", "needs_agent_work"))
            self.assertTrue(h.path("studies-v0.1.json").exists())
            shutil.copy(ROOT / "examples/demo/registry-demo-v0.1.json", h.path("registry-v0.1.json"))
            st = h.go()
            self.assertEqual(st["gates"]["coverage"]["status"], "pass")
            self.assertEqual(st["gates"]["render"]["status"], "pass")
            self.assertEqual((st["stage"], st["status"]), ("fulltext", "needs_agent_work"))
            ft = json.loads(h.path("fulltext-v0.1.json").read_text())
            self.assertEqual([i["study"] for i in ft["items"] if i["status"] == "retrieved"], ["pmid:32754983"])

            def review(o):
                next(i for i in o["items"] if i["study"] == "pmid:32754983")["review"] = {"decision": "upgraded", "grade": "A"}
            h.rw("fulltext-v0.1.json", review)
            st = h.go()
            self.assertIn("abstract_only 仍为 true", st["todo"][0])

            def upgrade(studies):
                s = next(x for x in studies if x["pmid"] == "32754983")
                s["abstract_only"], s["grade"] = False, "A"
            h.rw("studies-v0.1.json", upgrade)
            st = h.go()
            self.assertEqual(st["outer_loop"], 2)
            self.assertEqual((st["stage"], st["status"]), ("changes", "needs_approval"))
            cp = st["checkpoints"][-1]
            self.assertEqual([i["id"] for i in cp["items"]], ["grade:study:pmid:32754983:B->A"])
            h.ok(cp["id"], decision="approve")
            st = h.go()
            self.assertEqual((st["stage"], st["status"]), ("report", "needs_agent_work"))
            self.assertEqual(st["outer_loop"], 2)
            draft = h.path("report-draft-v0.1.md").read_text(encoding="utf-8")
            self.assertIn("## 流程闸门与人工审批", draft)
            self.assertIn("重新提取后升级 1 篇", draft)
            h.path("report-v0.1.md").write_text("# 合成报告\n\n## 结论先说\n\n- 【事实】合成。\n", encoding="utf-8")
            st = h.go()
            self.assertEqual(h.pending(st), ["release-1"])
            self.assertIn(RP.GATE_BEGIN, h.path("report-v0.1.md").read_text(encoding="utf-8"))
            h.ok("release-1", decision="approve")
            st = h.go()
            self.assertEqual(st["status"], "done")
            self.assertFalse(h.path("pending-approval-v0.1.json").exists())
            self.assertEqual(V.validate(st, STATE_SCHEMA), [])
            report = h.path("report-v0.1.md").read_text(encoding="utf-8")
            self.assertEqual(h.go()["status"], "done", "idempotent once released")
            self.assertEqual(h.path("report-v0.1.md").read_text(encoding="utf-8"), report, "gate block is deterministic")

            h.rw("registry-v0.1.json", lambda r: r["edges"][0].update(label={"zh": "改了"}))
            st = h.go()
            self.assertEqual((st["status"], st["checkpoints"][-1]["gate"]), ("needs_approval", "outer_loop"))

    def test_release_approval_is_bound_to_exact_files(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, oa=())
            h.to_extraction()
            shutil.copy(ROOT / "examples/demo/registry-demo-v0.1.json", h.path("registry-v0.1.json"))
            st = h.go()
            self.assertEqual(st["stage"], "report")
            h.path("report-v0.1.md").write_text("# 报告\n", encoding="utf-8")
            st = h.go()
            self.assertEqual(h.pending(st), ["release-1"])
            h.path("report-v0.1.md").write_text(h.path("report-v0.1.md").read_text(encoding="utf-8") + "\n补一句。\n", encoding="utf-8")
            h.ok("release-1", decision="approve")
            st = h.go()
            self.assertEqual(h.pending(st), ["release-2"], "approval of an older version does not release edited files")


class TestPipelineGates(unittest.TestCase):
    def test_coverage_rounds_then_escalation(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d)
            h.to_extraction()
            h.path("registry-v0.1.json").write_text(json.dumps(drop_study_33316945(), ensure_ascii=False))
            st = h.go()
            self.assertIn("第 1/2 轮", st["message"])
            self.assertEqual(h.go()["gates"]["coverage"]["rounds_used"], 0, "no new round without new extraction")
            reasons = ["布局冲突", "layout crowded"]
            for i, why in enumerate(reasons):
                h.rw("registry-v0.1.json", lambda r: r.update(unmapped=[{"pmid": "33316945", "category": "insufficient", "reason": why}]))
                st = h.go()
                if i < 1:
                    self.assertIn("第 2/2 轮", st["message"])
            self.assertEqual((st["status"], h.pending(st)), ("needs_approval", ["gate-coverage-1"]))
            h.ok("gate-coverage-1", decision="retry")
            st = h.go()
            self.assertEqual((st["status"], st["gates"]["coverage"]["rounds_used"]), ("needs_agent_work", 0))
            h.rw("registry-v0.1.json", lambda r: r.update(unmapped=[{"pmid": "33316945", "category": "background", "reason": "仅细胞系表型背景，无机制读出"}]))
            st = h.go()
            self.assertEqual(st["gates"]["coverage"]["status"], "pass")
            self.assertEqual(st["gates"]["coverage"]["result"]["excused"], 1)

    def test_render_mismatch_relayouts_then_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d)
            h.to_extraction()
            shutil.copy(ROOT / "examples/demo/registry-demo-v0.1.json", h.path("registry-v0.1.json"))
            h.drop = {"ED10"}
            st = h.go()
            rg = st["gates"]["render"]
            self.assertEqual((rg["status"], rg["relayouts_used"], len(rg["attempts"])), ("escalated", 3, 4))
            self.assertEqual(h.pending(st), ["gate-render-1"])
            self.assertTrue(any("ED10" in i["zh"] for i in st["checkpoints"][-1]["items"]))
            reg = json.loads(h.path("registry-v0.1.json").read_text())
            self.assertEqual(len(reg["edges"]), len(REG["edges"]), "edges are never dropped to pass the gate")
            h.drop = set()
            h.ok("gate-render-1", decision="retry")
            st = h.go()
            self.assertEqual(st["gates"]["render"]["status"], "pass")

    def test_layout_conflict_fixed_by_relayout(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, oa=())
            h.to_extraction()
            r = copy.deepcopy(REG)
            next(n for n in r["nodes"] if n["id"] == "doxOut").update(c=2, r=9.55)
            h.path("registry-v0.1.json").write_text(json.dumps(r, ensure_ascii=False))
            st = h.go()
            rg = st["gates"]["render"]
            self.assertEqual((rg["status"], rg["relayouts_used"]), ("pass", 1))
            self.assertTrue(h.path("registry-prelayout-1-v0.1.json").exists())
            self.assertTrue(G.geometry(json.loads(h.path("registry-v0.1.json").read_text()))["pass"])

    def test_edge_deletion_and_merge_need_item_by_item_approval(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d, oa=())
            h.to_extraction()
            shutil.copy(ROOT / "examples/demo/registry-demo-v0.1.json", h.path("registry-v0.1.json"))
            st = h.go()
            self.assertEqual(st["stage"], "report")

            def edit(r):
                r["edges"] = [e for e in r["edges"] if e["id"] != "EA2"]
                r["nodes"] = [n for n in r["nodes"] if n["id"] != "radEndo"]
                next(n for n in r["nodes"] if n["id"] == "gapSting")["mergedFrom"] = ["gapX"]
            h.rw("registry-v0.1.json", edit)
            st = h.go()
            cp = st["checkpoints"][-1]
            self.assertEqual(cp["kind"], "changes")
            self.assertEqual(sorted(i["id"] for i in cp["items"]), ["delete:edge:EA2", "merge:node:gapX->gapSting", "remove:node:radEndo"])
            h.ok(cp["id"], decision="approve", item=["delete:edge:EA2=reject"])
            st = h.go()
            self.assertEqual(st["status"], "needs_agent_work")
            self.assertIn("delete:edge:EA2", st["todo"][0])

            def revert(r):
                r["edges"].insert(1, next(e for e in REG["edges"] if e["id"] == "EA2"))
                r["nodes"].insert(2, next(n for n in REG["nodes"] if n["id"] == "radEndo"))
            h.rw("registry-v0.1.json", revert)
            st = h.go()
            self.assertEqual(st["gates"]["changes"]["status"], "pass")
            self.assertIn("驳回 1 项", RP.gates_section(st))

    def test_approve_validation(self):
        with tempfile.TemporaryDirectory() as d:
            h = Harness(d)
            h.go()
            for bad in (dict(decision="maybe"), dict(decision=None), dict(decision="approve", item=["nope=approve"])):
                with self.assertRaises(SystemExit):
                    h.ok("freeze-1", **bad)
            with self.assertRaises(SystemExit):
                h.ok("freeze-1", decision="approve", by=" ")
            h.ok("freeze-1", decision="approve")
            with self.assertRaises(SystemExit):
                h.ok("freeze-1", decision="approve")


if __name__ == "__main__":
    unittest.main()
