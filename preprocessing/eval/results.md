# Evaluation results

60 hand-written questions (written by the team from the dataset; see `eval/questions.json`). Baseline = naive RAG: BM25 top chunk over all documents, always answers, no filters, no version or applicability logic.

| Task | Questions | This system | Naive RAG baseline |
|---|---:|---:|---:|
| Find the right AD from a plain-English question | 16 | 16/16 | 16/16 |
| Look up by docket / AD number | 3 | 3/3 | 1/3 |
| Applicability verdict (model + serial) | 18 | 17/18 | 7/18 |
| Version / withdrawal awareness | 7 | 7/7 | 3/7 |
| List every AD for a model | 3 | 3/3 | 0/3 |
| Superseded AD: before / why / after | 3 | 3/3 | 0/3 |
| Correctly refuse, clarify or flag missing value | 10 | 10/10 | 0/10 |
| **Total** | **60** | **59/60 (98%)** | **27/60 (45%)** |

## Trust metrics

- Quoted sentences verified verbatim against their cited source: **339/339**
- Right document in top 5 retrieved (where a gold document exists): **50/50**
- False refusals on answerable questions: **0/50**
- Asked to clarify on answerable questions: **2/50** (A04, A05)
- Latency on real corpus (2042 chunks, 0.93M chars): median **10 ms**, p95 **15 ms**
- Latency on 5x replicated corpus (10210 chunks, synthetic, latency test only): median **46 ms**, p95 **59 ms**

## Failures (shown on purpose)

- **A10** `Piper PA-28-140 fuel selector valve S/N 28-100` → NOT REQUIRED (WITHDRAWN)  
  _28-100 is in the PA-28-150/160/180 row of the table, not the PA-28-140 row. Expected to FAIL: ranges are not tied to table rows._

## Per-question

