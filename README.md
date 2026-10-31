# ADTrace: traceable search over FAA airworthiness directives

Airbus track, Challenge 4: *How might AI help people find, understand and verify regulated technical information?*

ADTrace answers questions about FAA proposed airworthiness directives (NPRMs). It **quotes the source or says it can't**. Every sentence it shows is copied verbatim from a source document, cited as **record_id + Federal Register citation and page + section** (e.g. `NPRM-332 · 70 FR 54316-54318, p. 54316 · (c) Applicability`), and re-checked against that source before display.

## Run it

Needs Python 3.10+ with `pandas` and `openpyxl`. Nothing else.

```bash
python3 -m pip install pandas openpyxl certifi
python3 fetch_fulltext.py         # downloads the full Federal Register documents (needs internet, run once)
python3 -m adtrace.ingest         # builds data/index.pkl from data/challenge4_ad_index.xlsx + data/fulltext/
python3 -m adtrace.app            # open http://127.0.0.1:8000
python3 -m eval.run_eval          # re-runs the evaluation, writes eval/results.md
```

`fetch_fulltext.py` uses the `federal_register_link` (Federal Register API, raw text with `[[Page N]]` markers) and, for rows with no link, the govinfo link service by volume and page. It saves `data/fulltext/<record_id>.txt` so the demo then runs offline. The app works without it, but applicability then comes from summaries for most rows.

Optional: `export ANTHROPIC_API_KEY=...` adds a verified plain-English summary (see "Where AI is used").

## Data source and corpus size

