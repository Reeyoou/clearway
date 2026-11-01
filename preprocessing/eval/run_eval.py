"""Evaluate the engine against a naive-RAG baseline on the hand-written question set.

Baseline = what a typical hackathon RAG does: BM25 over all chunks, take the top chunk's
document, always answer. No equipment filter, no version logic, no applicability rules, no refusal.

Run:  python -m preprocessing.eval.run_eval      (writes eval/results.md and eval/results.json)
"""
from __future__ import annotations

import copy
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine import Engine, tokenize  # noqa: E402

Q = json.loads((ROOT / "preprocessing" / "eval" / "questions.json").read_text())


def baseline(e: Engine, q: str) -> dict:
    s = e.bm25(tokenize(q))
    if not s:
        return {"status": "answer", "docket": None, "kind": None}
    i = max(s, key=s.get)
    d = e.docs[e.chunks[i]["doc"]]
    return {"status": "answer", "docket": d["docket"], "kind": d["kind"], "verdict": "APPLIES"}


def judge(item: dict, r: dict, sysname: str) -> tuple[bool, str]:
    cat = item["cat"]
    if sysname == "engine":
        st = r["status"]
        dk = (r.get("document") or {}).get("docket")
        cands = [c["docket"] for c in r.get("candidates", [])]
        verdict = (r.get("applicability") or {}).get("verdict", "")
        banner = (r.get("banner") or {}).get("text", "")
    else:
        st, dk, cands, verdict = r["status"], r["docket"], [], r.get("verdict", "")
        banner = "WITHDRAWN" if r.get("kind") == "withdrawal" else ""
    if cat in ("topic", "identifier"):
        if st == "answer" and dk == item["gold_docket"]:
            return True, "correct document"
        if st == "clarify" and item["gold_docket"] in cands:
            return False, "asked to clarify (gold among candidates)"
        return False, f"{st}: got {dk}"
    if cat == "applicability":
        if sysname == "engine" and st == "clarify":
            c = next((c for c in r.get("candidates", []) if c["docket"] == item["gold_docket"]), None)
            if c and (c.get("verdict") or "").startswith(item["gold_verdict"]):
                return True, f"clarified; per-candidate verdict correct ({c['verdict'][:40]}…)"
            return False, "clarified; gold verdict not shown"
        if dk != item["gold_docket"]:
            return False, f"wrong document {dk}"
        why = (r.get("applicability") or {}).get("why", "") if sysname == "engine" else ""
        g = item["gold_verdict"]
        ok = verdict.startswith(g) or ("WITHDRAWN" in verdict and f"would have been: {g}" in why)
        if item.get("gold_contains"):
            ok = ok and item["gold_contains"] in verdict
        if item.get("gold_note_contains"):
            ok = ok and item["gold_note_contains"] in ((r.get("applicability") or {}).get("note") or "")
        if item.get("gold_not_contains"):
            ok = ok and item["gold_not_contains"] not in verdict
        return ok, verdict or "no verdict"
    if cat == "revision":
        if dk != item["gold_docket"]:
            return False, f"wrong document {dk}"
        g = item["gold_status"]
        if g == "ANY":
            return True, "correct document"
        if g == "WITHDRAWN":
            return ("WITHDRAWN" in banner), ("flagged withdrawn" if "WITHDRAWN" in banner else "withdrawal NOT flagged")
        if g == "FINAL":
            return ("final AD number" in banner), ("final AD number flagged" if "final AD number" in banner else "final AD number NOT flagged")
        if g == "REVISED":
            return ("versions" in banner), ("version chain shown" if "versions" in banner else "versions NOT shown")
    if cat == "list":
        if sysname != "engine":
            return False, "baseline returns one document, not a list"
        rows = r.get("list") or []
        ok = st == "list" and len(rows) >= item["min_rows"] and item["gold_docket"] in [x["docket"] for x in rows]
        return ok, f"{st}: {len(rows)} dockets listed"
    if cat == "compare":
        if sysname != "engine":
            return False, "baseline has no before/after view"
        sup = ((r.get("compare") or {}).get("supersession") or {})
        ok = st == "answer" and dk == item["gold_docket"] and bool(sup.get("old")) and bool(sup.get("new"))
        return ok, (f"old AD {sup.get('old_ad')}: before/why/after = {len(sup.get('old', []))}/{len(sup.get('why', []))}/{len(sup.get('new', []))}" if sup else f"{st}: {dk}, no comparison")
    if cat == "refuse":
        g = item["gold_status"]
        declined = st in ("refuse", "partial", "clarify")
        return (st == g) or (declined and g in ("refuse", "partial", "clarify") and st != "answer"), st
    return False, "?"


