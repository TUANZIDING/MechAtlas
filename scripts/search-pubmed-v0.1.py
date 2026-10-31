#!/usr/bin/env python3
"""PubMed search via NCBI E-utilities (esearch with history server -> paged efetch XML).

Example:
  python3 scripts/search-pubmed-v0.1.py --query '("Radiation"[Mesh]) AND ("Muscle, Skeletal"[Mesh])' \
      --max 20 --out runs/demo/raw/pubmed-q1.jsonl --email you@example.org

Set NCBI_API_KEY to raise the limit from 3 to 10 requests/second.
"""
import argparse
import os
import xml.etree.ElementTree as ET
import importlib.util
import sys
from pathlib import Path


def _load(rel: str):
    """Import a versioned file (e.g. scripts/common-v0.1.py) by path, cached in sys.modules."""
    p = Path(__file__).resolve().parent.parent / rel
    key = "mechatlas_" + p.stem.replace("-", "_").replace(".", "_")
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


C = _load("scripts/common-v0.1.py")
BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
DB = "pubmed"


def _txt(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_pubmed_xml(xml_bytes: bytes, query_id: str = "q1") -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for art in root.iter("PubmedArticle"):
        mc = art.find("MedlineCitation")
        a = mc.find("Article")
        pmid = _txt(mc.find("PMID"))
        abstract = " ".join(
            ((t.get("Label") + ": ") if t.get("Label") else "") + _txt(t)
            for t in a.findall("Abstract/AbstractText"))
        authors = []
        for au in a.findall("AuthorList/Author"):
            if au.find("LastName") is not None:
                authors.append(f"{_txt(au.find('LastName'))} {_txt(au.find('Initials'))}".strip())
            elif au.find("CollectiveName") is not None:
                authors.append(_txt(au.find("CollectiveName")))
        ids = {i.get("IdType"): _txt(i) for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
        doi = ids.get("doi") or next((_txt(e) for e in a.findall("ELocationID") if e.get("EIdType") == "doi"), None)
        year = _txt(a.find("Journal/JournalIssue/PubDate/Year")) or _txt(a.find("Journal/JournalIssue/PubDate/MedlineDate")) \
            or _txt(a.find("ArticleDate/Year"))
        ptypes = [_txt(p) for p in a.findall("PublicationTypeList/PublicationType")]
        out.append(C.make_record(
            DB, source_query=query_id, raw_id=pmid, pmid=pmid, pmcid=ids.get("pmc"), doi=doi,
            title=_txt(a.find("ArticleTitle")), abstract=abstract or None, authors=authors,
            journal=_txt(mc.find("MedlineJournalInfo/MedlineTA")) or _txt(a.find("Journal/ISOAbbreviation")),
            year=year, pub_types=ptypes, language=_txt(a.find("Language")) or None,
            is_preprint="Preprint" in ptypes, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"))
    return out


def search(query: str, max_records: int = 200, email: str | None = None, query_id: str = "q1",
           batch: int = 200):
    key = os.environ.get("NCBI_API_KEY")
    common = {"db": "pubmed", "tool": "mechatlas", "email": C.contact_email(email), "api_key": key}
    es = C.http_json(BASE + "esearch.fcgi", {**common, "term": query, "usehistory": "y",
                                             "retmax": 0, "retmode": "json"}, email=email)["esearchresult"]
    total = int(es.get("count", 0))
    records = []
    n = min(total, max_records)
    for start in range(0, n, batch):
        xml = C.http_get(BASE + "efetch.fcgi", {**common, "query_key": es["querykey"], "WebEnv": es["webenv"],
                                                "retstart": start, "retmax": min(batch, n - start),
                                                "retmode": "xml"}, email=email)
        records.extend(parse_pubmed_xml(xml, query_id))
        C.log(f"  pubmed {query_id}: {len(records)}/{n}")
    return total, records[:max_records]


def fetch_pmids(pmids: list[str], email: str | None = None) -> list[dict]:
    """efetch a known PMID list (used by verify / snowball)."""
    out = []
    for i in range(0, len(pmids), 200):
        xml = C.http_get(BASE + "efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids[i:i + 200]), "retmode": "xml",
                                                "tool": "mechatlas", "email": C.contact_email(email),
                                                "api_key": os.environ.get("NCBI_API_KEY")}, email=email)
        out.extend(parse_pubmed_xml(xml, "fetch"))
    return out


def main():
    ap = C.add_common_args(argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    a = ap.parse_args()
    total, recs = search(a.query, a.max, a.email, a.query_id)
    if a.out:
        C.write_jsonl(a.out, recs)
    C.emit_summary(DB, a.query_id, a.query, total, len(recs), a.out)


if __name__ == "__main__":
    main()
