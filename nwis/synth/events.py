"""Ground-truth DrillingEvent generation per well.

extraction_method=synthetic_truth, extraction_confidence=1.0 (these are the label, not an
extraction result). 2-8 events per well, formation-consistent depths, hot-zone-boosted
hazard mix, lognormal NPT hours, and phrase-bank cause/remedy text.

ILLUSTRATIVE / SYNTHETIC.
"""
from __future__ import annotations

import numpy as np

from nwis.schema import DrillingEvent, EventType as ET, Severity
from nwis.synth import hazards
from nwis.synth.wells import SynthWell

EVENTS_PER_WELL_RANGE = (2, 9)  # rng.integers high is exclusive -> 2..8

SEVERITY_BY_HAZARD: dict[ET, list[tuple[Severity, float]]] = {
    ET.mud_loss: [(Severity.low, 0.35), (Severity.medium, 0.40), (Severity.high, 0.20), (Severity.critical, 0.05)],
    ET.kick: [(Severity.medium, 0.25), (Severity.high, 0.45), (Severity.critical, 0.30)],
    ET.stuck_pipe: [(Severity.medium, 0.30), (Severity.high, 0.45), (Severity.critical, 0.25)],
    ET.overpressure: [(Severity.medium, 0.30), (Severity.high, 0.40), (Severity.critical, 0.30)],
    ET.torque_spike: [(Severity.low, 0.50), (Severity.medium, 0.40), (Severity.high, 0.10)],
    ET.cementing_issue: [(Severity.low, 0.40), (Severity.medium, 0.40), (Severity.high, 0.20)],
    ET.fishing_operation: [(Severity.high, 0.55), (Severity.critical, 0.45)],
    ET.wellbore_instability: [(Severity.low, 0.30), (Severity.medium, 0.45), (Severity.high, 0.25)],
    ET.gas_show: [(Severity.low, 0.45), (Severity.medium, 0.40), (Severity.high, 0.15)],
    ET.npt_other: [(Severity.low, 0.6), (Severity.medium, 0.4)],
}

# NPT hours-lost lognormal params (mean/sigma of underlying normal, in log-hours) by severity.
NPT_LOGNORMAL_PARAMS: dict[Severity, tuple[float, float]] = {
    Severity.low: (0.6, 0.4),      # median ~1.8 hr
    Severity.medium: (1.5, 0.5),   # median ~4.5 hr
    Severity.high: (2.4, 0.5),     # median ~11 hr
    Severity.critical: (3.2, 0.6),  # median ~24 hr
}

CAUSE_REMEDY_BANK: dict[ET, list[tuple[str, str]]] = {
    ET.mud_loss: [
        ("Lost circulation {rate} bbl/hr while drilling {fm} sand, partial returns only.",
         "Pumped {vol} bbl LCM pill, reduced ROP, regained partial returns."),
        ("Losses seen while running casing through {fm}, static losses continuing on connections.",
         "Spotted {vol} bbl high-fluid-loss pill across the loss zone and monitored pit levels."),
        ("Total losses encountered in {fm}, no returns to surface for {hrs} hrs.",
         "Pumped cement plug to seal loss zone, waited on cement, tested integrity before resuming."),
    ],
    ET.kick: [
        ("Flow-check positive, pit gain of {vol} bbl and flow-out exceeding flow-in while in {fm}.",
         "Shut in well, closed BOP, circulated kill mud per driller's method."),
        ("Gas-cut mud and increasing pit volume observed drilling {fm}, well flowing.",
         "Hard shut-in, recorded SIDPP/SICP, weighted up mud and circulated bottoms up."),
    ],
    ET.stuck_pipe: [
        ("Pipe stuck while tripping through {fm}, unable to work free, torque and drag increasing prior to stick.",
         "Worked pipe with max overpull, spotted {vol} bbl pipe-lax pill, freed after {hrs} hrs."),
        ("Differential sticking in {fm}, no rotation or reciprocation possible.",
         "Reduced mud weight, spotted spotting fluid across pay zone, freed string on jarring."),
    ],
    ET.overpressure: [
        ("SPP trend increasing and drag rising while drilling into {fm}, suspected transition zone.",
         "Raised mud weight in {vol}-ppg steps, monitored trip gas, continued drilling with caution."),
        ("Well kicked back while circulating out of {fm}, gas readings elevated on trip gas.",
         "Weighted mud up to balance formation pressure, circulated bottoms up before resuming."),
    ],
    ET.torque_spike: [
        ("Sharp torque increase on contact with {fm}, ROP dropped to near zero.",
         "Reduced WOB and RPM, reamed interval, resumed drilling at reduced parameters."),
    ],
    ET.cementing_issue: [
        ("Poor cement bond / losses during primary cementing of casing shoe in {fm}.",
         "Squeeze cemented shoe track, pressure tested to spec before drilling out."),
        ("Excessive cement losses to formation while cementing across {fm}.",
         "Staged cement job with lightweight lead slurry and full tail string."),
    ],
    ET.fishing_operation: [
        ("Following stuck pipe in {fm}, back-off performed, fish left in hole.",
         "Ran fishing BHA, latched fish with overshot, recovered string to surface."),
    ],
    ET.wellbore_instability: [
        ("Hole instability / cavings on surface while drilling {fm}, tight hole on connections.",
         "Increased mud weight and viscosity, short-tripped to condition hole."),
        ("Wellbore washout suspected in {fm}, oversized cuttings at surface.",
         "Adjusted mud rheology, ran wiper trips before continuing."),
    ],
    ET.gas_show: [
        ("Background gas rise while drilling {fm}, connection gas trending up.",
         "Increased mud weight slightly, monitored trend, continued drilling."),
    ],
    ET.npt_other: [
        ("Rig repair / equipment downtime while working in {fm} section.",
         "Repaired equipment, resumed operations after downtime."),
    ],
}


