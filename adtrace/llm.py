"""Optional plain-English summary layer. OFF unless ANTHROPIC_API_KEY is set.

The LLM never sees the corpus, only the already-verified quotes for one answer.
Its output is then checked sentence by sentence:
  * every sentence must cite at least one quote id like [E1]
  * every number / designator in the sentence must appear in the quotes it cites
Sentences that fail are dropped, and the UI says how many were dropped.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

API = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("ADTRACE_MODEL", "claude-haiku-5-5")

PROMPT = """You explain an FAA proposed airworthiness directive to an aircraft maintenance engineer.
Use ONLY the numbered quotes below. Do not add facts, numbers, part numbers, intervals or procedures that are not in the quotes.
Write 2-4 short sentences. End every sentence with the quote ids it relies on, like [E1] or [E1][E3].
If the quotes say the proposal was withdrawn, say that first.

Status: {status}
Quotes:
{quotes}"""

TOKEN = re.compile(r"\b(?=[A-Z0-9./-]*\d)[A-Z0-9][A-Z0-9./-]*\b")


def verify_summary(text: str, quotes: list[str]) -> dict:
    kept, dropped = [], []
    for s in re.split(r"(?<=[.!?\]])\s+(?=[A-Z])", text.strip()):
        s = s.strip()
        if not s:
            continue
        ids = [int(x) for x in re.findall(r"\[E(\d+)\]", s)]
        src = " ".join(quotes[i - 1] for i in ids if 1 <= i <= len(quotes))
        body = re.sub(r"\[E\d+\]", "", s)
        missing = [t for t in TOKEN.findall(body) if t.strip(".") not in src]
        if ids and src and not missing:
            kept.append(s)
        else:
            dropped.append({"sentence": s, "reason": "no citation" if not ids else f"not in cited quotes: {missing}"})
    return {"text": " ".join(kept), "kept": len(kept), "dropped": dropped}


def summarise(res: dict, timeout: float = 8.0) -> dict | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    items = list(res.get("evidence") or []) + list((res.get("applicability") or {}).get("evidence") or [])
    if not key or res.get("status") not in ("answer", "partial") or not items:
        return None
    quotes = [e["text"] for e in items]
    status = (res.get("banner") or {}).get("text", "") + " " + ((res.get("applicability") or {}).get("verdict") or "")
    body = {"model": MODEL, "max_tokens": 300, "messages": [{"role": "user", "content": PROMPT.format(
        status=status.strip(), quotes="\n".join(f"[E{i+1}] {q}" for i, q in enumerate(quotes)))}]}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
        text = "".join(b.get("text", "") for b in out.get("content", []))
    except Exception as ex:  # never let the LLM break the answer
        return {"error": f"LLM unavailable ({type(ex).__name__}); showing quotes only."}
    v = verify_summary(text, quotes)
    v["model"] = MODEL
    v["quote_ids"] = {f"E{i+1}": e["cite"]["chunk"] for i, e in enumerate(items)}
    return v
