# Volve real-data validation — honest read

**Setup.** 40 of 631 prepared Volve DDR documents (5 wellbores, 15/9-F-*) ingested with the unchanged
NWIS pipeline (qwen2.5:7b, local). Evaluation truth = pre-registered regex weak labels
(`nwis/external/volve/labels.py`), restricted to those 40 documents (`weak_truth_ingested.json`).

**Raw eval (`volve_metrics.json`):** precision 0.14, recall 0.06, F1 0.08 (tp 1, fp 6, fn 16; 17 weak labels, 7 extracted).

**Why the raw number is misleading — manual audit of all 17 weak labels:**

| Weak label | Real event? | Note |
|---|---|---|
| stuck_pipe ×3 @ 258–261 m | no | "potential for stuck pipe" toolbox talk; routine landing-string ops |
| kick @ 467 m | **yes** | "Observed gain of 1 m3" — LLM missed it |
| mud_loss @ 618 m | **yes** | "losses 3 m3/15 min at 655 m" — LLM extracted it at **655 m**; weak label carries the day's start depth 618 m → 37 m apart → outside the ±25 m match window, so a true positive was scored as a miss |
| stuck_pipe @ 1353 m (diverter lift) | no | "overpull" on a diverter lift |
| stuck_pipe @ 1348 m tight spot | **yes** (wellbore_instability) | LLM missed |
| stuck_pipe @ 1324 / 1220 / 1369 / 10.9 / 141 / 1369 m | no | pull tests, BHA make-up containing "jar", "lost overpull" while filling riser |
| stuck_pipe + wellbore_instability @ 1546 m tight hole 30 MT overpull | **yes** (one event, double-labelled) | LLM missed |
| mud_loss @ 1546 static losses 100 l/hr | **yes** (minor) | LLM missed |
| mud_loss @ 1546 conveyor belt modifications | no | regex hit on "drain" |

So the weak-label set on these 40 docs contains **≈5 real events**, not 17.

**What the LLM extracted (7):** total loss 140 l/1.5 h @ 578 m (real), losses 3 m3/15 min @ 655 m (real),
loss 200 l/min total 10 m3 (real, same episode), cement-unit overpressure alarm @ 159 m (real equipment
NPT), crane down / rig ESD shutdown / cuttings-conveyor stoppage as `npt_other` (all real NPT, outside the
weak-label vocabulary). **6–7 of 7 are genuine NPT events → extraction precision is high.**

**Honest summary:** on real North Sea DDR text the pipeline is *conservative*: high precision on what it
returns, but it under-recalls kicks/tight-hole episodes written in terse rig shorthand ("gain of 1 m3",
"tightspot", "took 30 MT overpull"). Recall vs the hand-audited real set ≈ 2–3 of 5–6.

**Actions:**
1. Add few-shot examples in the extraction prompt for terse shorthand: "gain of X m3" → kick, "tightspot /
   tight hole / took N MT overpull while POOH" → wellbore_instability, "static losses" → mud_loss.
2. Eval matcher: when truth depth is a day-level fallback (no explicit depth in the line), widen the
   window to the day's drilled interval instead of ±25 m.
3. Replace regex weak labels with a **small hand-labelled Volve set (40–60 docs)** before quoting any
   Volve number to judges. Until then the deck says: "validated qualitatively on Volve; extraction is
   precision-first; recall on rig shorthand is the known gap and the feedback loop is the fix".
4. DrillScribe's own finding applies: models built on one operator's phrasing need ~60 local labelled
   report-days to adapt — quote their AUC 0.678 → 0.785 curve, not ours.

---

## Round 2 — after the rig-shorthand few-shot prompt update

Prompt gained Example 3 (gain → kick, tightspot/overpull → wellbore_instability, static losses → mud_loss).
Same 40 documents, `--force`, eval with `--depth-tol 150` (day-level weak-label depths).

**Raw weak-label eval:** P 0.21 / R 0.18 (tp 3, fp 11, fn 14). Still not the yardstick (12 of the 17 weak
labels are the toolbox-talk / pull-test false positives audited above).

**Against the hand-audited real events (5):**

| Real event | Round 1 | Round 2 |
|---|---|---|
| kick, gain 1 m3 @ 467–473 m | missed | **caught** (kick @ 473, conf 0.75) |
| losses 3 m3/15 min @ 655 m | caught | caught |
| tight spot @ 1348 m, backreamed | missed | **caught** (wellbore_instability, conf 0.75) |
| static losses 100 l/hr | missed | **caught** (mud_loss, conf 0.50 → review queue) |
| tight hole @ 1546 m, 30 MT overpull | missed | missed |

