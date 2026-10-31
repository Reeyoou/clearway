"""Traceable AD search engine: retrieve -> resolve version -> check applicability -> cite -> verify, or refuse.

Design rule: code decides, text is quoted.
  * Retrieval, filtering, version logic and applicability are deterministic code.
  * Every answer sentence is a verbatim sentence from a source chunk, with its citation.
  * A verifier re-checks every sentence against its cited chunk before anything is shown.
  * An LLM (optional, see llm.py) may only rephrase verified evidence; its output is verified the same way.
"""
from __future__ import annotations

import math
import pickle
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STOP = set("""a an the of to for on in at by with and or is are was were be been do does did what which who whom whose
when where why how this that these those there here my our your i we you me us it its any all some about tell show give
need should would could can may might must please list find search look explain summarise summarize describe
ad ads airworthiness directive directives proposed proposal proposes propose nprm faa rule rulemaking notice
require required requires requirement requirements apply applies applicable applicability affected affect
model models airplane airplanes aircraft helicopter helicopters engine engines series serial number numbers sn s/n msn
have has having got say says said current latest status version docket dockets supersede supersedes superseded""".split())

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/.][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    toks = []
    for t in TOKEN_RE.findall(text.lower()):
        t = t.strip(".")
        if t in STOP or len(t) < 2 and not t.isdigit():
            continue
        toks.append(t)
        if "-" in t or "/" in t:
            toks.extend(p for p in re.split(r"[-/]", t) if p and p not in STOP and len(p) > 1)
    return toks


def norm_ws(s: str) -> str:
    return " ".join(s.split())


SENT_SPLIT = re.compile(r"(?<=[.;])(?<!\bNo\.)(?<!\bNos\.)(?<!S\.A\.)(?<!\bInc\.)(?<!\bCo\.)(?<!\bSec\.)(?<!U\.S\.)(?<!\bLtd\.)(?<!\bCorp\.)(?<!\bpara\.)(?<!\bFig\.)\s+(?=[A-Z(])")  # do not split after "No.", "S.A.", "Inc." etc.

DOCKET_RE = re.compile(r"\b(FAA-\d{4}-\d{3,6}(?:R\d)?|\d{4}-[A-Z]{2,3}-\d{1,4}-AD)\b", re.I)
AD_NUM_RE = re.compile(r"\b((?:19|20)\d{2}-\d{2}-\d{2})\b")
SERIAL_Q_RE = re.compile(r"(?:serial(?: number| no\.?)?|s/ ?n|msn)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]*\d)", re.I)
BOILER = re.compile(r"regulatory actions are needed|authority citation|under the authority delegated|Executive Order|we have determined that this|economic impact", re.I)
LIST_Q = re.compile(r"\b(?:which|list|all|every|any|show|find)\b.{0,40}\b(?:ADs?|directives?|proposals?|NPRMs?)\b|\b(?:ADs?|directives?|proposals?)\s+(?:for|on|concerning|about|affecting)\b", re.I)
VALUE_Q = re.compile(r"\b(?:what is the|what's the|what are the)\s+(?:\w+\s+){0,3}(?:value|torque|interval|limit|time|threshold|hours|cycles|pressure|dimension|clearance)|how (?:many|much|often|long|soon)", re.I)
VALUE_A = re.compile(r"\d[\d,.]*\s*(?:total\s+)?(?:flight hours|hours|flight cycles|cycles|landings|months|days|years|in-lb|inch-pounds|ft-lb|foot-pounds|N\.?m|inches|inch|mm|percent|%|psi)", re.I)
MODEL_LIKE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z]|\d{3})[A-Z0-9]+(?:-[A-Z0-9]+)*\b")


