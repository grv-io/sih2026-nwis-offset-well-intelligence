"""Daily Drilling Reports (DDR) + one Well Completion Report (WCR) per well.

Compressed drilling calendar (documented below and in data/synthetic/README.md): a full
Upper Assam well realistically drills over 60-150+ days, but this generator represents each
well's campaign as 6-10 "report-days" purely to keep the PDF corpus small
(target ~150-250 DDR PDFs total across 30 wells, per the Phase-1 spec). Each report-day still
carries a real calendar date (one calendar day apart) and a depth interval sized by a
per-formation ROP baseline, so slow formations (Barail, Kopili, Sylhet) get proportionally
less footage per report-day than fast ones (Alluvium, Tipam) -- the ROP signal is real, the
report cadence is compressed.

DDR PDFs are generated only for: every day with an event (T), T-1, T+1, and every 5th
report-day -- exactly the "days with events +/- 1 plus every 5th day" rule from the spec.

ILLUSTRATIVE / SYNTHETIC.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

from nwis.schema import DrillingEvent, SourceDocument
from nwis.synth import hazards
from nwis.synth.wells import SynthWell

PAGE_W, PAGE_H = letter

SLOPPY_SPELLINGS: dict[str, list[str]] = {
    "Alluvium": ["Alluvium", "ALLUVIUM"],
    "Dhekiajuli": ["Dhekiajuli", "Siwalik", "DHEKIAJULI FM"],
    "Namsang": ["Namsang", "NAMSANG FM", "Namsang Fm"],
    "Girujan": ["Girujan", "Gurjan", "GIRUJAN CLAY", "Girujan Fm"],
    "Tipam": ["Tipam", "TIPAM SST", "Tipam Ss", "Tipam Sand"],
    "Barail": ["Barail", "BARAIL", "Barail Coal-Shale", "Barail Gp"],
    "Kopili": ["Kopili", "KOPILI SH", "Kopili Shale"],
    "Sylhet": ["Sylhet", "SYLHET LST", "Sylhet Lst"],
    "Basement": ["Basement", "BASEMENT", "Bsmt"],
}

ABBREVIATIONS: list[tuple[str, str]] = [
    ("drilling", "drlg"), ("circulation", "circ"), ("formation", "fm"),
    ("approximately", "approx"), ("hours", "hrs"), ("observed", "obsvd"),
    ("continued", "cont'd"), ("operations", "ops"), ("connection", "conx"),
    ("bottoms up", "btms up"), ("tripping", "trppg"),
]

OPS_PHRASE_BANK = [
    "Drlg ahead in {fm} @ {rop:.0f} m/hr avg.",
    "Circ btms up, checked for gas, resumed drlg.",
    "Survey taken, inc/azi within plan.",
    "Short wiper trip to condition hole, no issues.",
    "POOH for bit change, RIH, resumed drlg {fm}.",
    "Slips and mud checks per program, ops normal.",
    "Cont'd drlg, monitoring torque/drag trend.",
    "Flow check ok, no signs of influx, drlg ahead.",
]

CASING_SIZE_BY_SHOE: dict[str, str] = {
    "Namsang": "13-3/8in surface",
    "Girujan": "9-5/8in intermediate",
    "Tipam": "9-5/8in intermediate-2",
    "Barail": "7in production",
}


def _sloppy(formation: str, rng: np.random.Generator) -> str:
    opts = SLOPPY_SPELLINGS.get(formation, [formation])
    return opts[int(rng.integers(0, len(opts)))]


def _abbreviate(text: str, rng: np.random.Generator, prob: float = 0.5) -> str:
    for full, short in ABBREVIATIONS:
        if full in text.lower() and rng.random() < prob:
            text = text.replace(full, short).replace(full.capitalize(), short)
    return text


def _typo(text: str, rng: np.random.Generator, prob: float = 0.06) -> str:
    words = text.split(" ")
    for i, w in enumerate(words):
        if len(w) > 4 and rng.random() < prob:
            j = int(rng.integers(1, len(w) - 1))
            chars = list(w)
            chars[j], chars[j - 1] = chars[j - 1], chars[j]
            words[i] = "".join(chars)
    return " ".join(words)


# --------------------------------------------------------------------------- #
# Drilling-day schedule (ROP-informed, compressed calendar)
# --------------------------------------------------------------------------- #
@dataclass
class DaySchedule:
    day: int
    date: date
    md_start: float
    md_end: float
    formation: str


def build_schedule(sw: SynthWell, rng: np.random.Generator) -> list[DaySchedule]:
    num_days = int(rng.integers(6, 11))
    order = [f for f, _ in sw.tops_md]
    bounds_md = [md for _, md in sw.tops_md] + [float(sw.trajectory.md[-1])]
    rop = {f: hazards.rop_baseline(f, rng) for f in order}

    # cumulative "time weight" (arbitrary units) at each formation boundary
    cum_time = [0.0]
    for i, f in enumerate(order):
        seg_len = bounds_md[i + 1] - bounds_md[i]
        cum_time.append(cum_time[-1] + seg_len / rop[f])
    cum_time_arr = np.array(cum_time)
    bounds_arr = np.array(bounds_md)

    total_time = cum_time_arr[-1]
    day_times = np.linspace(0.0, total_time, num_days + 1)
    day_end_md = np.interp(day_times, cum_time_arr, bounds_arr)
    day_end_md[-1] = bounds_arr[-1]

    def formation_at(md_val: float) -> str:
        idx = 0
        for i, b in enumerate(bounds_md[:-1]):
            if md_val >= b:
                idx = i
        return order[idx]

    schedule: list[DaySchedule] = []
    spud = sw.well.spud_date or date(2015, 1, 1)
    for d in range(1, num_days + 1):
        md_start, md_end = float(day_end_md[d - 1]), float(day_end_md[d])
        schedule.append(DaySchedule(
            day=d, date=spud + timedelta(days=d), md_start=md_start, md_end=md_end,
            formation=formation_at(md_end),
        ))
    return schedule


def assign_event_days(events: list[DrillingEvent], schedule: list[DaySchedule]) -> dict[str, int]:
    """Mutates each event's report_date in place; returns event_id -> day index."""
    day_ends = np.array([s.md_end for s in schedule])
    mapping: dict[str, int] = {}
    for e in events:
        depth = e.depth_md_m or 0.0
        idx = int(np.searchsorted(day_ends, depth, side="left"))
        idx = min(idx, len(schedule) - 1)
        e.report_date = schedule[idx].date
        mapping[e.event_id] = schedule[idx].day
    return mapping


