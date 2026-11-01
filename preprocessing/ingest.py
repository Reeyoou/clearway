"""Ingest the FAA AD NPRM spreadsheet into a clean, citable index.

No LLM is used anywhere in this file. Everything is deterministic and auditable:
  * cleans and normalises the spreadsheet (makes, dates, IDs)
  * recovers full rule text that the export shifted into the "Link" column
  * labels every row: proposal / supplemental / withdrawal
  * builds revision chains per docket (and links withdrawals to the dockets they name)
  * extracts applicability: models, "all" vs "certain", serial-number ranges,
    and whether applicability depends on an external service bulletin
  * splits text into citable chunks (doc, section, paragraph)

Run:  python -m preprocessing.ingest            (reads data/challenge4_ad_index.xlsx)
Writes data/index.pkl
"""
from __future__ import annotations

import pickle
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- makes
MAKE_RULES = [
    ("airbus helicopters", "Airbus Helicopters"), ("eurocopter", "Airbus Helicopters"),
    ("airbus canada", "Airbus Canada"), ("airbus", "Airbus"),
    ("mcdonnell douglas", "McDonnell Douglas"), ("boeing", "Boeing"),
    ("bombardier", "Bombardier"), ("embraer", "Embraer"), ("empresa brasileira", "Embraer"),
    ("textron aviation", "Textron Aviation (Cessna)"), ("cessna", "Textron Aviation (Cessna)"),
    ("raytheon", "Textron Aviation (Beechcraft)"), ("beech", "Textron Aviation (Beechcraft)"),
    ("piper", "Piper"), ("bell", "Bell"), ("gulfstream", "Gulfstream"),
    ("rolls-royce", "Rolls-Royce"), ("general electric", "General Electric"),
    ("pratt", "Pratt & Whitney"), ("cfm", "CFM International"), ("robinson", "Robinson"),
    ("sikorsky", "Sikorsky"), ("leonardo", "Leonardo"), ("agusta", "Leonardo"),
    ("dassault", "Dassault"), ("honeywell", "Honeywell"), ("de havilland", "De Havilland Canada"),
    ("bae systems", "BAE Systems"), ("fokker", "Fokker"), ("md helicopters", "MD Helicopters"),
    ("international aero engines", "International Aero Engines"), ("safran", "Safran"),
]


def norm_make(raw: str) -> list[str]:
    out = []
    for part in str(raw or "").split("|"):
        p = part.strip()
        if not p or p.lower() == "nan":
            continue
        low = p.lower()
        for key, name in MAKE_RULES:
            if key in low:
                p = name
                break
        if p not in out:
            out.append(p)
    return out


def makes_from_subject(subject: str) -> list[str]:
    low = subject.lower()
    return [name for key, name in MAKE_RULES if key in low][:1]