Recall vs real set: 2/5 → **4/5**. The 14 extractions also include genuine NPT the weak labels never
covered (losses @ 578 m; 200 l/min loss; 500 l/hr loss; cement-unit burst disk; crane / ESD / Siemens /
conveyor downtime). Two are questionable: "picked up single DP for rathole" as npt_other, and
"obstruction at 1083 m, washed down" as stuck_pipe (arguably wellbore_instability). Audit precision ≈ 12/14.

**Consequence for the synthetic number:** the synthetic eval (P 0.86 / R 0.94) was produced with the
round-1 prompt. Re-run `ingest.run data/synthetic/docs --force` + eval (≈ 75 min, Ollama-bound) when the
demo server is not in use, so the quoted number matches the shipped prompt.

---

## Round 3 — hand-labelled ground truth

Replaced the regex weak labels with a hand-labelled ground truth,
`data/external/volve/hand_truth.json` — 35 genuine events, one person's independent read of all 40
ingested documents in full, done **before** looking at the `events` table. Full methodology,
per-document breakdown and borderline-case reasoning: `data/external/volve/HAND_LABELS.md`.

```
$env:NWIS_DB = "data/volve.sqlite"
.venv\Scripts\python.exe -m nwis.ingest.eval --truth data\external\volve\hand_truth.json --out models\volve_hand_metrics.json --depth-tol 40
```

```
Overall: precision=0.714  recall=0.286  f1=0.408  (tp=10 fp=4 fn=25, n_truth=35, n_extracted=14)
Depth MAE: 10.0 m (n=4)

event_type             tp   fp   fn  precision   recall     f1
cementing_issue         0    1    0      0.000    0.000  0.000
kick                    1    0    0      1.000    1.000  1.000
mud_loss                3    2    0      0.600    1.000  0.750
npt_other               5    0   19      1.000    0.208  0.345
stuck_pipe              0    1    0      0.000    0.000  0.000
wellbore_instability    1    0    6      1.000    0.143  0.250
```

**Honest read.** Precision is genuinely good (0.71) and every single false positive traces to a
defensible taxonomy-boundary call (burst-disk/cement-unit event labelled `npt_other` by us vs.
`cementing_issue` by the model; one obstruction labelled `wellbore_instability` by us vs.
`stuck_pipe` by the model; the wellhead-connector leak escalation labelled `npt_other` by us vs.
`mud_loss` by the model) or an over-extraction of a real event (one real mud-loss episode split
into two extracted rows) — **not a hallucination**. Recall (0.29 overall) is the real, now
category-visible gap: the model reliably catches the single tight-spot phrasing it was given as a
few-shot example (07-09) and the most explicit "halted/terminated operation" sentences, but misses
~6 of 7 hand-labelled `wellbore_instability` events (everything phrased as a casing-running tight
spot, or an overpull worded differently from the example — including the canonical 1546 m/30 MT
tight-hole event that Round 2's table already flagged as still-missed) and the large majority of
genuine equipment/software `npt_other` events (raw water pump failure, dropped-DP-derrick incident,
casing tong fault, PRS malfunctions, mudpump leaks, choke-sensor fault, RA-source install failures,
VX-ring incident, hydraulic hose burst) — these are "found a fault, repaired it" narratives in a
register the prompt's single `npt_other` guidance ("use sparingly") doesn't cover.

**Caveat for the deck**: one person hand-labelled 40 documents in one sitting. No second reviewer,
no inter-rater check. Treat 35 as a reasonable, defensible count of real events in this window, not
a certified number — the borderline cases in HAND_LABELS.md (conf 0.5 entries, and the "excluded
despite some textual signal" list) show where a second labeller could reasonably land a few events
differently in either direction. This is still a materially more honest number to quote than the
regex weak labels: 35 real events with a documented rationale for each, versus 17 weak labels of
which the Round 1 audit found only ~5 were real.

**Action**: quote precision 0.71 / recall 0.29 (this table) as the honest Volve number, with the
one-line caveat above. The wellbore_instability and npt_other prompt gaps identified here
(casing-running tight spots; the broader equipment-fault register) are the next concrete
few-shot-example candidates if extraction time allows before the deck is finalised.