class Engine:
    def __init__(self, index_path: Path | None = None):
        index_path = index_path or ROOT / "data" / "index.pkl"
        with open(index_path, "rb") as f:
            ix = pickle.load(f)
        self.ix = ix
        self.docs = {d["id"]: d for d in ix["docs"]}
        self.chunks = ix["chunks"]
        for n, c in enumerate(self.chunks):
            c["cid"] = f"{c['doc']}#{n}"
        self.by_doc = defaultdict(list)
        for c in self.chunks:
            self.by_doc[c["doc"]].append(c)
        self.vocab = ix["model_vocab"]
        self.makes = ix["makes"]
        self._build_bm25()

    # ------------------------------------------------------------------ BM25
    def _build_bm25(self, k1=1.4, b=0.7):
        self.k1, self.b = k1, b
        self.tf, self.dl = [], []
        df = Counter()
        self.post = defaultdict(list)
        for i, c in enumerate(self.chunks):
            toks = tokenize(c["text"] + " " + (c["section"] if c["section"] != "Summary" else ""))
            tf = Counter(toks)
            self.tf.append(tf)
            self.dl.append(len(toks))
            for t in tf:
                df[t] += 1
                self.post[t].append(i)
        N = len(self.chunks)
        self.avgdl = sum(self.dl) / N
        self.idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        # doc-level term sets for coverage
        self.doc_terms = defaultdict(set)
        for i, c in enumerate(self.chunks):
            self.doc_terms[c["doc"]].update(self.tf[i])

    def bm25(self, q_toks: list[str], allowed_docs: set | None = None) -> dict[int, float]:
        scores = defaultdict(float)
        for t in set(q_toks):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.post[t]:
                if allowed_docs is not None and self.chunks[i]["doc"] not in allowed_docs:
                    continue
                f = self.tf[i][t]
                scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl))
        return scores

    # ------------------------------------------------------------------ parsing
    def match_models(self, tok: str) -> list[str]:
        """Vocabulary keys that a model token in a question refers to (loose: family match)."""
        tok = tok.upper()
        return [k for k in self.vocab if k == tok or k.startswith(tok + "-") or tok.startswith(k + "-")]

    def parse(self, q: str, model: str | None, serial: str | None) -> dict:
        dockets = [d.upper() for d in DOCKET_RE.findall(q)]
        ad_nums = [a for a in AD_NUM_RE.findall(q) if not any(a in d for d in dockets)]
        sm = SERIAL_Q_RE.search(q)
        serial = (serial or (sm.group(1) if sm else "") or "").strip().upper() or None
        q_wo = DOCKET_RE.sub(" ", q)
        q_wo = AD_NUM_RE.sub(" ", q_wo)
        if sm:
            q_wo = q_wo.replace(sm.group(0), " ")
        models, unknown = [], []
        cand = [model.upper()] if model else []
        cand += [m for m in MODEL_LIKE.findall(q_wo.upper()) if not re.fullmatch(r"(19|20)\d\d", m)]
        for m in cand:
            hits = self.match_models(m)
            if hits:
                if m not in models:
                    models.append(m)
            elif re.search(r"[A-Z]", m) and re.search(r"\d", m) and len(m) >= 3 and not re.fullmatch(r"\d+(ST|ND|RD|TH|S)", m):
                unknown.append(m)
        ql = q.lower()
        makes = [mk for mk in self.makes if mk.lower().split(" (")[0] in ql]
        return {"dockets": dockets, "ad_numbers": ad_nums, "serial": serial, "models": models,
                "unknown_models": unknown, "makes": makes}

    # ------------------------------------------------------------------ helpers
    def cite(self, c: dict) -> dict:
        d = self.docs[c["doc"]]
        return {"chunk": c["cid"], "doc": d["id"], "record_id": d["id"], "docket": d["docket"], "section": c["section"],
                "issue_date": d["issue_date"], "citation": d["citation"], "pages": d["pages"], "fr_cite": d["fr_cite"],
                "page": c.get("page"), "url": d["url"]}

    def chain_of(self, did: str) -> list[dict]:
        d = self.docs[did]
        ch = self.ix["chains"].get(d["docket"])
        ids = ch["versions"] if ch else [did]
        return [{"doc": x, "issue_date": self.docs[x]["issue_date"], "kind": self.docs[x]["kind"],
                 "subject": self.docs[x]["subject"]} for x in ids]

    def doc_models_match(self, d: dict, q_model: str) -> str:
        """'exact' | 'partial' (question too general) | 'none' | 'unknown' (no models parsed)."""
        ms = d["applicability"]["models"]
        if not ms:
            return "unknown"
        q = q_model.upper()
        if any(q == m or q.startswith(m + "-") or (q.startswith(m) and not m[-1].isdigit()) for m in ms):
            return "exact"
        if any(m.startswith(q + "-") or m.startswith(q) for m in ms):
            return "partial"
        return "none"

    def best_sentences(self, did_list: list[str], q_toks: list[str], k=3, sections_first=()) -> list[tuple[str, dict]]:
        qset = set(q_toks)
        scored = []
        for did in did_list:
            for c in self.by_doc[did]:
                if c["section"] == "Subject":
                    continue
                for s in SENT_SPLIT.split(c["text"]):
                    s = s.strip()
                    if len(s) < 25:
                        continue
                    st = set(tokenize(s))
                    sc = sum(self.idf.get(t, 0) for t in qset & st)
                    if c["section"] in sections_first:
                        sc += 0.5
                    if c["section"] == "Summary" and s.startswith("The FAA propos"):
                        sc += 0.8  # the 'what is proposed' sentence is the most useful default
                    scored.append((sc, s, c))
        scored.sort(key=lambda x: -x[0])
        out, seen = [], set()
        for sc, s, c in scored:
            if s in seen:
                continue
            seen.add(s)
            out.append((s, c))
            if len(out) >= k:
                break
        return out

    def find_sentence(self, did: str, needle: str, section_hint: str = "Applicability"):
        for c in self.by_doc[did]:
            if section_hint in c["section"] and needle in c["text"]:
                for s in SENT_SPLIT.split(c["text"]):
                    if needle in s:
                        return s.strip(), c
                return c["text"][:400], c
        for c in self.by_doc[did]:
            if needle in c["text"]:
                for s in SENT_SPLIT.split(c["text"]):
                    if needle in s:
                        return s.strip(), c
        return None

    # ------------------------------------------------------------------ applicability (pure rules)
    def applicability(self, d: dict, models: list[str], serial: str | None) -> dict | None:
        """Core verdict, then three deterministic refinements:
        exceptions in the text, part-number conditions, and proposal vs final-AD wording."""
        res = self._applicability_core(d, models, serial)
        a = d["applicability"]
        if (res and res["verdict"] in ("DEPENDS", "CANNOT DETERMINE") and a.get("parts_condition") and a.get("part_numbers")
                and not a["serial_ranges"] and not a["external_ref"] and not res["rules"][0:1] == ["model unknown"]
                and not any("-> none" in r or "-> partial" in r or "-> unknown" in r for r in res["rules"])):
            # 'certain' here means 'units with a given part installed': say that precisely
            res = {**res, "verdict": "APPLIES", "why": "The model is listed."}
        if not res or res["verdict"] != "APPLIES":
            return res
        ps = re.fullmatch(r"(.*?)(\d+)", serial or "")
        for ex in a.get("exclusions", []):
            ev = self.find_sentence(d["id"], ex["text"][:40]) or self.find_sentence(d["id"], ex["text"][-40:])
            if ex["kind"] in ("serial", "line") and ex["numbers"] and ps:
                if any((parse := re.fullmatch(r"(.*?)(\d+)", n)) and parse.group(1) == ps.group(1) and int(parse.group(2)) == int(ps.group(2)) for n in ex["numbers"]):
                    if ex["kind"] == "line":
                        continue  # excluded LINE numbers say nothing about a serial number
                    res["rules"].append(f"S/N {serial} appears in an exclusion list")
                    return {**res, "verdict": "DOES NOT APPLY", "why": f"S/N {serial} is inside a listed range but is explicitly excluded.",
                            "evidence": ([ev] if ev else []) + res["evidence"]}
                continue
            res["rules"].append(f"exception found: '{ex['text'][:90]}…'")
            return {**res, "verdict": "CANNOT DETERMINE", "why": "The applicability text contains an exception (e.g. a modification or configuration) that cannot be checked automatically. A qualified person must review it.",
                    "evidence": ([ev] if ev else []) + res["evidence"]}
        if a.get("parts_condition") and a.get("part_numbers"):
            pns = ", ".join(a["part_numbers"][:6])
            res["rules"].append(f"applicability depends on installed part number(s): {pns}")
            res = {**res, "verdict": "APPLIES (ONLY WITH PART INSTALLED)", "why": f"{res['why']} It applies only to units with part number {pns} installed. Check the installed parts."}
        final_ad = d.get("final_ad")
        if final_ad:
            res["rules"].append(f"final AD number {final_ad} recorded for this docket")
            res["verdict"] = res["verdict"].replace("APPLIES", f"APPLIES (FINAL AD {final_ad} RECORDED)", 1)
            res["caveat"] = (res.get("caveat") or "") + f" A final AD number ({final_ad}) is recorded, but the final rule is not in this dataset. Confirm against AD {final_ad}."
        else:
            res["verdict"] = res["verdict"].replace("APPLIES", "APPLIES IF ADOPTED", 1)
            res["rules"].append("document is a proposal (NPRM): it is not yet binding")
        return res

    def _applicability_core(self, d: dict, models: list[str], serial: str | None) -> dict | None:
        if not models and not serial:
            return None
        a = d["applicability"]
        ev = []
        app_c = next((c for c in self.by_doc[d["id"]] if c["section"].endswith("Applicability")), None)
        if app_c:
            ev.append((SENT_SPLIT.split(app_c["text"])[0].strip(), app_c))
        elif d["summary"]:
            sc = next(c for c in self.by_doc[d["id"]] if c["section"] == "Summary")
            ev.append((SENT_SPLIT.split(sc["text"])[0].strip(), sc))
        rule_trace = []
        if models:
            m = models[0]
            mm = self.doc_models_match(d, m)
            rule_trace.append(f"model '{m}' vs parsed models {a['models'][:12]} -> {mm}")
            if mm == "none":
                return {"verdict": "DOES NOT APPLY", "why": f"Model {m} is not among the models listed in this document's applicability.", "rules": rule_trace, "evidence": ev}
            if mm == "partial":
                variants = [x for x in a["models"] if x.startswith(m)]
                return {"verdict": "NEED MORE DETAIL", "why": f"This document lists specific {m} variants ({', '.join(variants[:8])}). Give the exact variant.", "rules": rule_trace, "evidence": ev}
            if mm == "unknown":
                return {"verdict": "CANNOT DETERMINE", "why": "The model list could not be parsed reliably from this document. Read the cited applicability text.", "rules": rule_trace, "evidence": ev}
        # ---- serial-number rule (team decision, consistent with FAA AC 39-7D para 9a):
        #   ranges listed            -> filter by range
        #   no serial numbers named  -> applies to ALL serial numbers, with an explicit comment
        #   serials named but unreadable -> cannot determine (refuse when unclear)
        ranges = [r for r in a["serial_ranges"] if r.get("kind") != "line"]
        line_only = bool(a["serial_ranges"]) and not ranges
        basis = "based on its full applicability text" if a.get("text_basis") == "full text" else "based on its summary; the full text is not indexed for this record"
        no_sn = f"No serial numbers are mentioned in this directive ({basis}), so it is treated as applying to all serial numbers."
        extra = ""
        if a["external_ref"]:
            extra = " The text refers to a service bulletin for the affected units; confirm against that bulletin."
        elif a["scope"] == "certain":
            extra = " The text says 'certain' units without listing serial numbers; the full document may narrow this."
        if line_only:
            rule_trace.append(f"ranges are LINE numbers {[(r['lo'], r['hi']) for r in a['serial_ranges']][:4]}, not serial numbers")
            return {"verdict": "CANNOT DETERMINE" if serial else "DEPENDS",
                    "why": "This document defines applicability by production LINE number, not serial number. Check your aircraft's line number.", "rules": rule_trace, "evidence": ev}
        if ranges:
            rule_trace.append(f"serial ranges listed: {[(r['lo'], r['hi']) for r in a['serial_ranges']][:8]}")
            if not serial:
                return {"verdict": "DEPENDS", "why": f"This directive is limited to listed serial numbers ({', '.join(r['lo'] if r['lo'] == r['hi'] else r['lo'] + '–' + r['hi'] for r in ranges[:4])}{'…' if len(ranges) > 4 else ''}). Enter a serial number to check.",
                        "rules": rule_trace, "evidence": ev}
            ps = re.fullmatch(r"(.*?)(\d+)", serial)
            if not ps:
                return {"verdict": "CANNOT DETERMINE", "why": f"S/N '{serial}' could not be read as a serial number.", "rules": rule_trace, "evidence": ev}
            pre, n = ps.group(1), int(ps.group(2))
            same = [r for r in ranges if r["prefix"] == pre]
            hit = [r for r in same if r["lo_n"] <= n <= r["hi_n"]]
            if hit:
                r = hit[0]
                s_ = self.find_sentence(d["id"], r["lo"])
                return {"verdict": "APPLIES", "why": (f"S/N {serial} is listed individually." if r['lo'] == r['hi'] else f"S/N {serial} is within {r['lo']} through {r['hi']}."), "rules": rule_trace, "evidence": [s_] if s_ else ev,
                        "caveat": "This document lists several models and several serial ranges. Ranges are not linked to individual models, so confirm the range belongs to your model in the cited text." if len(a['models']) > 1 and len(a['serial_ranges']) > 1 else None}
            if same:
                return {"verdict": "DOES NOT APPLY", "why": f"S/N {serial} is outside every listed serial range.", "rules": rule_trace, "evidence": ev}
            return {"verdict": "CANNOT DETERMINE", "why": f"S/N format '{serial}' does not match the serial format used in this document.", "rules": rule_trace, "evidence": ev}
        if a.get("serial_mentioned"):
            rule_trace.append("serial numbers are mentioned but no range could be read")
            return {"verdict": "CANNOT DETERMINE", "why": "The applicability text mentions serial numbers, but they could not be read reliably. Check the cited text.", "rules": rule_trace, "evidence": ev}
        rule_trace.append("no serial numbers mentioned -> all serial numbers")
        return {"verdict": "APPLIES", "why": (f"Applies to all serial numbers, including S/N {serial}." if serial else "Applies to all serial numbers of this model."),
                "note": no_sn + extra, "rules": rule_trace, "evidence": ev}

    def verdict_for(self, did: str, p: dict) -> str | None:
        """One-line applicability verdict for a candidate, version-aware."""
        if not (p["models"] or p["serial"]):
            return None
        d = self.docs[did]
        chain = self.chain_of(did)
        content = [x["doc"] for x in chain if x["kind"] != "withdrawal"]
        tgt = self.docs[content[-1]] if content else d
        a = self.applicability(tgt, p["models"], p["serial"])
        v = f"{a['verdict']}: {a['why']}" if a else None
        if self.docs[d["latest_id"]]["kind"] == "withdrawal":
            return "WITHDRAWN (no requirement)" + (f"; before withdrawal: {v}" if v else "")
        if v is None:
            return None
        return v

    # ------------------------------------------------------------------ list mode
    def version_list(self, did: str) -> list[dict]:
        """Each document in a docket's revision chain, oldest first, with one quoted line."""
        chain = self.chain_of(did)
        out = []
        for i, x in enumerate(chain):
            v = self.docs[x["doc"]]
            if x["kind"] == "withdrawal":
                q = self._sent(v["id"], r"withdraw", 1)
            else:
                q = (self._sent(v["id"], r"(?:This|The) proposed AD would (?:\w+ ){0,2}require|would (?:also |continue to )?require", 1)
                     or self._sent(v["id"], r"revises|supplemental|reopen", 1) or self._sent(v["id"], r"propos", 1))
            if not q:
                subj = next((c for c in self.by_doc[v["id"]] if c["section"] == "Subject"), None)
                q = [{"text": subj["text"], "cite": self.cite(subj)}] if subj else []
            out.append({"version": i + 1, "of": len(chain), "record_id": v["id"], "kind": x["kind"],
                        "publish_date": v.get("publish_date") or v["issue_date"], "comments_due": v["comments_due"],
                        "current": i == len(chain) - 1, "quote": q[0] if q else None})
        return out

    def list_for(self, p: dict, allowed: set) -> list[dict]:
        """One row per docket (latest version), newest first."""
        rows, seen = [], set()
        for did in sorted(allowed, key=lambda x: self.docs[x]["issue_date"] or "", reverse=True):
            d = self.docs[did]
            key = d["docket"] or did
            if key in seen:
                continue
            seen.add(key)
            chain = self.chain_of(did)
            content = [x["doc"] for x in chain if x["kind"] != "withdrawal"] or [did]
            body = self.docs[content[-1]]
            w = self._sent(body["id"], r"(?:This|The) proposed AD would (?:\w+ ){0,2}require|would (?:also |continue to )?require", 1) \
                or self._sent(body["id"], r"propos|withdraw", 1)
            rows.append({"doc": body["id"], "docket": d["docket"], "subject": d["subject"],
                         "issue_date": self.docs[chain[-1]["doc"]]["issue_date"], "versions": len(chain),
                         "status": self.docs[d["latest_id"]]["chain_status"] if d["latest_id"] in self.docs else d["chain_status"],
                         "supersedes": body["supersedes"],
                         "final_ad": d.get("final_ad") or body.get("final_ad"),
                         "comments_due": body["comments_due"],
                         "what": (w[0] if w else None),
                         "version_list": self.version_list(did) if len(chain) > 1 else [],
                         "verdict": self.verdict_for(did, p) if p["serial"] else None})
        return rows

    def check(self, model: str, serial: str | None = None) -> dict:
        """Check-applicability tab: every docket for an exact model, each with a verdict and counts."""
        t0 = time.perf_counter()
        p = self.parse(model, model, serial)
        keys = self.match_models(model) if model else []
        if not keys:
            near = sorted(k for k in self.vocab if k.startswith(model.upper()[:3]))[:12] if model else []
            return {"status": "refuse", "reason": f"No document in this dataset lists model '{model}'. That does not mean no AD exists for it, only that this dataset contains none."
                    + (f" Similar models in the dataset: {', '.join(near)}." if near else ""), "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
        allowed = {i for k in keys for i in self.vocab[k]}
        p = {**p, "models": [model.upper()], "serial": serial or None}
        rows = self.list_for(p, allowed)
        for r in rows:
            r["verdict"] = self.verdict_for(r["doc"], p) or ""
        def bucket(v):
            v = v.upper()
            if v.startswith("WITHDRAWN"): return "withdrawn"
            if v.startswith("APPLIES"): return "applies"
            if v.startswith("DOES NOT"): return "does_not"
            return "unknown"
        counts = {"applies": 0, "unknown": 0, "does_not": 0, "withdrawn": 0}
        for r in rows:
            r["bucket"] = bucket(r["verdict"])
            counts[r["bucket"]] += 1
        out = {"status": "check", "model": model, "serial": serial, "list": rows, "counts": counts, "parsed": p,
               "reason": f"{len(rows)} docket(s) list {model}. Verdicts come from rules applied to the quoted applicability text; a qualified person makes the final call.",
               "evidence": [r["what"] for r in rows if r["what"]]}
        out["verification"] = self.verify(out)
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    # ------------------------------------------------------------------ compare (supersession + versions)
    def _sent(self, did: str, pattern: str, k=2):
        rx = re.compile(pattern, re.I)
        out = []
        ordered = sorted(self.by_doc[did], key=lambda c: 0 if c["section"] == "Summary" else 1)  # summary first
        for c in ordered:
            if c["section"] == "Subject":
                continue
            for snt in SENT_SPLIT.split(c["text"]):
                snt = snt.strip()
                if rx.search(snt) and snt not in [o["text"] for o in out] and not BOILER.search(snt):
                    out.append({"text": snt, "cite": self.cite(c)})
                    if len(out) >= k:
                        return out
        return out

    def compare(self, d: dict) -> dict | None:
        out = {}
        if d["supersedes"]:
            ad = d["supersedes"][0]
            a = re.escape(ad)
            out["supersession"] = {
                "old_ad": ad,
                "old": self._sent(d["id"], rf"\bAD {a} (?:requires|required|currently requires)", 2),
                "why": self._sent(d["id"], rf"^Since (?:the FAA issued |we issued )?AD {a}|^Since (?:the FAA|we) issued", 1),
                "new": self._sent(d["id"], r"(?:This|The) proposed AD would (?:\w+ ){0,2}(?:require|retain)|would (?:also |continue to )?require", 2),
                "note": f"AD {ad} stays in force until a final rule replaces it; a proposal to supersede it changes nothing by itself. "
                        f"The full text of AD {ad} is not in this dataset; the 'previously required' lines are quoted from this proposal's own description of it.",
            }
        chain = self.chain_of(d["id"])
        if len([x for x in chain if x["kind"] != "withdrawal"]) > 1 or (len(chain) > 1):
            steps = []
            for i, x in enumerate(chain):
                if x["kind"] == "withdrawal":
                    q = self._sent(x["doc"], r"withdraw", 1) or [{"text": self.docs[x["doc"]]["subject"], "cite": self.cite(next(c for c in self.by_doc[x["doc"]] if c["section"] == "Subject"))}]
                else:
                    q = (self._sent(x["doc"], r"revises|this (?:new|supplemental) (?:action|NPRM)|reopen", 1)
                         or self._sent(x["doc"], r"would (?:\w+ ){0,2}require|proposes", 1))
                if not q:
                    q = self._sent(x["doc"], r".", 1)
                steps.append({"version": i + 1, "doc": x["doc"], "kind": x["kind"], "issue_date": x["issue_date"], "quote": q[0] if q else None})
            out["versions"] = steps
        return out or None

    # ------------------------------------------------------------------ main entry
    def ask(self, question: str, model: str | None = None, serial: str | None = None,
            min_coverage: float = 0.5, ambiguity_ratio: float = 0.9) -> dict:
        t0 = time.perf_counter()
        p = self.parse(question, model, serial)
        q_text = question
        for x in ([p["serial"]] if p["serial"] else []) + p["dockets"] + p["ad_numbers"]:
            q_text = re.sub(re.escape(x), " ", q_text, flags=re.I)
        q_text = SERIAL_Q_RE.sub(" ", q_text)
        q_toks = tokenize(q_text)
        res = {"question": question, "parsed": p, "status": None}

        def done(status, **kw):
            res.update(status=status, **kw)
            res["verification"] = self.verify(res)
            res["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return res

        # --- explicit identifiers
        allowed = None
        if p["dockets"] or p["ad_numbers"]:
            allowed = set()
            for d in self.docs.values():
                if d["docket"].upper() in p["dockets"] or set(p["dockets"]) & {x.upper() for x in d["named_dockets"]}:
                    allowed.add(d["id"])
                if p["ad_numbers"] and (d["ad_number"] in p["ad_numbers"] or set(p["ad_numbers"]) & set(d["supersedes"])):
                    allowed.add(d["id"])
            if not allowed:
                ids = ", ".join(p["dockets"] + p["ad_numbers"])
                return done("refuse", reason=f"No document in this dataset has docket or AD number {ids}. I won't guess from similar numbers.")
        if p["unknown_models"] and not p["models"] and not allowed:
            return done("refuse", reason=f"No document in this dataset mentions model {', '.join(p['unknown_models'])}. "
                        "The dataset covers 431 FAA proposed ADs; an absence here does not mean no AD exists for it.")
        # --- model / make filter (prevents blending across equipment types)
        if p["models"] and allowed is None:
            allowed = set()
            for m in p["models"]:
                for k in self.match_models(m):
                    allowed.update(self.vocab[k])
        if p["makes"]:
            mk = {d["id"] for d in self.docs.values() if set(d["makes"]) & set(p["makes"])}
            allowed = mk if allowed is None else (allowed & mk or allowed)

        if LIST_Q.search(question) and allowed and (p["models"] or p["makes"]) and not (p["dockets"] or p["ad_numbers"]):
            rows = self.list_for(p, allowed)
            res["list"] = rows
            res["list_scope"] = ", ".join(p["models"] or p["makes"])
            res["evidence"] = [r["what"] for r in rows if r["what"]]
            return done("list", reason=f"{len(rows)} docket(s) in this dataset mention {res['list_scope']}. "
                        "Status shows whether each proposal is still open, revised or withdrawn. These are proposals, not final ADs.")
        if not q_toks and allowed is None:
            return done("refuse", reason="The question has no searchable content.")
        scores = self.bm25(q_toks, allowed)
        if not scores and allowed:
            # identifiers / model filter matched but no free-text overlap: rank by recency within allowed set
            scores = {i: 0.01 for i, c in enumerate(self.chunks) if c["doc"] in allowed and c["section"] == "Summary"}
        if not scores:
            return done("refuse", reason="Nothing in the dataset matches this question.")

        # aggregate to docs, then to dockets (chains)
        doc_score = defaultdict(float)
        for i, s in scores.items():
            did = self.chunks[i]["doc"]
            doc_score[did] = max(doc_score[did], s)
        qset = set(q_toks)
        total_w = sum(self.idf.get(t, 0) for t in qset) or 1.0
        unknown_terms = [t for t in qset if t not in self.idf]

        def coverage(did):
            return sum(self.idf.get(t, 0) for t in qset & self.doc_terms[did]) / total_w if qset else 1.0

        chain_best = {}
        for did, s in doc_score.items():
            key = self.docs[did]["docket"] or did
            if key not in chain_best or s > chain_best[key][1]:
                chain_best[key] = (did, s)
        ranked = sorted(chain_best.values(), key=lambda x: -x[1])
        top_did, top_s = ranked[0]
        cov = coverage(top_did)
        res["retrieval"] = [{"doc": d, "docket": self.docs[d]["docket"], "score": round(s, 2), "coverage": round(coverage(d), 2),
                             "subject": self.docs[d]["subject"], "issue_date": self.docs[d]["issue_date"]} for d, s in ranked[:8]]
        explicit = bool(p["dockets"] or p["ad_numbers"])
        if not explicit and cov < min_coverage:
            missing = [t for t in qset if t not in self.doc_terms[top_did]]
            return done("refuse", reason=f"No document covers this question well enough (best coverage {cov:.0%}; "
                        f"terms not found: {', '.join(sorted(missing)[:8])}). I won't fill the gap from general knowledge.")
        if not explicit and len(ranked) > 1:
            close = [(d, s) for d, s in ranked[1:6] if s >= ambiguity_ratio * top_s and coverage(d) >= cov - 0.05]
            if not close and not p["serial"] and len(set(q_toks) - {m.lower() for m in p["models"]}) <= 2:
                # short, generic question: if several dockets match it fully, ask instead of picking one
                full = [(d, s) for d, s in ranked[1:6] if coverage(d) >= cov - 1e-9 and s >= 0.6 * top_s]
                if len(full) >= 1:
                    close = full
            if close:
                cands = [ranked[0]] + close
                return done("clarify", reason=f"{len(cands)} different dockets match this question about equally well. "
                            "Pick one (or add the docket number, model variant or serial number) so I don't mix instructions across directives."
                            + (" The applicability check for each candidate is shown." if (p["models"] or p["serial"]) else ""),
                            candidates=[{"doc": d, "docket": self.docs[d]["docket"], "subject": self.docs[d]["subject"],
                                         "issue_date": self.docs[d]["issue_date"], "status": self.docs[d]["chain_status"],
                                         "final_ad": self.docs[d].get("final_ad"), "comments_due": self.docs[d]["comments_due"],
                                         "supersedes": self.docs[d]["supersedes"],
                                         "versions": len(self.chain_of(d)),
                                         "version_list": self.version_list(d) if len(self.chain_of(d)) > 1 else [],
                                         "verdict": self.verdict_for(d, p)} for d, _ in cands])

        # --- resolve version
        d = self.docs[top_did]
        chain = self.chain_of(top_did)
        latest = self.docs[d["latest_id"]]
        content_ids = [x["doc"] for x in chain if x["kind"] != "withdrawal"] or [top_did]
        res["document"] = {k: d[k] for k in ["id", "docket", "subject", "issue_date", "publish_date", "citation", "pages", "fr_cite", "url", "kind", "makes", "ad_number", "body_source"]}
        res["chain"] = chain
        bad = [self.docs[x["doc"]] for x in chain if self.docs[x["doc"]].get("summary_mismatch")]
        if bad:
            res["data_issue"] = "; ".join(f"{b['id']} ({b['issue_date']}): its summary contains text from an unrelated FAA document "
                                          f"(\u201c{b['summary_mismatch'][:70]}\u2026\u201d), so that summary was ignored and the row's own rule text was used instead." for b in bad)
        if latest["kind"] == "withdrawal":
            s = self.best_sentences([latest["id"]], ["withdraw", "withdrawn", "withdrawing"], k=2)
            res["banner"] = {"level": "critical", "text": f"WITHDRAWN. The latest document in docket {d['docket']} ({latest['issue_date']}) withdraws this proposal. "
                             f"There is no requirement from this docket. Version {len(chain)} of {len(chain)}."
                             + (" Note: the spreadsheet's Status column says 'Current' for this row; that label is wrong." if d.get("status_in_dataset") == "Current" else "")}
            evidence = [{"text": t, "cite": self.cite(c)} for t, c in s]
            if not evidence:  # some withdrawal rows only have a subject line; cite it
                subj = next(c for c in self.by_doc[latest["id"]] if c["section"] == "Subject")
                evidence = [{"text": subj["text"], "cite": self.cite(subj)}]
            hist = self.best_sentences(content_ids[-1:], q_toks, k=2)
            app = {"verdict": "NOT REQUIRED (WITHDRAWN)", "why": "The proposal was withdrawn.", "rules": ["latest version kind = withdrawal"], "evidence": []}
            prior = self.applicability(self.docs[content_ids[-1]], p["models"], p["serial"])
            if prior:
                app["why"] += f" Before withdrawal it would have been: {prior['verdict']} ({prior['why']})"
                app["rules"] += prior["rules"]
                app["evidence"] = [{"text": t, "cite": self.cite(c)} for t, c in prior["evidence"] if t]
            res["compare"] = self.compare(self.docs[content_ids[-1]])
            res["key_quote"] = evidence[0] if evidence else None
            return done("answer", evidence=evidence, history=[{"text": t, "cite": self.cite(c)} for t, c in hist], applicability=app)
        if len(chain) > 1:
            older = top_did != chain[-1]["doc"]
            res["banner"] = {"level": "warn", "text": f"Docket {d['docket']} has {len(chain)} versions; showing version {[x['doc'] for x in chain].index(latest['id']) + 1} "
                             f"of {len(chain)} (latest, {latest['issue_date']})." + (" Your best text match was an older version." if older else "")}
            d = latest
        elif d.get("final_ad"):
            res["banner"] = {"level": "info", "text": f"Proposed rule issued {d['issue_date']}. A final AD number ({d['final_ad']}) is recorded for this docket, but the final rule is not in this dataset. Confirm requirements against AD {d['final_ad']}."}
        else:
            res["banner"] = {"level": "info", "text": f"Single version, issued {d['issue_date']}. This is a PROPOSED rule (NPRM), not a final AD."}
        app = self.applicability(d, p["models"], p["serial"])
        if app:
            app["evidence"] = [{"text": t, "cite": self.cite(c)} for t, c in app["evidence"] if t]
        ev = self.best_sentences([d["id"]], q_toks, k=3, sections_first=("(c) Applicability", "Applicability", "Summary"))
        if VALUE_Q.search(question):
            vals = [x for x in self.best_sentences([d["id"]], q_toks, k=12) if VALUE_A.search(x[0])]
            if vals:
                ev = vals[:3] + [x for x in ev if x not in vals][:1]
            else:
                res["key_quote"] = {"text": ev[0][0], "cite": self.cite(ev[0][1])} if ev else None
                return done("partial", reason="The matching document describes this topic but does not state the specific value you asked for. "
                            "It is normally in the referenced service information, which is not in this dataset. I won't supply a number from memory.",
                            evidence=[{"text": t, "cite": self.cite(c)} for t, c in ev], applicability=app)
        res["compare"] = self.compare(d)
        kq = self._sent(d["id"], r"(?:This|The) proposed AD would (?:\w+ ){0,2}require|would (?:also |continue to )?require", 1)
        res["key_quote"] = kq[0] if kq else ({"text": ev[0][0], "cite": self.cite(ev[0][1])} if ev else None)
        return done("answer", evidence=[{"text": t, "cite": self.cite(c)} for t, c in ev], applicability=app,
                    note=("Terms not found anywhere in the dataset: " + ", ".join(unknown_terms)) if unknown_terms else None)

    # ------------------------------------------------------------------ verification
    def verify(self, res: dict) -> dict:
        """Every quoted sentence must appear verbatim in the chunk it cites."""
        chunk_text = {c["cid"]: norm_ws(c["text"]) for c in self.chunks}
        items = list(res.get("evidence") or []) + list(res.get("history") or [])
        if res.get("applicability"):
            items += res["applicability"].get("evidence") or []
        if res.get("key_quote"):
            items.append(res["key_quote"])
        for row in (res.get("list") or []) + (res.get("candidates") or []):
            items += [v["quote"] for v in row.get("version_list") or [] if v.get("quote")]
        cmp_ = res.get("compare") or {}
        sup = cmp_.get("supersession") or {}
        items += (sup.get("old") or []) + (sup.get("why") or []) + (sup.get("new") or [])
        items += [v["quote"] for v in cmp_.get("versions") or [] if v.get("quote")]
        ok = [norm_ws(e["text"]) in chunk_text.get(e["cite"]["chunk"], "") for e in items]
        if not all(ok):  # never show unverifiable text
            for key in ("evidence", "history"):
                if res.get(key):
                    res[key] = [e for e in res[key] if norm_ws(e["text"]) in chunk_text.get(e["cite"]["chunk"], "")]
        return {"sentences": len(items), "verified": sum(ok), "all_verified": all(ok)}


if __name__ == "__main__":
    import json, sys
    e = Engine()
    print(json.dumps(e.ask(" ".join(sys.argv[1:]) or "R22 serial number 1500 teeter stop"), indent=2)[:4000])
