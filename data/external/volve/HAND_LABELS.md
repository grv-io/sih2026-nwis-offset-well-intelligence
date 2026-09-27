# Volve hand-labelled ground truth (Round 3)

One labeller, working independently. 40 ingested Volve DDR documents for well `15_9-F-12`
(2007-06-13 -> 2007-07-22, report days 1-40), read in full, **without** looking at the `events`
table first (to avoid anchoring on the model's output). The `events` table was only opened after
`data/external/volve/hand_truth.json` was written, to compute the comparison in the last section.

Source docs: `data/external/volve/docs/15_9-F-12/DDR_2007-MM-DD.txt`. Truth file:
`data/external/volve/hand_truth.json` (35 `DrillingEvent` rows, validated with `nwis.schema.DrillingEvent(**d)`).

## Labelling rules

1. **Taxonomy and non-events** follow `nwis/ingest/extract_events.py`'s `SYSTEM_PROMPT` exactly:
   the 12 `EventType`s, the "do NOT extract: routine drilling progress/tripping/casing/surveys/mud
   checks/rig maintenance with no NPT/meetings/BOP tests that pass" list, and Example 3's terse
   shorthand ("gain of X m3" -> kick; "tightspot/tight hole/took N MT overpull while POOH" ->
   wellbore_instability; "static losses" or losses beyond seepage -> mud_loss).
2. **Inclusion bar for equipment/software NPT (`npt_other`)**: the text must state either (a) an
   explicit halt/termination/postponement ("halted operations", "terminated operation", "drilling
   operation is postphoned", "stand-by due to..."), or (b) a specific fault found and a remedial
   action taken ("found fault in load sensor and in software", "found bursted hose in dragchain,
   repaired"). A bare one-liner with no identified fault and no stated consequence ("Had problems
   with PRS ... troubleshot PRS", "Problem with HTS software. Investigated same", "PRS not
   operational - ET calibrated PRS") was **excluded** as too thin to call a genuine event — these
   read like the routine snags every rig day has, not incidents. This line is subjective; the
   borderline cases below list where it was drawn.
3. **mud_loss vs. connector leaks**: `mud_loss` per the taxonomy is fluid lost *into the
   formation*. The wellhead/riser/LPDR mechanical connector seawater leak that this well tracks on
   and off from 06-23 through 06-28 (70-500 l/hr, monitored via trip tank) is **not** a formation
   loss, so escalations of it were labelled `npt_other`, not `mud_loss`, even though the word
   "loss" appears in the text.
4. **Multi-day continuing incidents** (raw water pump failure 06-22->06-24; Siemens
   software work 07-04->07-06) are recorded as **one event on the day they are first/most clearly
   narrated as an operational impact**, not repeated on every subsequent day that mentions
   "waiting on..." — except where a later day's report describes a **new, distinct** escalation or
   action (e.g. 07-05's "Terminated operation due to Siemens uploading of Software" and 07-06's
   "Stand-by due to priority of Siemens..." read as their own fresh mentions of impact, so both
   were kept as separate events from 07-04's). This mirrors how a per-document extraction pipeline
   would actually encounter the text — each day's chunk is scored independently — while avoiding
   inventing five identical copies of "pump is still broken."
5. **Closely-spaced obstructions on one trip** (e.g. two tight spots 9 m apart on the same RIH run)
   are merged into **one** `wellbore_instability` event with an `interval_top/base_md_m` spanning
   both, rather than one event per obstruction, to avoid over-fragmenting a single episode.
6. **depth_md_m**: the explicit depth in the sentence when stated; otherwise the nearest depth
   implied by the surrounding narrative (noted explicitly in `free_text`, e.g. "no explicit depth
   on this line; used the last stated RIH depth"); `null` when neither applies (mostly subsea/deck
   equipment incidents with no well MD, e.g. the VX-ring and RA-source incidents).
7. **formation**: `null` throughout — no Sodir formation tops are available for 15/9-F-* wells
   (matches the header note in every document and `nwis/schema.py`'s formation list, which is
   Upper-Assam-specific and doesn't apply to Volve).
8. **Confidence**: 0.9 for canonical, explicit, unambiguous events (matches the shorthand patterns
   in Example 3, or has an explicit "halted/terminated" statement); 0.7-0.8 for clear events with
   some judgement in depth or category; 0.5-0.6 for genuinely borderline calls kept in rather than
   dropped (noted individually below and in each event's `free_text`).
9. **severity**: `low` for brief/minor items with no lasting impact, `medium` for a normal
   NPT-causing fault or tight-hole episode, `high` for events that forced a trip/major rig
   reconfiguration or were safety-significant (raw water pump failure, the dropped-DP derrick
   incident, the final cuttings-conveyer breakdown that forced a POOH to make repairs).

## Per-document table

| Document (date) | n events | Note |
|---|---|---|
| 2007-06-13 | 0 | Rig skid F-4 -> F-12; no drilling. |
| 2007-06-14 | 0 | 36" hole opener BHA RIH/drilling to 221 m; PRS snag and TDS maintenance, both routine/no fault stated. |
| 2007-06-15 | 1 | npt_other: PORT FWD crane down halted ops. |
| 2007-06-16 | 0 | 30" conductor run and cemented; routine. |
| 2007-06-17 | 1 | npt_other: burst disk on top-up pipe ruptures during pressure test; cement unit overpressure alarm + control-screen freeze; remedial cement job. |
| 2007-06-18 | 0 | LPDR/landing string run; routine overpull test (weak-label FP precedent, excluded). |
| 2007-06-19 | 0 | Tensioning system, diverter rig-up; routine. |
| 2007-06-20 | 0 | 26" drillout BHA RIH; minor "leakage above diverter" mentioned but no remedial action/impact stated -- excluded as too thin. |
| 2007-06-21 | 0 | Drilled cement/shoe track; routine, "potential for stuck pipe" toolbox talk excluded per audit precedent. |
| 2007-06-22 | 2 | npt_other: forward PRS proximity-switch fault (borderline, conf 0.5); npt_other: raw water pump failure postpones drilling (high severity). |
| 2007-06-23 | 0 | Waiting on raw water pump; connector-leak monitoring (200 l/hr) -- background reading only, no response taken, excluded. |
| 2007-06-24 | 0 | Waiting on raw water pump repair continues (same event as 06-22, not re-logged); connector-leak monitoring (100 l/hr) excluded. |
| 2007-06-25 | 1 | npt_other: riser leak in wellhead connector (70-80 l/hr) forces PU off bottom; leak self-resolves. |
| 2007-06-26 | 2 | kick: gain of 1 m3 @ 473 m (canonical shorthand); mud_loss: minor 140 l/1.5 hr @ 618 m. |
| 2007-06-27 | 1 | mud_loss: losses 3 m3/15 min @ 655 m, escalating to 200 l/min, total 10 m3 (one episode). |
| 2007-06-28 | 1 | npt_other: connector leak escalates to 500 l/hr. |
| 2007-06-29 | 1 | npt_other: rig ESD system shutdown mid-drilling, workaround used. |
| 2007-06-30 | 0 | POOH, displace, routine. |
| 2007-07-01 | 0 | Diverter removal; weak-label "overpull lifting diverter" excluded per audit precedent. |
| 2007-07-02 | 0 | LPDR recovery; routine. |
| 2007-07-03 | 0 | LPDR laid down; brief "PRS software failure" repaired with no stated fault/impact, excluded. |
| 2007-07-04 | 1 | npt_other: POOH to shoe, wait on Siemens software download (ESD by-passed). |
| 2007-07-05 | 1 | npt_other: operation terminated due to Siemens software upload. |
| 2007-07-06 | 3 | npt_other: standby for Siemens priority; npt_other: racked back std, ROV failure; npt_other (high): 3 1/2" DP double falls out of derrick latch onto drill floor, ops terminated. |
| 2007-07-07 | 0 | 26" hole drilled 431-901 m into Utsira; routine. |
| 2007-07-08 | 0 | 26" hole drilled to 1369 m TD; routine, log evaluation delay not an NPT event. |
| 2007-07-09 | 2 | wellbore_instability: tight spot 1330-1348 m POOH, backreamed, 20 MT overpull (canonical); npt_other: mud pump #3 out of service (borderline, conf 0.5). |
| 2007-07-10 | 1 | wellbore_instability: two closely-spaced obstructions RIH 1093-1142 m, worked through both. |
| 2007-07-11 | 1 | wellbore_instability: 20 MT overpull POOH over two intervals (1303-1340 m and 1002-1102 m), lubricated out. |
| 2007-07-12 | 1 | wellbore_instability: tight spot 419 m running 20" CSG, lubricated through. |
| 2007-07-13 | 4 | wellbore_instability: tight spot 923-937 m running CSG; npt_other: casing tong load-sensor + software fault; wellbore_instability: two obstructions 1083-1120 m running CSG; npt_other (borderline, conf 0.5): 18 3/4" wellhead handling difficulty. |
| 2007-07-14 | 0 | Wellhead landed, cemented; weak-label "25 MT overpull on landing string" excluded per audit precedent; "Problem with HTS software. Investigated same" too thin, excluded. |
| 2007-07-15 | 0 | L/O landing string/BHA, HPDR rig-up; routine. |
| 2007-07-16 | 0 | HPDR/BOP run continues; routine. |
| 2007-07-17 | 1 | npt_other: VX seal ring comes off during TBC landing attempt (current), ROV replaces it; weak-label "10 MT overpull to confirm connector" excluded per audit precedent. |
| 2007-07-18 | 0 | BOP nippled up and pressure tested ok; routine. |
| 2007-07-19 | 3 | npt_other: hydraulic hose bursts on iron roughneck; npt_other: AFT PRS malfunction, dragchain hose repaired, switched to FWD PRS; npt_other: repeated RA-source install failures, switched to backup. |
| 2007-07-20 | 2 | npt_other: mudpump #1/#3 leakage and malfunction, repaired; npt_other: choke sensors faulty (I/O module), repaired before choke drill. |
| 2007-07-21 | 1 | npt_other: cuttings conveyer belt overload, safety-switch shutdown, had to stop pumping. |
| 2007-07-22 | 4 | npt_other: conveyer belt stops drilling @ 1436 m, restricted ROP after; npt_other (high): conveyer belt total stoppage @ 1546 m forces POOH + repairs; wellbore_instability: tight hole 1467-1546 m, 30 MT overpull (canonical, missed by LLM both prior rounds); mud_loss: static losses ~100 l/hr (borderline, conf 0.5). |

**19 of 40 documents have zero genuine events** (rig-move, riser/BOP running, casing prep,
toolbox-talk-heavy days, and days where the only candidate lines were routine/toolbox-talk/pull-test
false positives per the round-1/2 audit).

## Totals by event type (35 events, 21 documents)

| event_type | count |
|---|---|
| npt_other | 24 |
| wellbore_instability | 7 |
| mud_loss | 3 |
| kick | 1 |
| **Total** | **35** |

No `stuck_pipe`, `overpressure`, `torque_spike`, `cementing_issue`, `fishing_operation`,
`gas_show`, `twist_off`, or `lost_bha` events were found as genuine in these 40 documents — this
well's real NPT in this window is overwhelmingly equipment/software breakdowns (`npt_other`) and
tight-hole/obstruction episodes (`wellbore_instability`), consistent with the round-1/2 audit's
finding that the weak-label vocabulary (mostly `stuck_pipe`-flavoured regex) was largely false
positives (toolbox talks, pull tests, "jar" in BHA lists).

## Borderline cases (confidence 0.5, judgement calls)

- **06-22 forward PRS proximity-switch fault** — a specific fault *was* identified ("found failure
  on proximity switch"), unlike the terser PRS/software one-liners excluded elsewhere, but no halt
  duration or NPT is stated and drilling resumed immediately. Kept in at low confidence rather than
  dropped.
- **07-09 mud pump #3 out of service** — stated as a fact ("One mud pump out of services") with no
  described halt; resolved the next day. Kept once, not duplicated on 07-10 while it was still down.
- **07-13 subsea-wellhead handling difficulty** — "had problems getting it vertical without the
  possibility of damaging the threads" is a real difficulty but resolved smoothly the next day with
  no NPT stated.
- **07-22 static losses ~100 l/hr** — matches the round-2 audit's genuine-but-minor mud_loss
  finding (caught by the LLM at conf 0.50, review-queue territory in Round 2). A second, smaller
  reading later in the same report ("Static losses 30 l/hrs") was treated as a continuation of the
  same declining background condition, not counted twice.
- **Excluded despite some textual signal**: 06-20 "leakage above diverter" (no remedial action
  stated); 06-29 "took weight ... worked through tight spots and continued drilling" while reaming
  (routine reaming language, not the specific measured-interval-plus-remedial-action pattern of
  Example 3); 06-29 WOB fluctuation / "time-out to discuss further action" at 934 m (no explicit
  problem framing, resolved via routine mud-weight change); 07-13 "broke CSG jnt out, incorrect
  torque, inspected threads" (routine QC during casing running); 07-21 inconclusive XLOT (leak-off
  test gave no definitive result; standpipe valves were tested and found ok, so no confirmed fault
  to attach an event to). All of the connector-leak background readings at 150-250 l/hr on
  06-23/06-24 were excluded as monitoring narration with no operational response, versus the
  06-25 and 06-28 entries which describe an actual response (PU off bottom; explicit "increasing
  leak rate" escalation call-out).
- **06-17 burst disk / cement-unit overpressure** classified `npt_other` here, not
  `cementing_issue` — the root failure is the burst disk and the Halliburton control-unit lockup,
  not a defect in a cement job; the remedial cement job is the *fix*. The model (both in this
  session's DB and the round-1/2 audit) called it `cementing_issue`; this is a genuine, defensible
  taxonomy-boundary disagreement, discussed below.

## Eval against the Volve DB

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

**Honest reading — what the numbers mean and where they're generous:**

- **kick (1/1)** — clean catch, the canonical "gain of 1 m3" shorthand the prompt was built around.
- **mud_loss (3/3 recall, precision 0.6)** — both false positives are informative, not noise: one
  (06-27, no depth) is the LLM splitting the single 3 m3/15min-then-200l/min loss episode into two
  extracted rows (over-extraction of one real event, not a hallucination); the other (06-28, "loss
  of 500 l/hr") is a genuine real event that we classified differently — it's the wellhead/riser
  connector leak escalating, which we call `npt_other` (mechanical leak, not a formation loss) and
  the model calls `mud_loss`. Reasonable people could label either way; see rule 3 above.
- **wellbore_instability (1/7 recall)** — the pipeline still only catches the single tight-spot
  pattern closest to the prompt's Example 3 wording (07-09, "tightspot F/1348m T/1330m ...
  backreamed ... overpull"). It misses every tight-spot/obstruction encountered while **running
  20" casing** (07-12, 07-13 x2) and every POOH-overpull episode phrased slightly differently from
  the example (07-11's "lubricated out due to over pull of 20 MT", and — most notably — **07-22's
  1546 m "Tight hole took approx 30 MT overpull"**, the exact event flagged as still-missed in
  `VOLVE_EVAL_NOTES.md`'s Round 2 table, and still missed here. Casing-running tight spots in
  particular look like a distinct phrasing pattern ("RIH with 20" CSG ... tight spot ... set
  down/off N MT ... washed/lubricated through") that the current few-shot prompt (drilling-only
  Example 3) doesn't cover.
- **npt_other (5/24 recall nominal — real coverage is thinner still)** — one of the five "matches"
  (07-21) is a **coincidental match**: the extracted row has no depth, so the eval's depth check
  never disqualifies it, and it matched our 07-21 conveyer-belt-overload truth purely because both
  are `npt_other` events in the same document. The extracted text is actually about a *different*
  real issue in the same report ("cold mud overflowing the shakers", reduced ROP) that we did not
  log as a separate event (see rules; judged too thin/routine, arguably a miss on our side too).
  Setting that one aside, the pipeline reliably catches only the most explicit "halted/terminated
  operation" sentences (crane down, ESD shutdown, Siemens software wait) and misses the much larger
  population of "found a fault, repaired it" equipment narratives that make up most of this
  category by count: the raw water pump failure, the dropped-DP-derrick incident, the casing tong
  load-sensor fault, both PRS malfunctions, the mudpump leaks, the choke-sensor fault, the RA-source
  install failures, the VX-ring incident, and the hydraulic hose burst are all missed. `npt_other`
  is explicitly "use sparingly" in the system prompt with only drilling-adjacent framing, and it
  shows: the model under-extracts non-drilling rig/surface equipment breakdowns as a class.
- **stuck_pipe (0 truth, 1 fp) / wellbore_instability boundary** — the one `stuck_pipe` extraction
  ("Hit obstruction at 1083m washed down from 1083m to 1095m") is literally half of our
  wellbore_instability truth event at 1083-1120 m (07-13) — same incident, different label. We
  labelled every "obstruction encountered / worked through" pattern as `wellbore_instability`
  (tight hole) rather than `stuck_pipe` (which we reserved for a drillstring that could not move at
  all); the model split on this one instance. Another genuine, defensible taxonomy-boundary call,
  not a hallucination.
- **cementing_issue (0 truth, 1 fp)** — the 06-17 burst-disk/cement-unit event, see rule note
  above: we call it `npt_other`, the pipeline calls it `cementing_issue`. Same underlying real
  event, different label, not a wrong extraction.

**Net honest read for the deck**: on real North Sea DDR text, hand-labelled against a genuinely
independent read of all 40 documents, the pipeline's **precision is still good (0.71 overall, and
every disagreement traced above is a defensible taxonomy-boundary call or an over-extraction of a
real event, not a hallucination)**. **Recall is the real gap**, and it's now visible at the
category level rather than just "recall is bad": the pipeline is tuned to one tight-spot phrasing
and one register of "halted operations" language, and it misses roughly three-quarters of the
genuine wellbore-instability and equipment-NPT events in this window because they're phrased in
the many other ways rigs actually write these up (casing-running tight spots, "found fault in
X, repaired", "terminated operation due to..."). This is a stronger, harder number than the
regex-weak-label eval it replaces (P 0.21/R 0.18 raw, or the ~4/5 hand-audited-subset recall quoted
in Round 2) precisely because it is honest about the denominator: 35 real events, not 17 mostly-
false-positive regex hits.
