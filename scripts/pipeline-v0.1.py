#!/usr/bin/env python3
"""MechAtlas gated pipeline: bounded retry loops + human approval checkpoints, resumable from a run-state file.

Each `run` advances as far as it can, then stops with a clear reason and exits:
   0 done (release approved) · 10 waiting for human approval · 11 waiting for agent work (screening, extraction…)
  12 waiting for a retry backoff (--no-wait) · 1 error
Re-run the same command after the approval / work is done; finished stages are not repeated.

Stages and gates
  freeze      checkpoint (a): search strategy + inclusion/exclusion criteria are frozen by a human
  retrieval   every database must return >= 1 record without error; failing queries are retried with growing
              backoff (default 60 s, 300 s, 900 s); then checkpoint (b): skip / change_query / switch_source per database
  dedupe      automatic (DOI -> PMID -> title), screening sheet refreshed, judgments kept
  screening   agent work: every row decided, every exclusion has a reason, every inclusion graded
  outer loop (max loop.max_outer_loops, default 2):
    extraction  schema + evidence contract (automatic); coverage gate: every included study maps to >= 1 edge or has
                registry.unmapped[] with a scientific reason, mapped ratio >= min; re-extract rounds (default 2) -> (b)
    changes     checkpoint (c): every grade change, edge deletion, node merge/removal vs the approved baseline, item by item
    render      build atlas, compare atlas DATA (and the browser DOM) one-to-one with the registry, static + browser
                layout checks; up to 3 automatic re-layouts (rows only, never evidence) -> (b)
    fulltext    open-access full text for abstract-only studies; agent re-extracts; upgrades trigger the next outer loop
  refs        verify-refs: every PMID/DOI resolves with a matching title (automatic)
  report      PRISMA + report draft; the gate summary block in report-v0.1.md is kept in sync automatically
  release     checkpoint (d): nothing is published before a human approves the exact files

Files in the run directory: run-state-v0.1.json (machine-readable, schema/run-state-v0.1.schema.json),
pending-approval-v0.1.json / .md (what a human must decide, and the exact command to record it).

Examples:
  python3 scripts/pipeline-v0.1.py run config/my-topic-v0.1.yaml
  python3 scripts/pipeline-v0.1.py status runs/my-topic
  python3 scripts/pipeline-v0.1.py approve runs/my-topic --checkpoint freeze-1 --decision approve --by "张三"
  python3 scripts/pipeline-v0.1.py approve runs/my-topic --checkpoint gate-retrieval-1 --item openalex=skip --by "张三" --note "无 key"
  python3 scripts/pipeline-v0.1.py approve runs/my-topic --checkpoint changes-1 --decision approve --item delete:edge:E7=reject --by "张三"
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str):
    p = ROOT / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


C = _load("scripts/common-v0.1.py")
G = _load("scripts/gates-v0.1.py")
RS = _load("scripts/run-search-v0.1.py")
SH = _load("scripts/screen-sheet-v0.1.py")
V = _load("scripts/validate-registry-v0.1.py")
RL = _load("scripts/relayout-v0.1.py")

SCHEMA = "mechatlas-run-state-v0.1"
EXIT = {"done": 0, "needs_approval": 10, "needs_agent_work": 11, "waiting_retry": 12}
DEFAULTS = {
    "max_outer_loops": 2,
    "retrieval": {"max_retries": 3, "backoff_seconds": [60, 300, 900], "fail_on_zero_hits": True},
    "coverage": {"max_reextract_rounds": 2, "min_mapped_ratio": 0.5},
    "render": {"max_relayout_attempts": 3, "browser": "auto"},
    "fulltext": {"enabled": True},
    "refs": {"max_retries": 3, "backoff_seconds": [60, 300, 900]},
}
OPTIONS = {
    "freeze": ["approve", "reject"],
    "release": ["approve", "reject"],
    "changes": ["approve", "reject"],
    "gate_exhausted:retrieval": ["skip", "change_query", "switch_source"],
    "gate_exhausted": ["accept", "retry"],
}
RP = _load("scripts/report-v0.1.py")
GATE_BEGIN, GATE_END = RP.GATE_BEGIN, RP.GATE_END


class Stop(Exception):
    def __init__(self, status: str, message: str, todo: list[str] | None = None):
        super().__init__(message)
        self.status, self.message, self.todo = status, message, todo or []


def settings(cfg: dict) -> dict:
    out = copy.deepcopy(DEFAULTS)
    for k, v in (cfg.get("loop") or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def now_iso(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(t if t is not None else time.time(), dt.timezone.utc).isoformat(timespec="seconds")


class Paths:
    def __init__(self, run: Path):
        self.run = run
        j = lambda n: run / n
        self.state, self.pending, self.pending_md = j("run-state-v0.1.json"), j("pending-approval-v0.1.json"), j("pending-approval-v0.1.md")
        self.search_log, self.dedupe_stats, self.sheet = j("search-log-v0.1.json"), j("dedupe-stats-v0.1.json"), j("screening-v0.1.csv")
        self.studies, self.registry, self.coverage = j("studies-v0.1.json"), j("registry-v0.1.json"), j("coverage-v0.1.json")
        self.html, self.validation, self.render = j("atlas-v0.1.html"), j("validation-v0.1.json"), j("render-gate-v0.1.json")
        self.fulltext, self.fulltext_dir, self.changes = j("fulltext-v0.1.json"), j("fulltext"), j("changes-v0.1.json")
        self.base_registry, self.base_studies = j("baseline/registry-baseline-v0.1.json"), j("baseline/studies-baseline-v0.1.json")
        self.refs, self.prisma_json, self.prisma_md, self.prisma_svg = j("verify-refs-v0.1.json"), j("prisma-v0.1.json"), j("prisma-v0.1.md"), j("prisma-v0.1.svg")
        self.report_draft, self.report = j("report-draft-v0.1.md"), j("report-v0.1.md")


# ------------------------------------------------------------- hooks ----


class Hooks:
    """Side effects, injectable for offline tests."""

    def __init__(self, **kw):
        self.sleep = kw.get("sleep", time.sleep)
        self.now = kw.get("now", time.time)
        self.search = kw.get("search", self._search)
        self.build = kw.get("build", self._build)
        self.browser = kw.get("browser", self._browser)
        self.fulltext = kw.get("fulltext", self._fulltext)
        self.verify_refs = kw.get("verify_refs", self._verify_refs)

    @staticmethod
    def _search(cfg, run, cap, targets):
        log = RS.run_queries(cfg, run, cap, None, targets)
        if targets is None:
            log += RS.import_exports(cfg, run)
        return log

    @staticmethod
    def _build(registry, studies, html):
        p = subprocess.run(["node", str(ROOT / "scripts/build-atlas-v0.1.mjs"), "--registry", str(registry), "--studies", str(studies),
                            "--out", str(html)], capture_output=True, text=True)
        return p.returncode == 0, (p.stderr or p.stdout).strip()[-2000:]

    @staticmethod
    def _browser(html, registry, studies, out):
        """Real-click verifier; None when Playwright is not installed."""
        probe = subprocess.run(["node", "-e", "import('playwright').then(()=>process.exit(0),()=>process.exit(1))"], cwd=ROOT, capture_output=True)
        if probe.returncode != 0:
            return None
        Path(out).unlink(missing_ok=True)
        p = subprocess.run(["node", str(ROOT / "scripts/verify-atlas-v0.1.mjs"), "--html", str(html), "--registry", str(registry),
                            "--studies", str(studies), "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
        if Path(out).exists():
            return json.loads(Path(out).read_text(encoding="utf-8"))
        return {"passed": 0, "failed": 1, "pageErrors": [], "checks": [
            {"name": "verify-atlas crashed", "pass": False, "detail": (p.stderr or p.stdout).strip()[-300:]}]}

    @staticmethod
    def _fulltext(studies, outdir, old, only, email):
        return _load("scripts/fulltext-oa-v0.1.py").run(studies, outdir, old, email, only)

    @staticmethod
    def _verify_refs(registry_path, email):
        vr = _load("scripts/verify-refs-v0.1.py")
        return vr.verify(vr.load_items(str(registry_path)), email)


# ------------------------------------------------------------- state ----


def new_state(cfg: dict, cfg_path: str) -> dict:
    t = now_iso()
    return {"schema": SCHEMA, "topic": cfg["topic"]["id"], "config": str(cfg_path), "created": t, "updated": t,
            "status": "running", "stage": "freeze", "message": "", "todo": [], "outer_loop": 1,
            "settings": settings(cfg), "gates": {}, "checkpoints": [], "events": []}


def load_state(P: Paths) -> dict | None:
    return json.loads(P.state.read_text(encoding="utf-8")) if P.state.exists() else None


def save_state(P: Paths, st: dict) -> None:
    st["updated"] = now_iso()
    P.run.mkdir(parents=True, exist_ok=True)
    P.state.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    write_pending(P, st)


def event(st: dict, stage: str, msg: str, **detail) -> None:
    st["events"].append({"at": now_iso(), "stage": stage, "outer_loop": st.get("outer_loop", 1), "msg": msg, **detail})


def gate(st: dict, name: str) -> dict:
    return st["gates"].setdefault(name, {"status": "pending"})


def find_cp(st: dict, kind: str, fp: str, gate_name: str | None = None) -> dict | None:
    return next((c for c in reversed(st["checkpoints"]) if c["kind"] == kind and c["fingerprint"] == fp
                 and c.get("gate") == gate_name), None)


def open_cp(st: dict, kind: str, fp: str, title: str, items: list[dict], gate_name: str | None = None, why: str = "") -> dict:
    cp = find_cp(st, kind, fp, gate_name)
    if cp:
        return cp
    for old in st["checkpoints"]:
        if old["kind"] == kind and old.get("gate") == gate_name and old["status"] == "pending":
            old["status"] = "superseded"
            event(st, kind, f"checkpoint {old['id']} superseded (inputs changed)", checkpoint=old["id"])
    n = 1 + sum(1 for c in st["checkpoints"] if c["kind"] == kind and c.get("gate") == gate_name)
    cid = f"{'gate-' + gate_name if kind == 'gate_exhausted' else kind}-{n}"
    opts = OPTIONS.get(f"{kind}:{gate_name}") or OPTIONS[kind]
    cp = {"id": cid, "kind": kind, "gate": gate_name, "fingerprint": fp, "status": "pending", "created": now_iso(),
          "outer_loop": st.get("outer_loop", 1), "title_zh": title, "why_zh": why, "options": opts, "items": items,
          "resolution": None}
    st["checkpoints"].append(cp)
    event(st, kind, f"checkpoint {cid} opened", checkpoint=cid)
    return cp


def write_pending(P: Paths, st: dict) -> None:
    pend = [c for c in st["checkpoints"] if c["status"] == "pending"]
    if not pend:
        for p in (P.pending, P.pending_md):
            p.unlink(missing_ok=True)
        return
    P.pending.write_text(json.dumps({"schema": "mechatlas-pending-approval-v0.1", "run": str(P.run), "topic": st["topic"],
                                     "since": pend[0]["created"], "checkpoints": pend}, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 待人工审批 · {st['topic']}", "", "流水线已暂停，下列事项需要你（人）做决定。Agent 不得代为批准。", ""]
    for c in pend:
        L += [f"## {c['id']}：{c['title_zh']}", ""]
        if c.get("why_zh"):
            L += [c["why_zh"], ""]
        if c["items"]:
            L += ["| 条目 | 内容 |", "|---|---|"]
            L += [f"| `{i['id']}` | {i.get('zh', '')} |" for i in c["items"]]
            L.append("")
        L.append(f"可选决定：{' / '.join('`' + o + '`' for o in c['options'])}")
        ex = f"python3 scripts/pipeline-v0.1.py approve {P.run} --checkpoint {c['id']} --by <你的名字>"
        if c["kind"] == "gate_exhausted" and c.get("gate") == "retrieval":
            ex += " " + " ".join(f"--item {i['id']}=skip" for i in c["items"][:2]) + " --note <理由>"
        elif c["kind"] == "changes":
            ex += " --decision approve" + (f" --item {c['items'][0]['id']}=reject" if c["items"] else "")
        else:
            ex += f" --decision {c['options'][0]}"
        L += ["", "记录决定：", "", "```bash", ex, f"python3 scripts/pipeline-v0.1.py run {st['config']} --run-dir {P.run}", "```", ""]
    P.pending_md.write_text("\n".join(L), encoding="utf-8")


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _wait(st: dict, g: dict, delays: list, hooks: Hooks, no_wait: bool, label: str) -> None:
    """Growing backoff before retry number g['retries_used'] + 1. With --no-wait, stop and resume later."""
    i = g.get("retries_used", 0)
    delay = float(delays[min(i, len(delays) - 1)]) if delays else 0.0
    due = g.get("next_retry_at") or hooks.now() + delay
    g["next_retry_at"] = due
    left = due - hooks.now()
    if left > 0 and no_wait:
        raise Stop("waiting_retry", f"{label}: retry {i + 1} due at {now_iso(due)} (in {left:.0f} s); run again then")
    if left > 0:
        C.log(f"{label}: retry {i + 1} in {left:.0f} s")
        hooks.sleep(left)
    g["next_retry_at"] = None


# ------------------------------------------------------------ stages ----


def stage_freeze(ctx) -> None:
    st, cfg = ctx["st"], ctx["cfg"]
    fp = G.freeze_fingerprint(cfg)
    g = gate(st, "freeze")
    cp = find_cp(st, "freeze", fp)
    if cp and cp["status"] == "resolved":
        if cp["resolution"]["decision"] == "approve":
            if g.get("fingerprint") != fp:
                event(st, "freeze", "search strategy frozen", fingerprint=fp, checkpoint=cp["id"])
            g.update(status="pass", fingerprint=fp, checkpoint=cp["id"])
            return
        raise Stop("needs_agent_work", f"检索策略/纳排标准被驳回（{cp['id']}）：按审批意见修改 {st['config']} 后重跑",
                   [f"审批意见：{cp['resolution'].get('note') or '（无）'}"])
    g.update(status="pending", fingerprint=fp)
    items = [{"id": "eligibility", "zh": "纳入：" + "；".join((cfg.get("eligibility") or {}).get("include") or [])
              + "<br>排除：" + "；".join((cfg.get("eligibility") or {}).get("exclude") or [])}]
    for db, q in RS.planned(cfg):
        items.append({"id": f"{db}/{q.get('id', 'q1')}", "zh": f"`{q.get('query') or q.get('must')}`"})
    for ex in (cfg.get("search") or {}).get("manual_exports") or []:
        items.append({"id": f"{ex.get('db', ex['format'])}/{ex.get('id')}", "zh": f"手动导出 `{ex['path']}`：`{ex.get('query', '')}`"})
    prev = [c for c in st["checkpoints"] if c["kind"] == "freeze" and c["status"] == "resolved"]
    open_cp(st, "freeze", fp, "冻结检索策略与纳入/排除标准", items,
            why="检索开始前，请确认 PICO、纳入/排除标准与每条检索式。" + ("（配置自上次批准后已改动，需要重新批准。）" if prev else ""))
    raise Stop("needs_approval", "检索策略与纳排标准待人工冻结（checkpoint a）")


def stage_retrieval(ctx) -> None:
    st, cfg, P, hooks, s = ctx["st"], ctx["cfg"], ctx["P"], ctx["hooks"], ctx["s"]["retrieval"]
    fp = st["gates"]["freeze"]["fingerprint"]
    g = gate(st, "retrieval")
    if g.get("status") in ("pass", "pass_with_skips") and g.get("fingerprint") == fp:
        return
    cap = ctx["cap"]
    if g.get("fingerprint") != fp or not P.search_log.exists():
        keep = {db: d for db, d in (g.get("decisions") or {}).items() if d["decision"] == "skip"}
        g.clear()
        g.update(status="running", fingerprint=fp, retries_used=0, attempts=[], decisions=keep, next_retry_at=None)
        log = hooks.search(cfg, P.run, cap, None)
        _write(P.search_log, log)
        g["attempts"].append({"attempt": 0, "at": now_iso(), "targets": "all", "failing": _fail_ids(log, s, g)})
        event(st, "retrieval", f"searched {len(log)} queries")
    log = _json(P.search_log)
    while True:
        excused = {db for db, d in g["decisions"].items() if d["decision"] == "skip"}
        fails = G.retrieval_failures(log, s.get("fail_on_zero_hits", True), excused)
        g["failing"] = fails
        if not fails:
            g["status"] = "pass_with_skips" if excused else "pass"
            g["skipped"] = sorted(excused)
            stats = RS.dedupe_and_sheet(cfg, P.run, log)
            g["dedupe"] = stats
            event(st, "retrieval", f"retrieval gate {g['status']}", skipped=sorted(excused))
            return
        retry = [f for f in fails if not f["manual"]]
        if retry and g["retries_used"] < int(s["max_retries"]):
            _save(ctx)
            _wait(st, g, s["backoff_seconds"], hooks, ctx["no_wait"], "retrieval")
            targets = {(f["db"], f["query_id"]) for f in retry}
            new = hooks.search(cfg, P.run, cap, targets)
            log = RS.merge_log(log, new)
            _write(P.search_log, log)
            g["retries_used"] += 1
            g["attempts"].append({"attempt": g["retries_used"], "at": now_iso(), "targets": sorted(f"{a}/{b}" for a, b in targets),
                                  "failing": _fail_ids(log, s, g)})
            event(st, "retrieval", f"retry {g['retries_used']}/{s['max_retries']}", targets=sorted(f"{a}/{b}" for a, b in targets))
            continue
        dbs = sorted({f["db"] for f in fails})
        cfp = G.fingerprint({"freeze": fp, "fails": sorted(f"{f['db']}/{f['query_id']}:{f['reason']}" for f in fails)})
        items = [{"id": db, "zh": f"{db}：" + "；".join(f"{f['query_id']} {('0 条命中' if f['reason'] == 'zero_hits' else '错误 ' + str(f['detail'])[:120])}"
                                                      for f in fails if f["db"] == db)
                  + (f"（已自动重试 {g['retries_used']} 次）" if any(not f["manual"] for f in fails if f["db"] == db) else "（手动导出，重试无意义）")}
                 for db in dbs]
        cp = open_cp(st, "gate_exhausted", cfp, "检索闸门：重试用尽，需决定如何处理失败的数据库", items, "retrieval",
                     why="可选：skip（跳过该库，报告中写明）、change_query（改检索式）、switch_source（换数据源）。不会静默跳过。")
        if cp["status"] == "pending":
            g["status"] = "escalated"
            raise Stop("needs_approval", f"检索闸门重试用尽：{', '.join(dbs)}（checkpoint b）")
        res = cp["resolution"]["items"]
        todo = []
        for db in dbs:
            d = res.get(db)
            g["decisions"][db] = {"decision": d, "checkpoint": cp["id"], "by": cp["resolution"]["by"], "note": cp["resolution"].get("note")}
            if d != "skip":
                todo.append(f"{db}: {d} —— 按人工决定修改 {st['config']}（改检索式或换数据源），改动后需重新冻结检索策略")
        if todo:
            g["status"] = "awaiting_config_change"
            raise Stop("needs_agent_work", "按人工决定修改检索配置", todo)


def _fail_ids(log, s, g):
    ex = {db for db, d in (g.get("decisions") or {}).items() if d["decision"] == "skip"}
    return sorted(f"{f['db']}/{f['query_id']}:{f['reason']}" for f in G.retrieval_failures(log, s.get("fail_on_zero_hits", True), ex))


def stage_screening(ctx) -> None:
    st, P = ctx["st"], ctx["P"]
    rows = SH.read_sheet(P.sheet)
    lo = lambda v: (v or "").strip().lower()
    prob = []
    for r in rows:
        rid, ta, ft = r["record_id"], lo(r.get("ta_decision")), lo(r.get("ft_decision"))
        if ta not in ("include", "exclude", "maybe"):
            prob.append(f"{rid}: ta_decision 未填")
        elif ta == "exclude" and not (r.get("ta_reason") or "").strip():
            prob.append(f"{rid}: 题名摘要排除缺理由")
        elif ta in ("include", "maybe"):
            if ft not in ("include", "exclude", "not_retrieved"):
                prob.append(f"{rid}: ft_decision 未填")
            elif ft == "exclude" and not (r.get("ft_reason") or "").strip():
                prob.append(f"{rid}: 全文排除缺理由")
            elif ft == "include":
                gr = (r.get("grade") or "").strip().upper()
                if gr not in ("A", "B", "C"):
                    prob.append(f"{rid}: 纳入但未分级")
                elif gr == "A" and lo(r.get("abstract_only")) in ("yes", "y", "true", "1"):
                    prob.append(f"{rid}: A 级必须读过全文（abstract_only=yes）")
    g = gate(st, "screening")
    g.update(rows=len(rows), problems=len(prob), included=sum(1 for r in rows if lo(r.get("ft_decision")) == "include"))
    if prob:
        g["status"] = "incomplete"
        raise Stop("needs_agent_work", f"筛查表未完成：{len(prob)} 处（{P.sheet}）", prob[:30])
    g["status"] = "pass"


def _studies_in_sync(P: Paths) -> list[str]:
    inc = SH.included(SH.read_sheet(P.sheet))
    if not P.studies.exists():
        _write(P.studies, inc)
        return []
    have = {G.study_key(s) for s in _json(P.studies)}
    want = {G.study_key(s) for s in inc}
    return [f"筛查表纳入但 studies 中缺：{k}" for k in sorted(want - have)] + [f"studies 中有但筛查表未纳入：{k}" for k in sorted(have - want)]


def evidence_fp(P: Paths) -> str:
    """Registry + studies without layout fields: re-layout does not reset gates, evidence edits do."""
    reg = _json(P.registry)
    strip = {k: v for k, v in reg.items() if k not in ("lanes", "rows", "scaffoldTop")}
    strip["nodes"] = [{k: v for k, v in n.items() if k != "r"} for n in reg["nodes"]]
    return G.fingerprint({"registry": strip, "studies": _json(P.studies)})


def stage_extraction(ctx) -> None:
    st, P, s = ctx["st"], ctx["P"], ctx["s"]["coverage"]
    k = st["outer_loop"]
    sync = _studies_in_sync(P)
    if sync:
        raise Stop("needs_agent_work", "studies-v0.1.json 与筛查表不一致（screen-sheet include 重新导出或手动对齐）", sync[:30])
    if not P.registry.exists():
        raise Stop("needs_agent_work", f"请按 schema/registry-v0.1.schema.json 编写 {P.registry}",
                   ["每篇纳入研究要么进入至少一条证据边，要么在 registry.unmapped[] 写明科学理由（布局不是理由）"])
    reg, studies = _json(P.registry), _json(P.studies)
    errs = [f"registry {e}" for e in V.validate(reg, V.load_schema("registry-v0.1.schema.json"))]
    errs += [f"studies {e}" for e in V.validate(studies, V.load_schema("study-v0.1.schema.json"))]
    errs += V.contract(reg, studies)
    if errs:
        gate(st, "schema").update(status="fail", errors=len(errs))
        raise Stop("needs_agent_work", f"登记表 schema/证据契约未通过：{len(errs)} 处", errs[:30])
    gate(st, "schema").update(status="pass", errors=0)
    cov = G.coverage(studies, reg, float(s["min_mapped_ratio"]))
    _write(P.coverage, cov)
    g = gate(st, "coverage")
    if g.get("loop") != k:
        g.clear()
        g.update(status="pending", loop=k, rounds_used=0, failed_fps=[], history=[])
    fp = evidence_fp(P)
    summary = {k2: cov[k2] for k2 in ("included", "mapped", "excused", "unaccounted", "invalid", "mapped_ratio", "excused_by_category")}
    g.update(result=summary)
    if cov["pass"]:
        if g["status"] != "pass":
            event(st, "extraction", "coverage gate pass", **summary)
        g["status"] = "pass"
        return
    cp = find_cp(st, "gate_exhausted", fp, "coverage")
    if cp and cp["status"] == "resolved":
        if cp["resolution"]["decision"] == "accept":
            g["status"] = "waived"
            return
        if not g.get("retry_granted_by") == cp["id"]:
            g.update(rounds_used=0, failed_fps=[], retry_granted_by=cp["id"])
    todo = [f"未对应：{x['study']} {x.get('title') or ''}"[:160] for x in cov["unaccounted_items"][:20]]
    todo += [f"理由无效：{x['study']} —— {x['why']}" for x in cov["invalid_items"][:20]]
    if cov["mapped_ratio"] < cov["min_ratio"]:
        todo.append(f"进入证据边的比例 {cov['mapped_ratio']:.0%} 低于阈值 {cov['min_ratio']:.0%}：补提取证据边")
    if fp not in g["failed_fps"]:
        g["failed_fps"].append(fp)
        g["history"].append({"at": now_iso(), **summary})
        if len(g["failed_fps"]) > 1:
            g["rounds_used"] += 1
            event(st, "extraction", f"re-extraction round {g['rounds_used']} still below the gate", **summary)
    rounds = int(s["max_reextract_rounds"])
    if g["rounds_used"] < rounds:
        g["status"] = "reextract"
        raise Stop("needs_agent_work", f"提取覆盖闸门未过（第 {g['rounds_used'] + 1}/{rounds} 轮补提取）：详见 {P.coverage}", todo)
    g["status"] = "escalated"
    cp = open_cp(st, "gate_exhausted", fp, f"提取覆盖闸门：{rounds} 轮补提取后仍未通过", [
        {"id": "coverage", "zh": f"纳入 {cov['included']} 篇：进入证据边 {cov['mapped']}（{cov['mapped_ratio']:.0%}，阈值 {cov['min_ratio']:.0%}），"
                                 f"有理由未画 {cov['excused']}，无对应 {cov['unaccounted']}，理由无效 {cov['invalid']}"}],
        "coverage", why="accept：接受当前覆盖并在报告中写明缺口；retry：再给 Agent 一组补提取轮次。")
    raise Stop("needs_approval", f"提取覆盖闸门补提取 {rounds} 轮后仍未通过（checkpoint b，{cp['id']}）", todo)


def stage_changes(ctx) -> None:
    st, P = ctx["st"], ctx["P"]
    reg, studies = _json(P.registry), _json(P.studies)
    g = gate(st, "changes")
    g.setdefault("approved", [])
    g.setdefault("rejected", [])
    if not P.base_registry.exists():
        _write(P.base_registry, reg)
        _write(P.base_studies, studies)
        event(st, "changes", "baseline registry/studies recorded")
    items = G.registry_diff(_json(P.base_registry), reg, _json(P.base_studies), studies)
    _write(P.changes, {"baseline": str(P.base_registry), "items": items})
    while True:
        pending = [i for i in items if i["id"] not in g["approved"]]
        rejected = [i for i in pending if i["id"] in g["rejected"]]
        if rejected:
            g["status"] = "revert_rejected"
            raise Stop("needs_agent_work", "人工驳回的改动仍在登记表中：请撤回", [f"{i['id']}：{i['zh']}" for i in rejected])
        if not pending:
            if items:
                _write(P.base_registry, reg)
                _write(P.base_studies, studies)
                event(st, "changes", f"{len(items)} approved change(s) folded into the baseline")
            g.update(status="pass", last=[i["id"] for i in items])
            return
        cp = open_cp(st, "changes", G.fingerprint(sorted(i["id"] for i in pending)),
                     f"证据改动待审批：{len(pending)} 项（等级变更 / 删边 / 节点合并）", pending,
                     why="逐条决定：approve 或 reject。被驳回的条目需由 Agent 撤回后重跑。")
        if cp["status"] == "pending":
            g["status"] = "awaiting_approval"
            raise Stop("needs_approval", f"{len(pending)} 项证据改动待人工逐条审批（checkpoint c，{cp['id']}）")
        for i in pending:
            (g["approved"] if cp["resolution"]["items"].get(i["id"]) == "approve" else g["rejected"]).append(i["id"])


def stage_render(ctx) -> None:
    st, P, hooks, s = ctx["st"], ctx["P"], ctx["hooks"], ctx["s"]["render"]
    g = gate(st, "render")
    efp = evidence_fp(P)
    if g.get("status") in ("pass", "waived") and g.get("inputs_fp") == G.file_fingerprint(P.registry, P.studies) \
            and P.html.exists() and g.get("html_fp") == G.file_fingerprint(P.html):
        return
    if g.get("evidence_fp") != efp:
        g.clear()
        g.update(status="running", evidence_fp=efp, relayouts_used=0, attempts=[])
    while True:
        n = len(g["attempts"]) + 1
        ok, msg = hooks.build(P.registry, P.studies, P.html)
        reg = _json(P.registry)
        att = {"attempt": n, "at": now_iso(), "build": "ok" if ok else msg[-300:]}
        cons = G.render_consistency(reg, G.atlas_data(P.html.read_text(encoding="utf-8"))) if ok else {"pass": False, "mismatches": ["build failed"]}
        geo = G.geometry(reg)
        val, mode = None, s.get("browser", "auto")
        if mode == "off":
            btxt = "off"
        elif not (ok and cons["pass"] and geo["pass"]):
            btxt = "deferred: static checks failed first"
        else:
            val = hooks.browser(P.html, P.registry, P.studies, P.validation)
            if val is None and mode == "required":
                raise Stop("needs_agent_work", "render.browser=required 但 Playwright 不可用",
                           ["npm install --no-save playwright && npx playwright install chromium"])
            btxt = "skipped: Playwright not installed" if val is None else f"{val['passed']}/{val['passed'] + val['failed']}"
        browser_fail = [c["name"] + (f" — {c['detail']}" if c.get("detail") else "") for c in (val or {}).get("checks", []) if not c["pass"]]
        att.update(consistency=cons["mismatches"][:50], geometry={k: v for k, v in geo.items() if k != "pass" and v},
                   browser=btxt, browser_failed=browser_fail[:30])
        g["attempts"].append(att)
        passed = ok and cons["pass"] and geo["pass"] and not browser_fail
        _write(P.render, {"pass": passed, "attempt": att, "consistency": cons})
        if passed:
            g.update(status="pass", inputs_fp=G.file_fingerprint(P.registry, P.studies), html_fp=G.file_fingerprint(P.html),
                     browser=att["browser"], counts={"registry": cons["registry"], "atlas": cons["atlas"]})
            event(st, "render", f"render gate pass after {n} build(s)", relayouts=g["relayouts_used"])
            return
        if g["relayouts_used"] < int(s["max_relayout_attempts"]):
            new, moves = RL.relayout(reg, val)
            if not RL.same_evidence(reg, new):
                raise RuntimeError("relayout changed evidence (bug)")
            g["relayouts_used"] += 1
            shutil.copy(P.registry, P.run / f"registry-prelayout-{g['relayouts_used']}-v0.1.json")
            _write(P.registry, new)
            att["relayout"] = moves
            event(st, "render", f"re-layout {g['relayouts_used']}/{s['max_relayout_attempts']}", moves=len(moves))
            continue
        cp = find_cp(st, "gate_exhausted", efp, "render")
        if cp and cp["status"] == "resolved":
            if cp["resolution"]["decision"] == "accept":
                g.update(status="waived", inputs_fp=G.file_fingerprint(P.registry, P.studies), html_fp=G.file_fingerprint(P.html))
                return
            if g.get("retry_granted_by") != cp["id"]:
                g.update(relayouts_used=0, retry_granted_by=cp["id"])
                continue
        g["status"] = "escalated"
        problems = att["consistency"] + [f"{k}: {'; '.join(v[:5])}" for k, v in att["geometry"].items()] + att["browser_failed"]
        cp = open_cp(st, "gate_exhausted", efp, f"渲染一致性闸门：{s['max_relayout_attempts']} 次自动重排后仍未通过",
                     [{"id": f"p{i + 1}", "zh": p[:300]} for i, p in enumerate(problems[:40])], "render",
                     why="图谱与登记表不能一一对应或布局仍冲突。accept：接受（报告中写明）；retry：再给 3 次自动重排。不允许删边或合并节点来过闸。")
        raise Stop("needs_approval", f"渲染闸门重排用尽（checkpoint b，{cp['id']}）", problems[:20])


def stage_fulltext(ctx) -> bool:
    """Returns True when upgrades from this loop require another outer loop."""
    st, P, hooks, s = ctx["st"], ctx["P"], ctx["hooks"], ctx["s"]
    g = gate(st, "fulltext")
    g.setdefault("tried", [])
    g.setdefault("by_loop", {})
    k = str(st["outer_loop"])
    if not s["fulltext"].get("enabled", True):
        g["status"] = "disabled"
        return False
    studies = _json(P.studies)
    old = _json(P.fulltext)["items"] if P.fulltext.exists() else []
    cands = {G.study_key(x) for x in studies if x.get("abstract_only")} - set(g["tried"])
    if cands and k not in g["by_loop"]:
        if st["outer_loop"] >= int(s["max_outer_loops"]) + int(st.get("extra_loops_granted", 0)):
            g["not_attempted_cap"] = sorted(cands)
            g["by_loop"][k] = {"tried": 0, "retrieved": [], "not_attempted_cap": len(cands)}
            event(st, "fulltext", f"{len(cands)} abstract-only studies not attempted: outer-loop cap reached")
        else:
            items = hooks.fulltext(studies, P.fulltext_dir, old, cands, ctx["cfg"].get("contact_email"))
            ft = _load("scripts/fulltext-oa-v0.1.py")
            _write(P.fulltext, {"summary": ft.summary(items), "items": items})
            g["tried"] = sorted(set(g["tried"]) | cands)
            got = sorted(i["study"] for i in items if i["study"] in cands and i["status"] == "retrieved")
            g["by_loop"][k] = {"tried": len(cands), "retrieved": got,
                               "oa_link_only": sorted(i["study"] for i in items if i["study"] in cands and i["status"] == "oa_link_only")}
            event(st, "fulltext", f"open-access full text: {len(got)}/{len(cands)} retrieved")
    items = _json(P.fulltext)["items"] if P.fulltext.exists() else []
    this_loop = set((g["by_loop"].get(k) or {}).get("retrieved", []))
    now_abs = {G.study_key(x): x.get("abstract_only") for x in studies}
    todo = []
    for i in items:
        if i["study"] not in this_loop:
            continue
        r = i.get("review") or {}
        if r.get("decision") == "upgraded" and now_abs.get(i["study"]) is not False:
            todo.append(f"{i['study']}: review=upgraded 但 studies 中 abstract_only 仍为 true")
        elif r.get("decision") == "kept_abstract_only" and not (r.get("reason") or "").strip():
            todo.append(f"{i['study']}: kept_abstract_only 需写理由")
        elif r.get("decision") not in ("upgraded", "kept_abstract_only"):
            todo.append(f"{i['study']}: 读 {i.get('path')}，重新提取，在 {P.fulltext.name} 写 review.decision（upgraded / kept_abstract_only）")
    if todo:
        g["status"] = "awaiting_reextraction"
        raise Stop("needs_agent_work", f"全文升级：{len(todo)} 篇开放全文待重新提取（等级改动将进入人工审批）", todo[:30])
    upgraded = sorted(i["study"] for i in items if i["study"] in this_loop and (i.get("review") or {}).get("decision") == "upgraded")
    g["by_loop"].setdefault(k, {})["upgraded"] = upgraded
    g["status"] = "done"
    return bool(upgraded)


def stage_refs(ctx) -> None:
    st, P, hooks, s = ctx["st"], ctx["P"], ctx["hooks"], ctx["s"]["refs"]
    g = gate(st, "refs")
    fp = G.file_fingerprint(P.registry)
    if g.get("status") == "pass" and g.get("fp") == fp:
        return
    if g.get("fp") != fp:
        g.clear()
        g.update(status="running", fp=fp, retries_used=0)
    while True:
        res = hooks.verify_refs(P.registry, ctx["cfg"].get("contact_email"))
        from collections import Counter
        summ = {"checked": len(res), "status": dict(Counter(r["status"] for r in res)), "date": C.today()}
        _write(P.refs, {"summary": summ, "items": res})
        g["summary"] = summ
        bad = [r for r in res if r["status"] != "OK"]
        if not bad:
            g["status"] = "pass"
            return
        if all(r["status"] == "NETWORK_ERROR" for r in bad) and g["retries_used"] < int(s["max_retries"]):
            _save(ctx)
            _wait(st, g, s["backoff_seconds"], hooks, ctx["no_wait"], "verify-refs")
            g["retries_used"] += 1
            continue
        g["status"] = "fail"
        raise Stop("needs_agent_work", f"引用核查未全部 OK：{summ['status']}（绝不编造，查不到就删边并走审批）",
                   [f"{r['id']}: {r['status']} {r.get('note') or ''}"[:200] for r in bad[:30]])


def stage_report(ctx) -> None:
    st, P, cfg = ctx["st"], ctx["P"], ctx["cfg"]
    PR = _load("scripts/prisma-v0.1.py")
    sb = sorted(P.run.glob("snowball-r*-summary*.json"))
    c = PR.counts(_json(P.search_log), _json(P.dedupe_stats) if P.dedupe_stats.exists() else {}, SH.read_sheet(P.sheet),
                  [_json(x) for x in sb] or None)
    _write(P.prisma_json, c)
    P.prisma_md.write_text(PR.to_markdown(c), encoding="utf-8")
    P.prisma_svg.write_text(PR.to_svg(c), encoding="utf-8")
    val = _json(P.validation) if P.validation.exists() else None
    draft = RP.build(cfg, _json(P.registry), _json(P.studies), c, _json(P.refs) if P.refs.exists() else None, val, run_state=st)
    P.report_draft.write_text(draft, encoding="utf-8")
    if not P.report.exists():
        raise Stop("needs_agent_work", f"报告草稿已生成：{P.report_draft}",
                   [f"复制为 {P.report.name}，改写「结论先说」（3–5 条，标【事实】/【关联】/【推测】）；"
                    f"{GATE_BEGIN} 与 {GATE_END} 之间的闸门摘要由流水线自动同步，不要手改"])
    text = P.report.read_text(encoding="utf-8")
    block = RP.gates_block(st)
    if GATE_BEGIN in text and GATE_END in text:
        a, rest = text.split(GATE_BEGIN, 1)
        new = a + block + rest.split(GATE_END, 1)[1]
    else:
        new = text.rstrip("\n") + "\n\n" + block + "\n"
    if new != text:
        P.report.write_text(new, encoding="utf-8")
        event(st, "report", "gate summary synced into report")
    gate(st, "report")["status"] = "pass"


def stage_release(ctx) -> None:
    st, P = ctx["st"], ctx["P"]
    fp = G.file_fingerprint(P.html, P.registry, P.studies, P.report)
    g = gate(st, "release")
    cp = find_cp(st, "release", fp)
    if cp and cp["status"] == "resolved":
        if cp["resolution"]["decision"] == "approve":
            g.update(status="approved", fingerprint=fp, checkpoint=cp["id"], by=cp["resolution"]["by"])
            return
        g["status"] = "rejected"
        raise Stop("needs_agent_work", f"发布被驳回（{cp['id']}）：{cp['resolution'].get('note') or ''}")
    files = [P.html, P.registry, P.studies, P.report, P.prisma_md, P.refs, P.validation]
    items = [{"id": f.name, "zh": f"`{f}`" + ("" if f.exists() else "（不存在）")} for f in files]
    g["status"] = "awaiting_approval"
    open_cp(st, "release", fp, "发布前最终审批", items,
            why="以下文件将对外发布（复制到 docs/、推送或投稿）。批准只对这一版文件有效；文件再改动需要重新批准。")
    raise Stop("needs_approval", "发布前最终审批（checkpoint d）")


def _save(ctx):
    save_state(ctx["P"], ctx["st"])


# ------------------------------------------------------------ driver ----


def advance(cfg_path: str, run_dir: str | None = None, hooks: Hooks | None = None, no_wait: bool = False,
            cap: int | None = None) -> dict:
    cfg = C.load_topic(cfg_path)
    P = Paths(Path(run_dir or f"runs/{cfg['topic']['id']}"))
    st = load_state(P) or new_state(cfg, cfg_path)
    st["settings"] = s = settings(cfg)
    st["config"] = str(cfg_path)
    ctx = {"st": st, "cfg": cfg, "P": P, "hooks": hooks or Hooks(), "s": s, "no_wait": no_wait,
           "cap": cap if cap is not None else int((cfg.get("search") or {}).get("max_per_query", 2000))}
    resume_fulltext = (st["stage"], st["status"]) == ("fulltext", "needs_agent_work")
    st["status"], st["message"], st["todo"] = "running", "", []
    try:
        for name, fn in (("freeze", stage_freeze), ("retrieval", stage_retrieval), ("screening", stage_screening)):
            st["stage"] = name
            fn(ctx)
        loop_cap = int(s["max_outer_loops"]) + int(st.get("extra_loops_granted", 0))
        if st.get("loop_exit_fp") and P.registry.exists() and st["loop_exit_fp"] != G.file_fingerprint(P.registry, P.studies):
            _reopen_loop(st, loop_cap)
        while not st.get("loop_exit_fp"):
            if not resume_fulltext:
                for name, fn in (("extraction", stage_extraction), ("changes", stage_changes), ("render", stage_render)):
                    st["stage"] = name
                    fn(ctx)
            resume_fulltext = False
            st["stage"] = "fulltext"
            if stage_fulltext(ctx) and st["outer_loop"] < loop_cap:
                st["outer_loop"] += 1
                event(st, "loop", f"outer loop {st['outer_loop']}/{loop_cap}: re-extract and re-verify after full-text upgrades")
                continue
            st["loop_exit_fp"] = G.file_fingerprint(P.registry, P.studies)
            event(st, "loop", f"outer loop finished after {st['outer_loop']} loop(s)")
        for name, fn in (("refs", stage_refs), ("report", stage_report), ("release", stage_release)):
            st["stage"] = name
            fn(ctx)
        if P.registry.exists() and st["loop_exit_fp"] != G.file_fingerprint(P.registry, P.studies):
            raise Stop("needs_agent_work", "登记表在最终核查后又被改动：重跑以重新核查")
        st["stage"], st["status"], st["message"] = "done", "done", "发布已获人工批准"
    except Stop as e:
        st["status"], st["message"], st["todo"] = e.status, e.message, e.todo
    save_state(P, st)
    return st


def _reopen_loop(st: dict, loop_cap: int) -> None:
    """Registry/studies changed after the loop finished: re-verify inside the bounded outer loop."""
    if st["outer_loop"] < loop_cap:
        st["outer_loop"] += 1
        st["loop_exit_fp"] = None
        event(st, "loop", f"registry changed after verification: outer loop {st['outer_loop']}/{loop_cap}")
        return
    fp = G.fingerprint({"loop_cap": loop_cap, "after": st["loop_exit_fp"]})
    cp = find_cp(st, "gate_exhausted", fp, "outer_loop")
    if cp and cp["status"] == "resolved" and cp["resolution"]["decision"] == "retry":
        st["extra_loops_granted"] = int(st.get("extra_loops_granted", 0)) + 1
        st["outer_loop"] += 1
        st["loop_exit_fp"] = None
        event(st, "loop", f"extra outer loop granted by {cp['id']}")
        return
    if cp and cp["status"] == "resolved":
        raise Stop("needs_agent_work", "外层循环已达上限且人工未批准加轮：撤回最终核查后的改动")
    open_cp(st, "gate_exhausted", fp, f"外层循环已达上限（{loop_cap} 轮），登记表在最终核查后又被改动",
            [{"id": "outer_loop", "zh": "retry：再允许 1 轮完整的提取—审批—渲染核查；accept 不可用于跳过核查（等同驳回改动）"}], "outer_loop")
    raise Stop("needs_approval", "外层循环上限已到（checkpoint b）")


def approve(run_dir: str, cid: str, by: str, decision: str | None = None, item: list[str] | None = None,
            note: str | None = None) -> dict:
    P = Paths(Path(run_dir))
    st = load_state(P)
    if not st:
        raise SystemExit(f"no run state in {run_dir}")
    cp = next((c for c in st["checkpoints"] if c["id"] == cid), None)
    if not cp:
        raise SystemExit(f"unknown checkpoint {cid}; pending: {[c['id'] for c in st['checkpoints'] if c['status'] == 'pending']}")
    if cp["status"] != "pending":
        raise SystemExit(f"checkpoint {cid} already resolved: {cp['resolution']}")
    if not by or not by.strip():
        raise SystemExit("--by is required (who decided)")
    per = {}
    for it in item or []:
        k, _, v = it.partition("=")
        if k not in {i["id"] for i in cp["items"]}:
            raise SystemExit(f"unknown item {k} in {cid}")
        per[k] = v
    for v in list(per.values()) + ([decision] if decision else []):
        if v not in cp["options"]:
            raise SystemExit(f"decision {v!r} not allowed for {cid}; options: {cp['options']}")
    itemized = cp["kind"] == "changes" or (cp["kind"] == "gate_exhausted" and cp.get("gate") == "retrieval")
    if itemized:
        for i in cp["items"]:
            per.setdefault(i["id"], decision)
        missing = [k for k, v in per.items() if not v]
        if missing:
            raise SystemExit(f"every item needs a decision (use --decision or --item ID=…): missing {missing}")
        decision = decision or ("mixed" if len(set(per.values())) > 1 else next(iter(per.values()), None))
    elif not decision:
        raise SystemExit(f"--decision is required; options: {cp['options']}")
    cp["status"] = "resolved"
    cp["resolution"] = {"decision": decision, "items": per, "by": by.strip(), "note": note, "at": now_iso()}
    event(st, cp["kind"], f"checkpoint {cid} resolved: {decision}", checkpoint=cid, by=by.strip())
    if cp["kind"] == "freeze" and decision == "approve":
        st["gates"].setdefault("freeze", {})["approved_by"] = by.strip()
    save_state(P, st)
    return cp


def status_view(st: dict) -> dict:
    return {"topic": st["topic"], "status": st["status"], "stage": st["stage"], "outer_loop": st["outer_loop"],
            "message": st["message"], "todo": st["todo"],
            "gates": {k: v.get("status") for k, v in st["gates"].items()},
            "pending": [{"id": c["id"], "title": c["title_zh"], "items": len(c["items"]), "options": c["options"]}
                        for c in st["checkpoints"] if c["status"] == "pending"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="advance the pipeline until done or blocked")
    r.add_argument("topic")
    r.add_argument("--run-dir")
    r.add_argument("--no-wait", action="store_true", help="do not sleep through retry backoff; exit 12 and resume later")
    r.add_argument("--max", type=int, help="override max records per query (smoke tests)")
    s = sub.add_parser("status")
    s.add_argument("run_dir")
    a_ = sub.add_parser("approve", help="record a human decision on a pending checkpoint")
    a_.add_argument("run_dir")
    a_.add_argument("--checkpoint", required=True)
    a_.add_argument("--by", required=True, help="who decided (a person, not the agent)")
    a_.add_argument("--decision")
    a_.add_argument("--item", action="append", help="ITEM_ID=DECISION (repeatable)")
    a_.add_argument("--note")
    a = ap.parse_args()
    if a.cmd == "run":
        st = advance(a.topic, a.run_dir, no_wait=a.no_wait, cap=a.max)
        print(json.dumps(status_view(st), ensure_ascii=False, indent=1))
        sys.exit(EXIT.get(st["status"], 1))
    if a.cmd == "status":
        st = load_state(Paths(Path(a.run_dir)))
        if not st:
            sys.exit(f"no run state in {a.run_dir}")
        print(json.dumps(status_view(st), ensure_ascii=False, indent=1))
        return
    cp = approve(a.run_dir, a.checkpoint, a.by, a.decision, a.item, a.note)
    print(json.dumps({"checkpoint": cp["id"], "resolution": cp["resolution"],
                      "next": "python3 scripts/pipeline-v0.1.py run <topic.yaml> --run-dir " + a.run_dir}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
