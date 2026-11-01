"""MechAtlas shared helpers (stdlib only).

Loaded by the other scripts through ``load_common()``; file names carry a version
number, so this module is imported by path rather than by name.

- polite HTTP GET with per-host rate limiting, retries and exponential backoff
- the common record schema (see schema/record-v0.1.schema.json) and normalisers
- JSONL read/write, topic config loading (YAML via PyYAML, or JSON)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.2.1"
ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- HTTP ----
_LAST_CALL: dict[str, float] = {}
MAX_RETRY_WAIT = float(os.environ.get("MECHATLAS_MAX_RETRY_WAIT", "120"))
DEFAULT_INTERVAL = {  # seconds between calls per host (politeness)
    "eutils.ncbi.nlm.nih.gov": 0.34,  # 3 req/s without key; 0.11 with NCBI_API_KEY
    "www.ebi.ac.uk": 0.2,
    "api.openalex.org": 0.12,
    "api.crossref.org": 0.2,
    "api.semanticscholar.org": 1.1,  # unauthenticated shared pool
    "api.biorxiv.org": 0.5,
    "api.clarivate.com": 0.5,
    "api.elsevier.com": 0.2,
}
if os.environ.get("NCBI_API_KEY"):
    DEFAULT_INTERVAL["eutils.ncbi.nlm.nih.gov"] = 0.11


def contact_email(cfg_email: str | None = None) -> str:
    return cfg_email or os.environ.get("MECHATLAS_EMAIL") or "mechatlas@example.org"


def user_agent(email: str | None = None) -> str:
    return f"MechAtlas/{VERSION} (+https://github.com/TUANZIDING/MechAtlas; mailto:{contact_email(email)})"


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             email: str | None = None, retries: int = 5, timeout: int = 60,
             method: str = "GET") -> bytes:
    """GET with rate limiting + retries (429/5xx/network) and exponential backoff."""
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        url = url + ("&" if "?" in url else "?") + q
    host = urllib.parse.urlparse(url).netloc
    h = {"User-Agent": user_agent(email), "Accept": "*/*"}
    if headers:
        h.update(headers)
    delay = 1.5
    for attempt in range(retries + 1):
        wait = DEFAULT_INTERVAL.get(host, 0.2) - (time.time() - _LAST_CALL.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[host] = time.time()
        try:
            req = urllib.request.Request(url, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                sleep = float(ra) if ra and ra.isdigit() else delay
                if sleep > MAX_RETRY_WAIT:  # e.g. a daily budget exhausted: fail fast, don't hang
                    raise HttpError(e.code, url, f"Retry-After {sleep:.0f}s exceeds {MAX_RETRY_WAIT}s; {body}")
                log(f"  {host}: HTTP {e.code}, retry {attempt + 1}/{retries} in {sleep:.1f}s")
                time.sleep(sleep)
                delay = min(delay * 2, 60)
                continue
            raise HttpError(e.code, url, body)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                log(f"  {host}: {e!r}, retry {attempt + 1}/{retries} in {delay:.1f}s")
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise
    raise RuntimeError("unreachable")


def http_json(url: str, params: dict | None = None, **kw):
    return json.loads(http_get(url, params, **kw).decode("utf-8"))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------- records ----
RECORD_FIELDS = ["id", "source_db", "source_query", "raw_id", "pmid", "pmcid", "doi", "title",
                 "abstract", "authors", "journal", "year", "pub_types", "language",
                 "is_preprint", "url", "retrieved"]


def today() -> str:
    return _dt.date.today().isoformat()


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = str(doi).strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    d = d.strip().rstrip(".").lower()
    return d if d.startswith("10.") else None


def norm_pmid(pmid) -> str | None:
    if pmid is None:
        return None
    m = re.search(r"(\d{1,9})", str(pmid).replace("https://pubmed.ncbi.nlm.nih.gov/", ""))
    return m.group(1) if m else None


def norm_title(title: str | None) -> str:
    """Lowercase, strip accents/markup/punctuation; keeps CJK characters."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", re.sub(r"<[^>]+>", " ", str(title)))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_year(v) -> int | None:
    if v is None:
        return None
    m = re.search(r"(1[89]\d\d|20\d\d)", str(v))
    return int(m.group(1)) if m else None


def make_record(source_db: str, **kw) -> dict:
    """Build a record in the common schema; unknown keys are dropped."""
    rec = {k: None for k in RECORD_FIELDS}
    rec.update({k: v for k, v in kw.items() if k in RECORD_FIELDS})
    rec["source_db"] = source_db
    rec["doi"] = norm_doi(rec["doi"])
    rec["pmid"] = norm_pmid(rec["pmid"])
    rec["year"] = parse_year(rec["year"])
    rec["authors"] = [a.strip() for a in (rec["authors"] or []) if a and a.strip()]
    rec["pub_types"] = [p for p in (rec["pub_types"] or []) if p]
    rec["is_preprint"] = bool(rec["is_preprint"])
    rec["title"] = re.sub(r"\s+", " ", rec["title"]).strip() if rec["title"] else None
    rec["abstract"] = re.sub(r"\s+", " ", rec["abstract"]).strip() if rec["abstract"] else None
    rec["retrieved"] = rec["retrieved"] or today()
    if rec["doi"]:
        rec["id"] = f"doi:{rec['doi']}"
    elif rec["pmid"]:
        rec["id"] = f"pmid:{rec['pmid']}"
    else:
        h = hashlib.sha1(f"{norm_title(rec['title'])}|{rec['year']}".encode()).hexdigest()[:12]
        rec["id"] = f"{source_db}:{rec['raw_id'] or h}"
    return rec


def write_jsonl(path, records) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path) -> list[dict]:
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_topic(path) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # PyYAML
    except ImportError:
        sys.exit("PyYAML is required for YAML topic files: pip install -r requirements.txt")
    return yaml.safe_load(text)


def emit_summary(db: str, query_id: str, query: str, total, retrieved: int, out: str | None):
    s = {"db": db, "query_id": query_id, "query": query, "count_total": total,
         "retrieved": retrieved, "date": today(), "out": out}
    print(json.dumps(s, ensure_ascii=False))
    return s


def add_common_args(ap):
    ap.add_argument("--query", required=True, help="query string in this database's syntax")
    ap.add_argument("--query-id", default="q1", help="short id recorded on every record")
    ap.add_argument("--max", type=int, default=200, help="maximum records to retrieve (0 = count only)")
    ap.add_argument("--out", help="output JSONL path (common record schema)")
    ap.add_argument("--email", help="contact email for API politeness (or MECHATLAS_EMAIL)")
    return ap