def _weighted_choice(rng: np.random.Generator, items: list, weights: list):
    p = np.array(weights, dtype=float)
    p = p / p.sum()
    idx = rng.choice(len(items), p=p)
    return items[idx]


def _sample_severity(event_type: ET, rng: np.random.Generator) -> Severity:
    opts = SEVERITY_BY_HAZARD.get(event_type, [(Severity.medium, 1.0)])
    sevs = [o[0] for o in opts]
    weights = [o[1] for o in opts]
    return _weighted_choice(rng, sevs, weights)


def _sample_npt_hours(severity: Severity, rng: np.random.Generator) -> float:
    mu, sigma = NPT_LOGNORMAL_PARAMS[severity]
    return float(np.clip(rng.lognormal(mu, sigma), 0.25, 240.0))


def _formation_interval(sw: SynthWell, formation: str) -> tuple[float, float, float, float]:
    """(top_md, base_md, top_tvd, base_tvd) for one formation of one well."""
    tvd_by_f = dict(sw.tops_tvd)
    md_by_f = dict(sw.tops_md)
    order = [f for f, _ in sw.tops_tvd]
    idx = order.index(formation)
    top_tvd = tvd_by_f[formation]
    top_md = md_by_f[formation]
    if idx + 1 < len(order):
        base_tvd = tvd_by_f[order[idx + 1]]
        base_md = md_by_f[order[idx + 1]]
    else:
        base_tvd = float(sw.trajectory.tvd[-1])
        base_md = float(sw.trajectory.md[-1])
    return top_md, base_md, top_tvd, base_tvd


def _sample_depth_in_formation(sw: SynthWell, formation: str, rng: np.random.Generator) -> tuple[float, float, float]:
    top_md, base_md, top_tvd, base_tvd = _formation_interval(sw, formation)
    if base_tvd <= top_tvd:
        depth_tvd = top_tvd
    else:
        depth_tvd = float(rng.uniform(top_tvd, base_tvd))
    depth_md = float(sw.trajectory.md_at_tvd(depth_tvd))
    depth_md = float(np.clip(depth_md, top_md, base_md if base_md > top_md else top_md + 1.0))
    offset = depth_tvd - top_tvd
    return depth_md, depth_tvd, offset


def _cause_remedy(event_type: ET, formation: str, rng: np.random.Generator, hours: float, vol: float) -> tuple[str, str]:
    bank = CAUSE_REMEDY_BANK.get(event_type, [("NPT event in {fm}.", "Operations resumed.")])
    cause_tmpl, remedy_tmpl = bank[int(rng.integers(0, len(bank)))]
    fmt = dict(fm=formation, rate=int(rng.uniform(10, 80)), vol=int(vol), hrs=round(hours, 1))
    return cause_tmpl.format(**fmt), remedy_tmpl.format(**fmt)