| ID | Question | This system | Baseline | ms |
|---|---|---|---|---:|
| T01 | 787 leading edge outboard geared rotary actuator replacement with lockout actuator | ✅ correct document | ✅ correct document | 13 |
| T02 | 787 low range radio altimeter coaxial cables non-computed data | ✅ correct document | ✅ correct document | 9 |
| T03 | 737 video monitors detaching from passenger service unit rails, missing retention lanyards | ✅ correct document | ✅ correct document | 9 |
| T04 | CH-47D forward transmission support beam fitting fatigue cracks | ✅ correct document | ✅ correct document | 9 |
| T05 | A350 galley lower attachment studs fractured | ✅ correct document | ✅ correct document | 9 |
| T06 | A320 family engine fire switch self-releasing causing uncommanded in-flight shutdown | ✅ correct document | ✅ correct document | 9 |
| T07 | Bell 429 display units losing display, traffic system disabled, flight manual supplement | ✅ correct document | ✅ correct document | 10 |
| T08 | Pratt & Whitney Canada automated damage tracking system under-counting low cycle fatigue | ✅ correct document | ✅ correct document | 9 |
| T09 | rudder drive corrosion on BO-209 aircraft | ✅ correct document | ✅ correct document | 9 |
| T10 | Dornier 228 hydraulic pump motor damaged by excessive heat | ✅ correct document | ✅ correct document | 9 |
| T11 | Dauphin engine compartment firewall seal gaps angle brackets | ✅ correct document | ✅ correct document | 8 |
| T12 | 757-200 door 2 intercostals behind door stops and hinges inspection | ✅ correct document | ✅ correct document | 10 |
| T13 | AE 3007A compressor cone shaft cyclic life limit increase | ✅ correct document | ✅ correct document | 11 |
| T14 | 787 center wing fuel tank sealant side-of-body not cured during production | ✅ correct document | ✅ correct document | 10 |
| T15 | AS350B3 main rotor servo-control upper ball bearing end tightening torque loss | ✅ correct document | ✅ correct document | 9 |
| T16 | PW4077 HPC 15th stage disk ultrasonic scan inspection part number omitted | ✅ correct document | ✅ correct document | 10 |
| A01 | Does this apply to my DHC-8-400, S/N 4050? | ✅ APPLIES IF ADOPTED | ✅ APPLIES | 10 |
| A02 | Does this apply to my DHC-8-400, S/N 4002? | ✅ DOES NOT APPLY | ❌ APPLIES | 10 |
| A03 | DHC-8-400 S/N 4001 horizontal stabilizer shims | ✅ APPLIES IF ADOPTED | ✅ APPLIES | 10 |
| A04 | EMB-120 serial 120004 applicability | ✅ clarified; per-candidate verdict correct (APPLIES IF ADOPTED: S/N 120004 is listed…) | ✅ APPLIES | 10 |
| A05 | EMB-120 serial 120400 applicability | ✅ clarified; per-candidate verdict correct (DOES NOT APPLY: S/N 120400 is outside ev…) | ❌ wrong document 2000-NM-120-AD | 10 |
| A06 | 750XL serial number 115 | ✅ APPLIES IF ADOPTED | ✅ APPLIES | 10 |
| A07 | 750XL serial number 125 | ✅ DOES NOT APPLY | ❌ APPLIES | 10 |
| A08 | Reims F406 S/N F406-0091 | ✅ APPLIES IF ADOPTED | ✅ APPLIES | 9 |
| A09 | Reims F406 S/N F406-0090 | ✅ DOES NOT APPLY | ❌ APPLIES | 9 |
| A10 | Piper PA-28-140 fuel selector valve S/N 28-100 | ❌ NOT REQUIRED (WITHDRAWN) | ❌ APPLIES | 11 |
| A11 | 757-200 serial number 50 door 2 intercostals | ✅ CANNOT DETERMINE | ❌ APPLIES | 10 |
| A12 | Hamilton Sundstrand 14SF-7 propeller auxiliary pump serial number 387 | ✅ NOT REQUIRED (WITHDRAWN) | ❌ APPLIES | 10 |
| A13 | Hamilton Sundstrand 14SF-7 propeller auxiliary pump serial number 388 | ✅ NOT REQUIRED (WITHDRAWN) | ✅ APPLIES | 10 |
| A14 | Sikorsky S-76B life raft deployment handle | ✅ APPLIES IF ADOPTED (ONLY WITH PART INSTALLED) | ❌ APPLIES | 10 |
| A15 | Hamilton Sundstrand 14SF-7 propeller auxiliary pump serial number 96 | ✅ NOT REQUIRED (WITHDRAWN) | ✅ APPLIES | 10 |
| A16 | Hamilton Sundstrand 14SF-7 propeller auxiliary pump serial number 95 | ✅ NOT REQUIRED (WITHDRAWN) | ❌ APPLIES | 10 |
| A17 | AS350B3 serial 1234 servo-control upper ball bearing torque | ✅ APPLIES IF ADOPTED | ❌ APPLIES | 9 |
| A18 | PW4077 HPC 15th stage disk serial P-727001 | ✅ CANNOT DETERMINE | ❌ APPLIES | 10 |
| V01 | What does docket 2000-NM-120-AD require for the EMB-120? | ✅ flagged withdrawn | ✅ flagged withdrawn | 10 |
| V02 | Robinson R22 droop and teeter stop bracket replacement | ✅ flagged withdrawn | ❌ withdrawal NOT flagged | 10 |
| V03 | Gulfstream G150 serial number 210 | ✅ flagged withdrawn | ❌ withdrawal NOT flagged | 10 |
| V04 | CL-604 serial number 5400 | ✅ flagged withdrawn | ❌ withdrawal NOT flagged | 16 |
| V05 | Bell UH-1H type certificate holder names update and additional military helicopter models | ✅ correct document | ✅ correct document | 16 |
| V06 | What is the latest version of docket FAA-2006-23739? | ✅ flagged withdrawn | ✅ flagged withdrawn | 15 |
| V07 | 787 leading edge outboard geared rotary actuator replacement with lockout actuator | ✅ final AD number flagged | ❌ final AD number NOT flagged | 16 |
| R01 | Are there any ADs for the Airbus A350-1000 cabin oxygen system? | ✅ refuse | ❌ answer | 11 |
| R02 | What does docket FAA-2099-9999 require? | ✅ refuse | ❌ answer | 9 |
| R03 | What does AD 2031-01-01 require? | ✅ refuse | ❌ answer | 9 |
| R04 | What is the torque value for the main rotor servo-control upper ball bearing nut on AS350B3? | ✅ partial | ❌ answer | 10 |
| R05 | best pizza in Singapore | ✅ refuse | ❌ answer | 11 |
| R06 | Cessna 172 seat rail inspection | ✅ refuse | ❌ answer | 9 |
| R07 | How many flight hours between inspections for the ATR 72 propeller blades? | ✅ refuse | ❌ answer | 10 |
| R08 | Boeing 787 inspection | ✅ clarify | ❌ answer | 10 |
| R09 | Tesla Model 3 battery recall | ✅ clarify | ❌ answer | 10 |
| R10 | Embraer 190 wing spar corrosion | ✅ refuse | ❌ answer | 10 |
| I01 | Summarise docket FAA-2026-2713 | ✅ correct document | ❌ answer: got FAA-2026-11800 | 9 |
| I02 | Which proposal supersedes AD 2026-09-02? | ✅ correct document | ✅ correct document | 10 |
| I03 | FAA-2025-5394 | ✅ correct document | ❌ answer: got FAA-2026-8785 | 9 |
| L01 | Which airworthiness directives concern PW4077 engines? | ✅ list: 1 dockets listed | ❌ baseline returns one document, not a list | 12 |
| L02 | List all ADs for the 737-800 | ✅ list: 19 dockets listed | ❌ baseline returns one document, not a list | 12 |
| L03 | Which proposals affect CF6-80C2 engines? | ✅ list: 3 dockets listed | ❌ baseline returns one document, not a list | 10 |
| C01 | Which proposal supersedes AD 2026-09-02? | ✅ old AD 2026-09-02: before/why/after = 1/1/1 | ❌ baseline has no before/after view | 10 |
| C02 | A330 radio management panel data synchronization AFM revision | ✅ old AD 2025-09-07: before/why/after = 1/1/1 | ❌ baseline has no before/after view | 11 |
| C03 | AW169 airworthiness limitations section incorrect material referenced | ✅ old AD 2024-05-06: before/why/after = 1/1/1 | ❌ baseline has no before/after view | 15 |