def days_to_render(schedule: list[DaySchedule], event_days: set[int]) -> set[int]:
    days = set()
    for d in event_days:
        days.update({d - 1, d, d + 1})
    days.update(d.day for d in schedule if d.day % 5 == 0)
    valid = {s.day for s in schedule}
    return days & valid


# --------------------------------------------------------------------------- #
# Text content builders
# --------------------------------------------------------------------------- #
def ddr_lines(sw: SynthWell, day: DaySchedule, events_today: list[DrillingEvent],
              rng: np.random.Generator) -> list[str]:
    well = sw.well
    fm_disp = _sloppy(day.formation, rng)
    footage = day.md_end - day.md_start
    lines = [
        f"DAILY DRILLING REPORT (DDR)",
        f"Well: {well.well_id}  ({well.name})   Field: {well.field}   Basin: {well.basin}",
        f"Report Date: {day.date.isoformat()}   Report Day: {day.day}",
        f"Depth: {day.md_start:.0f} -> {day.md_end:.0f} m MD  (progress {footage:.0f} m)",
        f"Current Fm: {fm_disp}",
        "-" * 70,
        "OPERATIONS SUMMARY:",
    ]
    n_ops_lines = int(rng.integers(2, 5))
    for _ in range(n_ops_lines):
        phrase = OPS_PHRASE_BANK[int(rng.integers(0, len(OPS_PHRASE_BANK)))]
        rop_val = hazards.rop_baseline(day.formation, rng)
        txt = phrase.format(fm=fm_disp, rop=rop_val)
        txt = _abbreviate(txt, rng)
        txt = _typo(txt, rng)
        lines.append("  " + txt)

    lines.append("-" * 70)
    lines.append("NPT / EVENTS:")
    if events_today:
        for e in events_today:
            fm_e = _sloppy(e.formation or day.formation, rng)
            txt = (f"  {e.hours_lost_npt:.1f} hrs NPT - {e.event_type.value.upper()} @ "
                   f"{e.depth_md_m:.0f} m MD ({fm_e}), sev={e.severity.value}: {e.cause} {e.remedy}")
            txt = _abbreviate(txt, rng, prob=0.3)
            lines.append(txt)
    else:
        lines.append("  No NPT recorded. Ops normal.")
    return lines