def generate_events(sw: SynthWell, rng: np.random.Generator) -> list[DrillingEvent]:
    well = sw.well
    weight_table = hazards.hazard_weight_table(well.lat, well.lon)
    formations_present = {f for f, _ in sw.tops_tvd}
    pairs = [(f, et) for (f, et) in weight_table.keys() if f in formations_present]
    weights = [weight_table[p] for p in pairs]

    n_events = int(rng.integers(*EVENTS_PER_WELL_RANGE))
    events: list[DrillingEvent] = []

    for i in range(n_events):
        formation, event_type = _weighted_choice(rng, pairs, weights)
        depth_md, depth_tvd, offset = _sample_depth_in_formation(sw, formation, rng)
        severity = _sample_severity(event_type, rng)
        hours = _sample_npt_hours(severity, rng)
        vol = float(rng.uniform(20, 120))
        cause, remedy = _cause_remedy(event_type, formation, rng, hours, vol)
        mw = hazards.mud_weight_sample(formation, event_type, rng)

        event = DrillingEvent(
            event_id=f"{well.well_id}-EVT-{i + 1:03d}",
            well_id=well.well_id,
            source_document_id="",  # backfilled once the day's DDR document is generated
            source_page_ref=f"{well.well_id}#pending",
            report_date=None,  # backfilled from the drilling-day schedule
            depth_md_m=round(depth_md, 1),
            depth_tvd_m=round(depth_tvd, 1),
            formation=formation,
            formation_offset_from_top_m=round(offset, 1),
            event_type=event_type,
            severity=severity,
            hours_lost_npt=round(hours, 2),
            cause=cause,
            remedy=remedy,
            mud_weight_ppg_at_event=round(mw, 2),
            free_text=f"{cause} {remedy}",
            extraction_confidence=1.0,
            extraction_method="synthetic_truth",
            reviewed_by_human=True,
        )
        events.append(event)

        if (event_type == ET.stuck_pipe and severity in (Severity.high, Severity.critical)
                and rng.random() < hazards.FISHING_AFTER_SEVERE_STUCK_PIPE_PROB):
            fish_depth_md = depth_md + float(rng.uniform(2.0, 15.0))
            fish_depth_tvd = float(sw.trajectory.tvd_at_md(fish_depth_md))
            fish_offset = fish_depth_tvd - _formation_interval(sw, formation)[2]
            fish_hours = _sample_npt_hours(Severity.high, rng)
            fish_cause, fish_remedy = _cause_remedy(ET.fishing_operation, formation, rng, fish_hours, vol)
            events.append(DrillingEvent(
                event_id=f"{well.well_id}-EVT-{i + 1:03d}F",
                well_id=well.well_id,
                source_document_id="",
                source_page_ref=f"{well.well_id}#pending",
                report_date=None,
                depth_md_m=round(fish_depth_md, 1),
                depth_tvd_m=round(fish_depth_tvd, 1),
                formation=formation,
                formation_offset_from_top_m=round(fish_offset, 1),
                event_type=ET.fishing_operation,
                severity=_sample_severity(ET.fishing_operation, rng),
                hours_lost_npt=round(fish_hours, 2),
                cause=fish_cause,
                remedy=fish_remedy,
                mud_weight_ppg_at_event=round(hazards.mud_weight_sample(formation, ET.fishing_operation, rng), 2),
                free_text=f"{fish_cause} {fish_remedy}",
                extraction_confidence=1.0,
                extraction_method="synthetic_truth",
                reviewed_by_human=True,
            ))

    # Cementing issues at casing shoe points (independent of the sampled hazard mix above).
    for shoe_fm in hazards.CASING_SHOE_FORMATIONS:
        if shoe_fm not in formations_present:
            continue
        if rng.random() < hazards.CEMENTING_ISSUE_PROB_PER_SHOE:
            top_md, _, top_tvd, _ = _formation_interval(sw, shoe_fm)
            severity = _sample_severity(ET.cementing_issue, rng)
            hours = _sample_npt_hours(severity, rng)
            vol = float(rng.uniform(20, 120))
            cause, remedy = _cause_remedy(ET.cementing_issue, shoe_fm, rng, hours, vol)
            idx = len(events) + 1
            events.append(DrillingEvent(
                event_id=f"{well.well_id}-EVT-{idx:03d}C",
                well_id=well.well_id,
                source_document_id="",
                source_page_ref=f"{well.well_id}#pending",
                report_date=None,
                depth_md_m=round(top_md, 1),
                depth_tvd_m=round(top_tvd, 1),
                formation=shoe_fm,
                formation_offset_from_top_m=0.0,
                event_type=ET.cementing_issue,
                severity=severity,
                hours_lost_npt=round(hours, 2),
                cause=cause,
                remedy=remedy,
                mud_weight_ppg_at_event=round(hazards.mud_weight_sample(shoe_fm, ET.cementing_issue, rng), 2),
                free_text=f"{cause} {remedy}",
                extraction_confidence=1.0,
                extraction_method="synthetic_truth",
                reviewed_by_human=True,
            ))

    events.sort(key=lambda e: e.depth_md_m or 0.0)
    return events