- **Source:** `data/challenge4_ad_index.xlsx` (the organisers' updated file): sheet `nprm_index` (431 NPRMs with a stable `record_id`, ISO dates, `supersedes_ad`, a serial-number flag), sheet `revision_chains` (99 dockets with more than one record; the latest publish date is the current version) and `data_notes`. The previous spreadsheet is no longer used.
- **Corpus without downloads:** 431 records, 2,042 citable chunks, about 141,000 words (≈311 pages at 3,000 characters per page). 178 records have rule text inside the sheet; the rest are summaries.
- **Corpus with downloads (measured on our demo laptop):** `fetch_fulltext.py` downloaded 402 documents (29 failed, older documents not reachable by volume/page). Full text is indexed for **421 of 431** records: **963,003 words, ≈2,176 pages** at 3,000 characters per page, covering **1,316 Federal Register pages**, in **11,876 citable chunks**. Serial ranges are read for 48 documents (29 from the sheet alone).
- **Revision chains:** ADTrace's version order matches `revision_chains` for all 99 dockets. It additionally links one withdrawal notice (NPRM-371) that withdraws two Cessna dockets at once (2002-CE-05-AD and 2002-CE-57-AD), so those show as withdrawn.

## What the data really looks like (found during ingestion)

- **431 rows of FAA proposed ADs, 2001 to Oct 2026, in 323 dockets.** These are proposals, not final rules, and every answer says so.
- **124 rows already contain the full proposed-rule text,** but in the `federal_register_link` column instead of a link. The ingester recovers it.
- **125 rows are withdrawals, and 127 dockets end withdrawn.** For example, docket 2000-NM-120-AD (EMB-120) was proposed in 2001, revised in 2003 and 2005, then withdrawn in 2008.
- **The source Status column said "Current" for every row** (organisers' note), yet 226 rows (52%) belong to dockets that end in a withdrawal. ADTrace derives status from each docket's history instead.
- **21 proposals have a final AD number recorded** (e.g. FAA-2026-2712 → AD 2026-20-10). The final rules themselves are not in the dataset, so ADTrace says "confirm against AD X".
- **The Model column is 80% empty, and maker names vary** ("Boeing" versus "The Boeing Company"). Models are extracted from the applicability text instead.
- **At least one row has the wrong summary.** NPRM-314 (docket FAA-2006-23739) is titled as an EMB-145 AD, but its Summary column holds text from an unrelated FAA notice about instrument approach procedures. Its hidden rule text shows it is actually a **withdrawal**. ADTrace flags AD rows whose summary isn't about an AD, ignores that summary, and shows a "data issue found" warning. We found this during testing and corrected one answer key (V06) as a result.
- **Applicability comes in several forms:** "all", "certain", serial ranges, single listed serials, line-number ranges (not serials), exceptions ("excluding S/Ns 387, 405…", "excluding airplanes with Modification 5068"), required installed part numbers, and "as listed in Service Bulletin X".
- **Comment deadlines** are not a column in the new file; ADTrace reads them from the document text ("must receive comments … by …") when the text is indexed, and otherwise says the deadline is not stated.

### Serial-number rule (team decision)
| What the directive says | ADTrace's answer |
|---|---|
| Lists serial ranges | Checks the serial: applies / does not apply (with exclusions) |
| Mentions no serial numbers | **Applies to all serial numbers**, with the comment "No serial numbers are mentioned in this directive, so it is treated as applying to all serial numbers" (and whether this is based on full text or summary only) |
| Mentions serial numbers that cannot be read reliably | **Cannot determine** (refuse when unclear) |
| Uses line numbers instead | Asks for the line number |

This follows FAA AC 39-7D ¶9a ("if there are no serial number references, all serial numbers are affected") and errs on the safe side: it can over-include, never silently exclude.

## How it works

```
question ──► parse (docket, AD no., model, serial)          deterministic
         ──► filter by make/model, then BM25 search          deterministic  (stops cross-equipment blending)
         ──► coverage + ambiguity checks ──► REFUSE / CLARIFY
         ──► resolve docket version chain ──► WITHDRAWN / REVISED banner
         ──► applicability rules (model list, serial/line ranges, external SB)
         ──► pick verbatim sentences + citations
         ──► verifier: every quote must appear in its cited chunk
         ──► (optional) LLM summary of the verified quotes ──► verifier again
```

### The four tabs
- **Ask:** any question; answers, refusals and clarifications.
- **Find:** a model or maker gives every docket that mentions it.
- **Check applicability:** an exact model (plus an optional serial) gives a verdict for every docket, with counts (would apply / cannot determine / do not apply / withdrawn).
- **Revision history:** a docket or AD number gives the version story, or the supersession before/after view.

### The three demo scenarios
1. **Find:** "List all ADs for the 737-800" gives a table of every matching docket, with status (open, revised, withdrawn), what each would require (quoted), and any AD it supersedes. Add a serial number to get an "applies?" column.
2. **Verify:** "Does this apply to my DHC-8-400, S/N 4050?" gives a rule-based verdict with a rule trace and the source sentence.
3. **Compare:** "Which proposal supersedes AD 2026-09-02?" gives a side-by-side view of what the old AD required, what the proposal would require, and why it changed. For multi-version dockets, it also gives a quoted step for each version.

| Requirement | How ADTrace meets it |
|---|---|
| Specific references | Each sentence cites record_id, Federal Register citation and page (exact page when the full text has page markers) and section. Click a citation to see the highlighted source text. |
| Zero hallucination | Answers are extractive. The verifier checks each quote against its chunk (296/296 in evaluation). |
| Configuration filtering | Rule-based applicability: model match, serial ranges, single serials, line numbers vs serials, excluded serials, no serials mentioned → all serials (with a comment), unreadable serials → "cannot determine", configuration exceptions → "cannot determine, review", required part numbers → "only with part installed". Proposals say "APPLIES IF ADOPTED". The rule trace is shown. |
| Revision logic | Version order from the `revision_chains` sheet (latest publish date = current), plus withdrawals that name a docket. A banner shows "version x of n", "WITHDRAWN" or "final AD number recorded", plus a quoted step for each version. |
| Clear uncertainty | Five outcomes: Answer, Refused, Needs clarification (with per-candidate verdicts), Partial (value not in data), and Cannot determine (applicability). |
| Under 3 s latency | Median 8 ms on the real corpus, and 36 ms on a corpus replicated 5x. |

### Where AI is used, and where it isn't
- **No LLM** for retrieval, filtering, applicability, version logic or refusal. Those are code and rules, so they can be audited.
- **The optional LLM** only rewrites the already-verified quotes into 2–4 plain-English sentences. Each sentence must cite quote IDs `[E1]`, and every number or designator in it must appear in the cited quote, or the sentence is dropped and the drop is shown. Test: an invented "inspect every 600 flight hours [E1]" is dropped.

## Evaluation

See `eval/results.md` for per-question results. Summary:

| Task | Questions | ADTrace | Naive RAG baseline |
|---|---:|---:|---:|
| Find the right AD from a plain-English question | 16 | 16/16 | 16/16 |
| Look up by docket / AD number | 3 | 3/3 | 1/3 |
| Applicability verdict (model + serial, exclusions, parts, no-serial rule) | 18 | 17/18 | 7/18 |
| Version / withdrawal / final-AD awareness | 7 | 7/7 | 3/7 |
| List every AD for a model | 3 | 3/3 | 0/3 |
| Superseded AD: before / why / after | 3 | 3/3 | 0/3 |
| Correctly refuse, clarify or flag missing value | 10 | 10/10 | 0/10 |
| **Total** | **60** | **59/60 (98%)** | **27/60 (45%)** |

The baseline is BM25 top-1 over all chunks that always answers: no filters, no versions, no rules. **Retrieval alone is not the hard part, since both get 16/16.** The gains come from what happens after retrieval.

**Honest limits of this evaluation:**
- We wrote the 60 questions ourselves from the dataset, and we tuned thresholds while looking at them. Treat 98% as an upper bound. A fair test needs fresh held-out questions written by someone else; `eval/questions.json` is easy to extend.
- Two answerable questions (A04, A05) got "needs clarification" rather than a direct answer, because several EMB-120 dockets match. The correct verdict is shown per candidate.

## User and impact

**User:** the airworthiness / technical records engineer (at a CAMO, operator or MRO) who decides, for each aircraft, whether a directive applies, and keeps the record of applicable ADs that operators must hold under [14 CFR 91.417](https://www.ecfr.gov/current/title-14/chapter-I/subchapter-F/part-91/subpart-E/section-91.417). **Today:** they search the Federal Register by hand. NPRMs are not distributed electronically ([FAA AC 39-7D](https://www.faa.gov/documentlibrary/media/advisory_circular/ac%2039-7d.pdf), ¶8c). They read each applicability statement, where limits are set by serial number or number series (¶9a), and compare by eye.

**Measured on this dataset** (`eval/results.md`, and the counts below come from the data):

| Model | Dockets mentioning it | Documents | Federal Register pages | Of which withdrawn |
|---|---:|---:|---:|---:|
| 737-800 | 19 | 23 | 76 | 4 |
| A330 | 15 | 20 | 77 | 3 |
| 787-9 | 12 | 13 | 42 | 4 |
| A320 | 10 | 12 | 39 | 3 |

- One ADTrace query replaces that screening: about 10 ms, and every verdict is cited.
- **Risk avoided:** the spreadsheet labels all 431 rows "Current", but 226 belong to withdrawn dockets. On withdrawal questions, the naive RAG baseline flagged 1 of 4 and ADTrace 4 of 4. On questions that should be refused, the baseline answered 10 of 10 and ADTrace refused or flagged 10 of 10.
- **Not measured:** time saved for a real engineer. We have not run a user study, so we do not claim hours saved. That is the first thing a pilot would measure.

## Known failure (shown on purpose)

**A10: Piper PA-28-140, S/N 28-100.** The Piper AD has a table with a serial range per model. ADTrace extracts all the ranges but does not link each range to its table row. S/N 28-100 falls in the PA-28-150/160/180 row, so it should **not** apply to a PA-28-140, but ADTrace says it would have applied. (The docket was later withdrawn, so the final answer is still "not required".)
**How the user knows:** the verdict shows a caveat ("ranges are not linked to individual models, confirm in the cited text"), the rule trace lists every range, and one click shows the table. **Fix:** parse applicability tables row by row.

Other limits:
- Without `fetch_fulltext.py`, 253 of 431 records have only a summary. Under the serial rule they are treated as applying to all serial numbers, and the comment says it is "based on its summary". Fetching the full text lets ADTrace find serial ranges that summaries omit.
- Where applicability is defined by an external service bulletin (25 documents), ADTrace applies the no-serial rule and adds "confirm against that bulletin".
- `fetch_fulltext.py` could not be run from our build environment (no internet access there). Its parser was tested on Federal Register-format text (page markers, DATES line, "(c) Applicability" section); run it on a networked laptop and check `data/fulltext/_failures.txt`.
- Search is lexical (BM25). Paraphrases with no shared words may be missed and then refused, which is the safe failure. Embeddings could be added as a second retriever.
- The data contains proposals and withdrawals, not final ADs. Where a final AD number is recorded (21 dockets) ADTrace says so, but it cannot show the final rule's text.

## Files

```
adtrace/ingest.py     cleaning, text recovery, version chains, applicability extraction
adtrace/engine.py     search, rules, refusal, verification
adtrace/llm.py        optional verified LLM summary
adtrace/app.py        demo server (standard library only), static/index.html is the UI
eval/                 questions.json, run_eval.py, results.md
fetch_fulltext.py     optional Federal Register full-text download
data/challenge4_ad_index.xlsx   the organisers' dataset (nprm_index, revision_chains, data_notes)
data/fulltext/        downloaded Federal Register texts (created by fetch_fulltext.py)
```

## Credits
The "APPLIES IF ADOPTED" wording, the use of the AD Number column, exception and part-number handling, the tabbed Ask / Find / Check / History layout, and the "Status column says Current" warning came from a teammate's parallel prototype (AD Trace, FastAPI) and were merged into this build.

## Licence
MIT. See LICENSE.