def wcr_lines(sw: SynthWell, all_events: list[DrillingEvent]) -> list[list[str]]:
    well = sw.well
    page1 = [
        "WELL COMPLETION REPORT (WCR)",
        f"Well: {well.well_id}  ({well.name})   Field: {well.field}   Basin: {well.basin}",
        f"Spud Date: {well.spud_date}   Status: {well.status}",
        f"Lat/Lon: {well.lat:.4f}, {well.lon:.4f}   KB Elev: {well.kb_elev_m:.1f} m",
        f"Trajectory: {well.trajectory_type.value}   TD (MD): {well.td_md_m:.0f} m   "
        f"TD (TVD): {sw.trajectory.tvd[-1]:.0f} m",
        "",
        "FORMATION TOPS:",
        f"{'Formation':<14}{'Top MD (m)':>12}{'Top TVD (m)':>14}{'Base MD (m)':>14}{'Base TVD (m)':>14}",
    ]
    order = [f for f, _ in sw.tops_md]
    tvd_by_f = dict(sw.tops_tvd)
    md_by_f = dict(sw.tops_md)
    for i, f in enumerate(order):
        base_md = md_by_f[order[i + 1]] if i + 1 < len(order) else well.td_md_m
        base_tvd = tvd_by_f[order[i + 1]] if i + 1 < len(order) else sw.trajectory.tvd[-1]
        page1.append(f"{f:<14}{md_by_f[f]:>12.0f}{tvd_by_f[f]:>14.0f}{base_md:>14.0f}{base_tvd:>14.0f}")

    page2 = [
        "CASING PROGRAM:",
        f"{'Shoe Formation':<18}{'Casing':<24}{'Set Depth MD (m)':>18}",
    ]
    for shoe_fm, size in CASING_SIZE_BY_SHOE.items():
        if shoe_fm in md_by_f:
            page2.append(f"{shoe_fm:<18}{size:<24}{md_by_f[shoe_fm]:>18.0f}")
    page2 += ["", "MUD PROGRAM SUMMARY (pore-frac window, ppg):",
              f"{'Formation':<14}{'Pore lo':>9}{'Pore hi':>9}{'Frac lo':>9}{'Frac hi':>9}"]
    for f in order:
        lo1, hi1, lo2, hi2 = hazards.MUD_WEIGHT_WINDOW_PPG[f]
        page2.append(f"{f:<14}{lo1:>9.1f}{hi1:>9.1f}{lo2:>9.1f}{hi2:>9.1f}")

    page3 = ["PROBLEMS ENCOUNTERED:"]
    if all_events:
        for e in sorted(all_events, key=lambda x: x.depth_md_m or 0.0):
            page3.append(f"  {e.report_date} | {e.depth_md_m:.0f} m MD | {e.formation} | "
                         f"{e.event_type.value} | {e.severity.value} | {e.hours_lost_npt:.1f} hrs NPT")
            page3.append(f"    Cause: {e.cause}")
            page3.append(f"    Remedy: {e.remedy}")
    else:
        page3.append("  None recorded.")

    return [page1, page2, page3]


# --------------------------------------------------------------------------- #
# PDF / image rendering
# --------------------------------------------------------------------------- #
def _draw_text_pdf(pages: list[list[str]], out_path: Path) -> None:
    c = rl_canvas.Canvas(str(out_path), pagesize=letter)
    for page_lines in pages:
        c.setFont("Courier", 9)
        y = PAGE_H - 50
        for line in page_lines:
            c.drawString(40, y, str(line)[:115])
            y -= 12
            if y < 40:
                c.showPage()
                c.setFont("Courier", 9)
                y = PAGE_H - 50
        c.showPage()
    c.save()