def main():
    e = Engine()
    rows, lat = [], []
    for item in Q:
        r = e.ask(item["q"])
        lat.append(r["latency_ms"])
        b = baseline(e, item["q"])
        ok_e, why_e = judge(item, r, "engine")
        ok_b, why_b = judge(item, b, "baseline")
        top5 = [x["docket"] for x in r.get("retrieval", [])[:5]] or [x["docket"] for x in r.get("list", [])]
        rows.append({"id": item["id"], "cat": item["cat"], "q": item["q"], "engine_ok": ok_e, "engine": why_e,
                     "baseline_ok": ok_b, "baseline": why_b, "status": r["status"], "latency_ms": r["latency_ms"],
                     "hit5": item.get("gold_docket") in top5 if item.get("gold_docket") else None,
                     "verified": r["verification"], "note": item.get("note", "")})

    cats = ["topic", "identifier", "applicability", "revision", "list", "compare", "refuse"]
    names = {"topic": "Find the right AD from a plain-English question", "identifier": "Look up by docket / AD number",
             "applicability": "Applicability verdict (model + serial)", "revision": "Version / withdrawal awareness", "list": "List every AD for a model", "compare": "Superseded AD: before / why / after",
             "refuse": "Correctly refuse, clarify or flag missing value"}
    summ = []
    for c in cats:
        rs = [x for x in rows if x["cat"] == c]
        summ.append((names[c], len(rs), sum(x["engine_ok"] for x in rs), sum(x["baseline_ok"] for x in rs)))
    answerable = [x for x in rows if x["cat"] != "refuse"]
    false_refusals = [x for x in answerable if x["status"] in ("refuse", "partial")]
    clarified = [x for x in answerable if x["status"] == "clarify"]
    sent = sum(x["verified"]["sentences"] for x in rows)
    ver = sum(x["verified"]["verified"] for x in rows)
    hit5 = [x["hit5"] for x in rows if x["hit5"] is not None]

    # scale test: replicate the corpus to ~5x (synthetic, for latency only)
    e5 = Engine()
    base = copy.deepcopy(e5.chunks)
    for k in range(4):
        for c in base:
            e5.chunks.append({**c, "cid": f"{c['cid']}x{k}"})
    e5._build_bm25()
    lat5 = [e5.ask(item["q"])["latency_ms"] for item in Q]

    p95 = lambda xs: sorted(xs)[int(0.95 * (len(xs) - 1))]
    total_e = sum(x["engine_ok"] for x in rows)
    total_b = sum(x["baseline_ok"] for x in rows)
    md = ["# Evaluation results", "",
          f"{len(rows)} hand-written questions (written by the team from the dataset; see `eval/questions.json`). "
          "Baseline = naive RAG: BM25 top chunk over all documents, always answers, no filters, no version or applicability logic.", "",
          "| Task | Questions | This system | Naive RAG baseline |", "|---|---:|---:|---:|"]
    md += [f"| {n} | {k} | {a}/{k} | {b}/{k} |" for n, k, a, b in summ]
    md += [f"| **Total** | **{len(rows)}** | **{total_e}/{len(rows)} ({total_e/len(rows):.0%})** | **{total_b}/{len(rows)} ({total_b/len(rows):.0%})** |", "",
           "## Trust metrics", "",
           f"- Quoted sentences verified verbatim against their cited source: **{ver}/{sent}**",
           f"- Right document in top 5 retrieved (where a gold document exists): **{sum(hit5)}/{len(hit5)}**",
           f"- False refusals on answerable questions: **{len(false_refusals)}/{len(answerable)}**" + (f" ({', '.join(x['id'] for x in false_refusals)})" if false_refusals else ""),
           f"- Asked to clarify on answerable questions: **{len(clarified)}/{len(answerable)}**" + (f" ({', '.join(x['id'] for x in clarified)})" if clarified else ""),
           f"- Latency on real corpus ({len(e.chunks)} chunks, {sum(len(c['text']) for c in e.chunks)/1e6:.2f}M chars): median **{statistics.median(lat):.0f} ms**, p95 **{p95(lat):.0f} ms**",
           f"- Latency on 5x replicated corpus ({len(e5.chunks)} chunks, synthetic, latency test only): median **{statistics.median(lat5):.0f} ms**, p95 **{p95(lat5):.0f} ms**", "",
           "## Failures (shown on purpose)", ""]
    fails = [x for x in rows if not x["engine_ok"]]
    md += [f"- **{x['id']}** `{x['q']}` → {x['engine']}" + (f"  \n  _{x['note']}_" if x["note"] else "") for x in fails] or ["- none"]
    md += ["", "## Per-question", "", "| ID | Question | This system | Baseline | ms |", "|---|---|---|---|---:|"]
    md += [f"| {x['id']} | {x['q']} | {'✅' if x['engine_ok'] else '❌'} {x['engine']} | {'✅' if x['baseline_ok'] else '❌'} {x['baseline']} | {x['latency_ms']:.0f} |" for x in rows]
    (ROOT / "preprocessing" / "eval" / "results.md").write_text("\n".join(md) + "\n")
    (ROOT / "preprocessing" / "eval" / "results.json").write_text(json.dumps(rows, indent=2))
    print("\n".join(md[:30]))


if __name__ == "__main__":
    main()