# --------------------------------------------------------------------------- text helpers
def clean(t: str) -> str:
    t = str(t or "").replace("\\n", "\n").replace("\\t", " ").replace("\t", " ")
    t = re.sub(r"(?m)^[ \t]*[=\-_]{5,}[ \t]*$", "", t)        # Federal Register separator lines
    t = re.sub(r"[=\-_]{8,}", " ", t)
    t = re.sub(r"[  ]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()


SENT_SPLIT = re.compile(r"(?<=[.;:])(?<!\bNo\.)(?<!\bNos\.)(?<!S\.A\.)(?<!\bInc\.)(?<!\bCo\.)(?<!\bSec\.)(?<!U\.S\.)(?<!\bLtd\.)(?<!\bCorp\.)(?<!\bpara\.)(?<!\bFig\.)\s+(?=[A-Z(])")  # do not split after "No.", "S.A.", "Inc." etc.


def sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if not para:
            continue
        out.extend(s.strip() for s in SENT_SPLIT.split(para) if len(s.strip()) > 3)
    return out


# --------------------------------------------------------------------------- sections
KNOWN_HEADS = [
    "Comments Due Date", "Affected ADs", "Applicability", "Subject", "Unsafe Condition",
    "Compliance", "Alternative Methods of Compliance (AMOCs)", "Alternative Methods of Compliance",
    "Related Information", "Special Flight Permits", "Reason", "Effective Date",
    "Material Incorporated by Reference", "Parts Installation", "Parts Installation Prohibition",
    "Repetitive Inspections", "Corrective Actions", "Required Actions", "Actions",
    "Inspection", "Replacement", "Modification", "Credit for Previous Actions",
    "Other FAA AD Provisions", "New Requirements of This AD", "Definitions", "Exceptions to Service Information Specifications",
    "The Withdrawal", "Withdrawal of the NPRM", "Conclusion", "Ferry Only", "Procedures",
    "No Reporting Requirement", "Reporting Requirement", "FAA AD Differences",
    "SUMMARY", "DATES", "ADDRESSES", "FOR FURTHER INFORMATION CONTACT", "SUPPLEMENTARY INFORMATION",
    "Comments Invited", "Background", "Discussion", "FAA's Determination", "Proposed AD Requirements",
    "Costs of Compliance", "Authority for This Rulemaking", "Regulatory Findings", "The Proposed Amendment",
    "Related Service Information Under 1 CFR Part 51", "Explanation of Required Compliance Information",
]
HEAD_RE = re.compile(
    r"(?:^|\n)\s*(?:\(([a-z]{1,2})\)\s*)?(" + "|".join(re.escape(h) for h in sorted(KNOWN_HEADS, key=len, reverse=True)) + r")[ \t]*(?::|(?=\n))",
)


def split_sections(text: str) -> list[tuple[str, str]]:
    """Return [(section_name, body)] in order. Text before the first heading is 'Preamble'."""
    marks = [(m.start(), m.end(), (f"({m.group(1)}) " if m.group(1) else "") + m.group(2)) for m in HEAD_RE.finditer(text)]
    if not marks:
        return [("Text", text)]
    out = []
    if marks[0][0] > 0 and text[: marks[0][0]].strip():
        out.append(("Preamble", text[: marks[0][0]]))
    for i, (s, e, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[e:end].strip()
        if body:
            out.append((name, body))
    return out


# --------------------------------------------------------------------------- applicability
MODEL_TOKEN = re.compile(r"\b[A-Z]{0,6}[0-9]?[A-Z0-9]*(?:[-/][A-Z0-9]+)*\b")


def _has_digit(s): return any(c.isdigit() for c in s)


def extract_models(span: str) -> list[str]:
    """Pull model designators out of an applicability phrase.
    Handles shorthand like 'A330-243, -243F' -> A330-243, A330-243F."""
    span = span.replace("–", "-").replace("‑", "-")
    # cut at words that start the 'installed on' part, to avoid picking airplane models for engine ADs
    span = re.split(r"installed on|but not limited to|certificated in any category|as listed in|as identified in", span, maxsplit=1)[0]
    # drop serial-number and part-number phrases so their numbers are not read as models
    span = re.sub(r"(?:serial (?:numbers?|nos?\.?)|S/ ?Ns?|part (?:numbers?|nos?\.?)|P/ ?Ns?)\b[^;]*?(?=;|\. |$| Model )", " ", span, flags=re.I)
    span = re.sub(r"\b\S+ (?:through|thru) \S+", " ", span)
    models, base = [], None
    for raw in re.findall(r"(?<![A-Za-z0-9])-?[A-Z0-9][A-Z0-9/.-]*", span):
        tok = raw.strip(".-/") if not raw.startswith("-") else raw.rstrip(".")
        if not _has_digit(tok) or len(tok) < 2:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok) or re.fullmatch(r"(19|20)\d\d", tok):
            continue  # AD numbers / years
        if re.fullmatch(r"\d{1,2}", tok) or re.fullmatch(r"0\d+", tok) or re.fullmatch(r"\d{4,}", tok):
            continue
        if tok.startswith("-"):
            if base:
                tok = base + tok
            else:
                continue
        else:
            base = tok.split("-")[0] if "-" in tok else tok
        tok = tok.upper()
        if tok not in models:
            models.append(tok)
    return models[:60]


SERIAL = r"[A-Z]{0,4}\d{0,4}[A-Z]{0,2}-?\d{2,}"
RANGE_RE = re.compile(rf"\b({SERIAL}|\d+)\s+(?:through|thru)\s+({SERIAL})\b")  # plain '1 through 365' too
SUBSEQ_RE = re.compile(rf"\b({SERIAL})\s+and\s+(?:subsequent|up)\b")


def parse_serial(s: str):
    m = re.fullmatch(r"(.*?)(\d+)", s.strip().upper())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def extract_applicability(app_text: str, summary: str) -> dict:
    text = app_text or ""
    scope_src = text if text else summary
    low = scope_src.lower()
    scope = "certain" if re.search(r"\bcertain\b|serial n|s/ ?n|as listed in|as identified in|having|with .{0,40}installed", low) else ("all" if re.search(r"\ball\b", low) else "unknown")
    span = text if text else (re.search(r"(?:for|to) (?:all|certain) (.{0,400}?)(?:\. |This )", summary, re.S) or re.search(r"(.{0,400})", summary, re.S)).group(1)
    models = extract_models(span)
    ranges = []
    for m in RANGE_RE.finditer(text):
        a, b = m.group(1), m.group(2)
        pa, pb = parse_serial(a), parse_serial(b)
        if pa and pb and pa[0] == pb[0] and pa[1] <= pb[1]:
            before = text[max(0, m.start() - 160):m.start()].lower()
            kind = "line" if before.rfind("line number") > max(before.rfind("serial"), before.rfind("s/n")) else "serial"
            ranges.append({"lo": a, "hi": b, "prefix": pa[0], "lo_n": pa[1], "hi_n": pb[1], "kind": kind})
    # single serials listed next to ranges, e.g. 'serial numbers 4001, and 4003 through 4081'
    in_range = {r["lo"] for r in ranges} | {r["hi"] for r in ranges}
    for ph in re.finditer(r"serial (?:numbers?|nos?\.?)\s*(?:\(S/ ?Ns?\))?\s*([^;]{0,200}?)(?:inclusive|certificated|,? that|;|\.\s)", text, re.I):
        listed = re.split(r"\b(?:except(?: for)?|excluding|other than)\b", ph.group(1), maxsplit=1, flags=re.I)[0]  # never count excluded numbers as included
        for tok in re.findall(rf"\b{SERIAL}\b", listed.replace("- ", "-")):
            ps = parse_serial(tok)
            if ps and tok not in in_range and not re.fullmatch(r"(19|20)\d\d", tok):
                ranges.append({"lo": tok, "hi": tok, "prefix": ps[0], "lo_n": ps[1], "hi_n": ps[1], "kind": "serial"})
                in_range.add(tok)
    for a in SUBSEQ_RE.findall(text):
        pa = parse_serial(a)
        if pa:
            ranges.append({"lo": a, "hi": "and subsequent", "prefix": pa[0], "lo_n": pa[1], "hi_n": 10**12, "kind": "serial"})
    # model designators that appear in applicability tables (e.g. 'PA-28-140' rows)
    for t in re.findall(r"\b[A-Z]{1,4}-\d{1,3}[A-Z]{0,2}(?:-\d{1,4}[A-Z]{0,2})*(?:/\d{2,4})*\b", text):
        if t not in models and not any(t in (r["lo"], r["hi"]) for r in ranges):
            models.append(t)
    # exceptions ("excluding S/Ns 95, 108", "except freighters") and part-number conditions
    exclusions = []
    for m in re.finditer(r"[^.;]{0,100}\b(?:except(?: for)?|excluding|other than)\b[^.;]{0,220}", text, re.I):
        phrase = " ".join(m.group(0).split())
        tail = re.split(r"\b(?:except(?: for)?|excluding|other than)\b", phrase, maxsplit=1, flags=re.I)[1]
        nums = re.findall(rf"\b{SERIAL}\b", tail.replace(",", ", "))
        if re.search(r"modif|equipped|incorporat|embodi|convert|freighter|configur|installed|service bulletin", tail, re.I):
            kind, nums = "condition", []          # an exception about configuration: needs a human
        elif "line number" in phrase.lower():
            kind = "line"
        else:
            kind = "serial"
        exclusions.append({"text": phrase, "numbers": [n for n in nums if not re.fullmatch(r"(19|20)\d\d", n)][:80], "kind": kind})
    part_numbers = []
    for m in re.finditer(r"(?:part numbers?|P/ ?Ns?)\s*(?:\(P/ ?Ns?\))?\s*([^;]{0,160}?)(?:,? installed|;|\. |certificated|$)", text, re.I):
        for pn in re.findall(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)+\b|\b(?=[A-Z0-9]*\d)[A-Z0-9]{6,}\b", m.group(1)):
            if pn not in part_numbers:
                part_numbers.append(pn)
    parts_condition = bool(part_numbers) and bool(re.search(r"\binstalled\b|\bequipped with\b|\bwith (?:a |an )?[^,;]{0,60}(?:part number|P/ ?N)", text, re.I))
    external = bool(re.search(r"(?:as )?(?:listed|identified|specified) in .{0,80}(?:service bulletin|alert service|SB )", text, re.I | re.S))
    return {"scope": scope, "models": models, "serial_ranges": ranges, "external_ref": external,
            "exclusions": exclusions, "part_numbers": part_numbers[:20], "parts_condition": parts_condition,
            "source": "full text" if text else "summary"}


# --------------------------------------------------------------------------- kinds and links
DOCKET_RE = re.compile(r"\b(FAA-\d{4}-\d{3,6}(?:R\d)?|\d{4}-[A-Z]{2,3}-\d{1,4}-AD)\b")
AD_NUM_RE = re.compile(r"\b((?:19|20)\d{2}-\d{2}-\d{2})\b")


def kind_of(subject: str, summary: str, body: str) -> str:
    s = (subject + " " + summary[:400]).lower()
    if ("withdrawal" in subject.lower() or re.search(r"\bwithdraw(?:s|ing|n)\b.{0,60}(?:notice|nprm|proposed)", s)
            or "the withdrawal" in body.lower()[:200]
            or re.search(r"ACTION:\s*(?:Supplemental )?notice of proposed rulemaking[^.]{0,40}withdrawal", body[:3000], re.I)):
        return "withdrawal"
    if re.search(r"supplemental notice|snprm|reopen|revises? (?:an|the) (?:earlier|previously)", s) or re.search(r"ACTION:\s*Supplemental notice of proposed rulemaking", body[:3000], re.I):
        return "supplemental"
    return "proposal"


# --------------------------------------------------------------------------- main
PAGE_RE = re.compile(r"\[\[Page (\d+)\]\]")
DUE_RE = re.compile(r"(?:must receive comments?|comments? must be received|receive comments on this)[^.]{0,90}?\b(?:by|on or before)\s+([A-Z][a-z]+\.? \d{1,2},? \d{4})", re.I)


def fr_cite(citation: str, pages: str) -> str:
    """'Federal Register, Volume 91 Number 200 (...)' + '(Pages 66019-66022)' -> '91 FR 66019-66022'."""
    v = re.search(r"Volume (\d+)", citation or "", re.I)
    p = re.findall(r"\d+", pages or "")
    if not v or not p:
        return (citation or "").strip()
    return f"{v.group(1)} FR {p[0]}" + (f"-{p[-1]}" if len(p) > 1 and p[-1] != p[0] else "")


def build(xlsx: Path, out: Path, fulltext_dir: Path | None = None) -> dict:
    """Reads challenge4_ad_index.xlsx (sheets: nprm_index, revision_chains, data_notes)."""
    df = pd.read_excel(xlsx, sheet_name="nprm_index", header=2).astype(object)
    df.columns = [str(c).strip() for c in df.columns]
    chains_sheet = pd.read_excel(xlsx, sheet_name="revision_chains", header=2).astype(object)
    notes = pd.read_excel(xlsx, sheet_name="data_notes").astype(object)
    docs, chunks = [], []
    fetched = 0

    for i, r in df.iterrows():
        g = lambda c: "" if pd.isna(r.get(c)) else str(r.get(c)).strip()
        did = g("record_id")
        link_raw = g("federal_register_link")
        url = link_raw if re.fullmatch(r"https?://\S+", link_raw) else ""
        # In the source export, the proposed-rule text sits in the link column for many rows.
        body = clean(link_raw) if (not url and (len(link_raw) > 300 or re.search(r"withdraw|Applicability|Proposed Amendment", link_raw))) else ""
        body_source = "spreadsheet (rule text in link column)" if body else ""
        summary = clean(g("summary"))
        if len(summary) > 1500 and not body and re.search(r"Applicability|The Withdrawal", summary):
            body, summary = summary, ""
            body_source = "spreadsheet (rule text in summary column)"
        if fulltext_dir and (fulltext_dir / f"{did}.txt").exists():  # full Federal Register text from fetch_fulltext.py
            body = clean((fulltext_dir / f"{did}.txt").read_text(encoding="utf-8", errors="ignore"))
            body_source = "Federal Register full text"
            fetched += 1
        subject = clean(g("subject"))
        # data-quality check: an AD row whose summary is about something else (export error)
        summary_mismatch = bool(summary) and subject.lower().startswith("airworthiness directives") and not re.search(
            r"airworthiness|\bADs?\b|NPRM|proposed rul|directive|withdraw", summary, re.I)
        bad_summary = summary if summary_mismatch else ""
        if summary_mismatch:
            summary = ""
        sections = split_sections(body) if body else []
        app_text = " ".join(b for n, b in sections if n.endswith("Applicability"))
        if not app_text and body:
            m = re.search(r"Applicability:?(.{0,1500}?)(?:Compliance|Unsafe Condition|\(d\))", body, re.S)
            app_text = m.group(1) if m else ""
        app_text = PAGE_RE.sub(" ", app_text)
        kind = kind_of(subject, summary, body)
        makes = norm_make(g("make")) or makes_from_subject(subject)
        named_dockets = sorted(set(DOCKET_RE.findall(summary + " " + body)) - {g("docket_number")})
        sup_col = [x.strip() for x in re.split(r"[;,|\s]+", g("supersedes_ad")) if re.fullmatch(r"(?:19|20)\d{2}-\d{2}-\d{2}", x.strip())]
        supersedes = sorted(set(sup_col) | set(re.findall(r"supersed\w* (?:Airworthiness Directive \(AD\) |AD )?((?:19|20)\d{2}-\d{2}-\d{2})", summary + " " + body)))
        due = DUE_RE.search(PAGE_RE.sub(" ", body + " " + summary))
        due_iso = None
        if due:
            dt = pd.to_datetime(due.group(1).replace(".", ""), errors="coerce")
            due_iso = None if pd.isna(dt) else dt.date().isoformat()
        appl = extract_applicability(app_text, summary)
        model_col = [m.strip().upper() for m in g("model").split("|") if m.strip()]
        for m in model_col:
            if m not in appl["models"]:
                appl["models"].append(m)
        appl["serial_mentioned"] = bool(re.search(r"serial n|\bS/ ?Ns?\b|\bMSNs?\b|serial numbers?", app_text or summary, re.I))
        appl["text_basis"] = "full text" if app_text else "summary only"
        first_page = (re.findall(r"\d+", g("pages")) or [None])[0]
        doc = {
            "id": did, "row": int(i) + 4, "docket": g("docket_number"), "subject": subject,
            "issue_date": g("issue_date") or None, "publish_date": g("publish_date") or None,
            "comments_due": due_iso, "ad_number": g("ad_number"),
            "makes": makes, "model_column": model_col, "product_type": g("product_type"), "product_subtype": g("product_subtype"),
            "citation": g("citation"), "pages": g("pages"), "fr_cite": fr_cite(g("citation"), g("pages")),
            "first_page": int(first_page) if first_page else None, "url": url,
            "summary": summary, "body": body, "has_full_text": bool(body), "body_source": body_source,
            "kind": kind, "named_dockets": named_dockets, "supersedes": supersedes,
            "mentions_serial_flag": g("mentions_serial_numbers").lower() == "yes",
            "applicability": appl,
            "app_text": " ".join(app_text.split()),
            "summary_mismatch": bad_summary[:300] or None,
        }
        docs.append(doc)

        # citable chunks; 'page' is exact when the full text carries [[Page N]] markers
        fp = doc["first_page"]
        chunks.append({"doc": did, "section": "Subject", "text": subject, "page": fp})
        if summary:
            chunks.append({"doc": did, "section": "Summary", "text": " ".join(summary.split()), "page": fp})
        page = fp
        for name, b in sections:
            buf, buf_page = "", page
            for snt in sentences(b):
                marks = PAGE_RE.findall(snt)
                snt = " ".join(PAGE_RE.sub(" ", snt).split())
                if marks:
                    page = int(marks[-1])
                if not snt:
                    continue
                if len(buf) + len(snt) > 900 and buf:
                    chunks.append({"doc": did, "section": name, "text": buf.strip(), "page": buf_page})
                    buf, buf_page = "", page
                if not buf:
                    buf_page = page
                buf += " " + snt
            if buf.strip():
                chunks.append({"doc": did, "section": name, "text": buf.strip(), "page": buf_page})

    # --------------------------------------------------- revision chains per docket
    # Source of truth: the revision_chains sheet (latest publish date = current version).
    by_id = {d["id"]: d for d in docs}
    fam = defaultdict(set)
    for d in docs:
        if d["docket"]:
            fam[d["docket"]].add(d["id"])
    for _, row in chains_sheet.iterrows():
        if pd.notna(row.get("docket_number")) and pd.notna(row.get("record_id")) and str(row["record_id"]) in by_id:
            fam[str(row["docket_number"]).strip()].add(str(row["record_id"]).strip())
    for d in docs:  # withdrawals name the docket(s) they withdraw
        if d["kind"] == "withdrawal":
            for k in d["named_dockets"]:
                if k in fam:
                    fam[k].add(d["id"])
    chains = {}
    for k, ids in fam.items():
        ordered = sorted(ids, key=lambda x: (by_id[x]["publish_date"] or by_id[x]["issue_date"] or "", x))
        latest = by_id[ordered[-1]]
        withdrawn = any(by_id[x]["kind"] == "withdrawal" for x in ordered) and latest["kind"] == "withdrawal"
        final_ad = next((by_id[x]["ad_number"] for x in reversed(ordered) if by_id[x]["ad_number"]), None)
        chains[k] = {"docket": k, "versions": ordered, "latest": ordered[-1], "final_ad": final_ad,
                     "status": "WITHDRAWN" if withdrawn else ("FINAL AD RECORDED" if final_ad else ("REVISED" if len(ordered) > 1 else "SINGLE"))}
    for d in docs:
        ch = chains.get(d["docket"])
        if ch:
            d["version"] = ch["versions"].index(d["id"]) + 1
            d["n_versions"] = len(ch["versions"])
            d["latest_id"] = ch["latest"]
            d["chain_status"] = ch["status"]
            d["final_ad"] = ch["final_ad"]
        else:
            d.update(version=1, n_versions=1, latest_id=d["id"], chain_status="SINGLE", final_ad=d["ad_number"] or None)

    # model vocabulary (for detecting models in a question and for out-of-scope refusals)
    vocab = defaultdict(set)
    for d in docs:
        for m in d["applicability"]["models"]:
            vocab[m].add(d["id"])
        for m in re.findall(r"\b[A-Z]{0,5}\d[A-Z0-9]*(?:-[A-Z0-9]+)*\b", d["subject"]):
            if _has_digit(m) and len(m) >= 2:
                vocab[m.upper()].add(d["id"])

    corpus_chars = sum(len(c["text"]) for c in chunks)
    fr_pages = 0
    for d in docs:
        if d["has_full_text"]:
            p = re.findall(r"\d+", d["pages"] or "")
            fr_pages += (int(p[-1]) - int(p[0]) + 1) if len(p) > 1 and 0 <= int(p[-1]) - int(p[0]) < 300 else 1
    index = {"docs": docs, "chunks": chunks, "chains": chains, "model_vocab": {k: sorted(v) for k, v in vocab.items()},
             "makes": sorted({m for d in docs for m in d["makes"]}), "source_file": xlsx.name,
             "data_notes": [(str(a), str(b)) for a, b in notes.values.tolist()],
             "corpus": {"records": len(docs), "chunks": len(chunks), "chars": corpus_chars, "words": sum(len(c["text"].split()) for c in chunks),
                        "approx_pages": round(corpus_chars / 3000), "full_text_records": sum(d["has_full_text"] for d in docs),
                        "fetched_records": fetched, "fr_pages_full_text": fr_pages}}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        pickle.dump(index, f)
    return index


def report(ix: dict) -> None:
    docs = ix["docs"]
    k = pd.Series([d["kind"] for d in docs]).value_counts().to_dict()
    print(f"docs {len(docs)} | chunks {len(ix['chunks'])} | full text {sum(d['has_full_text'] for d in docs)}")
    print("kinds", k)
    st = pd.Series([c["status"] for c in ix["chains"].values()]).value_counts().to_dict()
    print("dockets", len(ix["chains"]), st)
    print("with serial ranges", sum(bool(d["applicability"]["serial_ranges"]) for d in docs),
          "| external SB applicability", sum(d["applicability"]["external_ref"] for d in docs),
          "| models parsed", sum(bool(d["applicability"]["models"]) for d in docs))
    print("corpus", ix["corpus"])


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "challenge4_ad_index.xlsx"
    ix = build(src, ROOT / "data" / "index.pkl", ROOT / "data" / "fulltext")
    report(ix)