def _render_page_image(lines: list[str], rng: np.random.Generator) -> Image.Image:
    w, h = 1700, 2200
    bg = int(rng.uniform(222, 244))
    arr = np.full((h, w, 3), bg, dtype=np.uint8)
    img = Image.fromarray(arr)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("cour.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    y = 70
    for line in lines:
        draw.text((60, y), str(line)[:110], fill=(15, 15, 15), font=font)
        y += 34
        if y > h - 60:
            break

    angle = float(rng.uniform(0.3, 0.8)) * (1 if rng.random() < 0.5 else -1)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(bg, bg, bg))

    arr = np.array(img).astype(np.int16)
    noise = rng.normal(0, 7, arr.shape)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    img = img.filter(ImageFilter.GaussianBlur(radius=float(rng.uniform(0.5, 1.1))))
    return img


def _draw_scanned_pdf(pages: list[list[str]], out_path: Path, rng: np.random.Generator) -> None:
    c = rl_canvas.Canvas(str(out_path), pagesize=letter)
    for page_lines in pages:
        img = _render_page_image(page_lines, rng)
        # Pre-compress to JPEG bytes: reportlab embeds the compressed stream directly instead
        # of re-flate-compressing the full raw bitmap on every drawImage (~30x faster here).
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=PAGE_W, height=PAGE_H)
        c.showPage()
    c.save()


def write_document(pages: list[list[str]], out_dir: Path, base_name: str, scanned: bool,
                    rng: np.random.Generator) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / f"{base_name}.txt"
    pdf_path = out_dir / f"{base_name}.pdf"
    txt_path.write_text("\n\f\n".join("\n".join(p) for p in pages), encoding="utf-8")
    if scanned:
        _draw_scanned_pdf(pages, pdf_path, rng)
    else:
        _draw_text_pdf(pages, pdf_path)
    return txt_path, pdf_path


# --------------------------------------------------------------------------- #
# Orchestration for one well
# --------------------------------------------------------------------------- #
def generate_well_reports(sw: SynthWell, events: list[DrillingEvent], out_root: Path,
                           scanned: bool, rng: np.random.Generator
                           ) -> tuple[list[SourceDocument], list[DaySchedule]]:
    well = sw.well
    out_dir = out_root / well.well_id

    schedule = build_schedule(sw, rng)
    event_day_map = assign_event_days(events, schedule)
    event_days = set(event_day_map.values())
    render_days = days_to_render(schedule, event_days)

    events_by_day: dict[int, list[DrillingEvent]] = {}
    for e in events:
        events_by_day.setdefault(event_day_map[e.event_id], []).append(e)

    documents: list[SourceDocument] = []

    for day in schedule:
        if day.day not in render_days:
            continue
        base_name = f"{well.well_id}_DDR_{day.date.isoformat()}"
        lines = ddr_lines(sw, day, events_by_day.get(day.day, []), rng)
        write_document([lines], out_dir, base_name, scanned, rng)
        doc_id = base_name
        documents.append(SourceDocument(
            document_id=doc_id, well_id=well.well_id, doc_type="DDR",
            path=str((out_dir / f"{base_name}.pdf").as_posix()),
            report_date=day.date, n_pages=1, is_scanned=scanned,
        ))
        for e in events_by_day.get(day.day, []):
            e.source_document_id = doc_id
            e.source_page_ref = f"{base_name}.pdf#p1"

    wcr_pages = wcr_lines(sw, events)
    wcr_base = f"{well.well_id}_WCR"
    write_document(wcr_pages, out_dir, wcr_base, scanned, rng)
    documents.append(SourceDocument(
        document_id=wcr_base, well_id=well.well_id, doc_type="WCR",
        path=str((out_dir / f"{wcr_base}.pdf").as_posix()),
        report_date=schedule[-1].date, n_pages=len(wcr_pages), is_scanned=scanned,
    ))

    return documents, schedule
