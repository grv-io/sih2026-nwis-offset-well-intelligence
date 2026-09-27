"""Deterministic alert rule engine (Phase 6, A7: rules are deterministic; ML score is
displayed/escalates, never the sole gate).

Three rule families, each returning list[Alert]:
  - process_precedent_zone : lookahead advisory, BEFORE the bit arrives (owned gap).
  - process_param_anomaly  : N-of-M + dwell over nwis.live.anomaly flags.
  - process_model_risk     : escalates existing open alerts if Phase 5 is available.

Alert-fatigue control (ISA-18.2 style): per (well, hazard, rule) cooldown, dedupe of
still-open alerts, ack API, severity tiers, hard cap of open alerts with
lowest-severity eviction.

Other modules (nwis.geo.correlate, nwis.predict.risk_ahead) are imported lazily
inside methods and any failure (ImportError or bad data) is recorded in
`signals_unavailable` and the rule is skipped -- this engine must work standalone.
"""
from __future__ import annotations

import itertools
from collections import deque
from typing import Optional

from nwis.config import settings
from nwis.schema import Alert, EventType, Severity

from nwis.live import anomaly as anom
from nwis.live import recommend as reco

MAX_OPEN_ALERTS = 20
PRECEDENT_MIN_EVENTS = 3
PRECEDENT_RADIUS_KM = 5.0
MODEL_RISK_THRESHOLD = 0.7

# process_precedent_zone (and, once it's real, process_model_risk) hit the DB / do
# dip-fit geometry per call (~1 s each, measured against real synthetic data --
# nwis.geo.correlate.active_well_lookahead has no caching). At 1 m sample spacing
# that's thousands of near-identical queries over a well's run for no new
# information, so callers (replay.py, simulate.py) only re-check every this many
# metres of new depth. 50 m matches Phase 5's own risk-interval granularity
# (config.interval_m) -- it's the natural cadence, not an arbitrary throttle.
PRECEDENT_CHECK_INTERVAL_M = settings.interval_m

SEVERITY_RANK: dict[Severity, int] = {
    Severity.low: 0, Severity.medium: 1, Severity.high: 2, Severity.critical: 3,
}
SEVERITY_ORDER = [Severity.low, Severity.medium, Severity.high, Severity.critical]

_id_counter = itertools.count(1)


def _new_id() -> str:
    return f"ALERT-{next(_id_counter):06d}"


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _bump_severity(sev: Severity) -> Severity:
    i = SEVERITY_ORDER.index(sev)
    return SEVERITY_ORDER[min(i + 1, len(SEVERITY_ORDER) - 1)]


def _severity_from_strength(strength: float) -> Severity:
    if strength >= 0.85:
        return Severity.critical
    if strength >= 0.6:
        return Severity.high
    if strength >= 0.35:
        return Severity.medium
    return Severity.low


class AlertEngine:
    def __init__(self, config=None, max_open_alerts: int = MAX_OPEN_ALERTS):
        self.config = config or settings
        self.max_open_alerts = max_open_alerts
        self.open: dict[str, Alert] = {}
        self.history: list[Alert] = []
        self.signals_unavailable: set[str] = set()

        # rule-name -> deque[bool] of the last M flags, keyed by (well_id, rule_name)
        self._flag_hist: dict[tuple, deque] = {}
        # (well_id, rule_name) -> t_s at which the N-of-M gate first armed
        self._armed_since: dict[tuple, float] = {}
        # (well_id, hazard, rule) -> t_s of the last alert fired for cooldown
        self._last_fired: dict[tuple, float] = {}
        # (well_id, hazard, rule) -> currently-open alert_id, for dedupe
        self._open_key_to_id: dict[tuple, str] = {}

    # ------------------------------------------------------------------ #
    # bookkeeping shared by all three rule families
    # ------------------------------------------------------------------ #
    def _already_open(self, cooldown_key: tuple) -> bool:
        aid = self._open_key_to_id.get(cooldown_key)
        return aid is not None and aid in self.open

    def _in_cooldown(self, cooldown_key: tuple, t_s: float) -> bool:
        last_t = self._last_fired.get(cooldown_key)
        return last_t is not None and (t_s - last_t) < self.config.alert_cooldown_s

    def _register(self, alert: Alert, cooldown_key: tuple) -> None:
        self.open[alert.alert_id] = alert
        self.history.append(alert)
        self._last_fired[cooldown_key] = alert.t_s
        self._open_key_to_id[cooldown_key] = alert.alert_id
        self._enforce_cap()

    def _enforce_cap(self) -> None:
        while len(self.open) > self.max_open_alerts:
            victim_id = min(
                self.open,
                key=lambda k: (SEVERITY_RANK[self.open[k].severity], self.open[k].t_s),
            )
            self.open.pop(victim_id)
            for key, aid in list(self._open_key_to_id.items()):
                if aid == victim_id:
                    del self._open_key_to_id[key]

    # ------------------------------------------------------------------ #
    # (a) precedent_zone -- lookahead, before the bit arrives
    # ------------------------------------------------------------------ #
    def process_precedent_zone(
        self, well_id: str, t_s: float, current_md: float, formation: Optional[str] = None,
    ) -> list[Alert]:
        try:
            from nwis.geo.correlate import active_well_lookahead
        except ImportError:
            self.signals_unavailable.add("precedent_zone")
            return []

        lookahead_m = self.config.lookahead_intervals * self.config.interval_m
        try:
            events = active_well_lookahead(well_id, current_md, lookahead_m, PRECEDENT_RADIUS_KM)
        except Exception:
            self.signals_unavailable.add("precedent_zone")
            return []
        if not events:
            return []

        by_hazard: dict[str, list[tuple]] = {}
        for ev in events:
            # nwis.geo.correlate.active_well_lookahead returns "expected_md_m" /
            # "source_well_id"; tolerate "depth_md_m" / "well_id" too in case that
            # contract shifts, so this engine never breaks on a field rename.
            depth = _get(ev, "expected_md_m", _get(ev, "depth_md_m"))
            if depth is None:
                continue
            dist_ahead = depth - current_md
            # defense in depth: only count events strictly ahead of the bit, inside the window.
            if dist_ahead <= 0 or dist_ahead > lookahead_m:
                continue
            hazard = _get(ev, "event_type") or _get(ev, "hazard")
            if hazard is None:
                continue
            hazard_val = hazard.value if hasattr(hazard, "value") else str(hazard)
            by_hazard.setdefault(hazard_val, []).append((ev, dist_ahead))

        new_alerts: list[Alert] = []
        for hazard_val, items in by_hazard.items():
            if len(items) < PRECEDENT_MIN_EVENTS:
                continue
            cooldown_key = (well_id, hazard_val, "precedent_zone")
            if self._already_open(cooldown_key) or self._in_cooldown(cooldown_key, t_s):
                continue

            wells = sorted({
                str(_get(ev, "source_well_id", _get(ev, "well_id")))
                for ev, _ in items if _get(ev, "source_well_id", _get(ev, "well_id"))
            })
            mws = [m for ev, _ in items if (m := _get(ev, "mud_weight_ppg_at_event")) is not None]
            dists = [d for _, d in items]
            event_ids = [str(_get(ev, "event_id")) for ev, _ in items if _get(ev, "event_id")]

            message = self._precedent_message(formation, hazard_val, wells, dists, mws)
            severity = Severity.high if len(items) >= 5 else Severity.medium

            alert = Alert(
                alert_id=_new_id(), well_id=well_id, t_s=t_s, depth_md_m=current_md, formation=formation,
                rule="precedent_zone", hazard=EventType(hazard_val), severity=severity, message=message,
                precedent_event_ids=event_ids,
            )
            self._try_recommend(alert, precedent_events=[ev for ev, _ in items])
            self._register(alert, cooldown_key)
            new_alerts.append(alert)
        return new_alerts

    @staticmethod
    def _precedent_message(formation, hazard_val, wells, dists, mws) -> str:
        fm = formation or "current interval"
        lo, hi = min(dists), max(dists)
        wells_str = ", ".join(wells[:5])
        hazard_plural = {
            "kick": "kicks", "mud_loss": "mud losses", "stuck_pipe": "stuck-pipe events",
            "overpressure": "overpressure events",
        }.get(hazard_val, f"{hazard_val} events")
        msg = (
            f"Approaching {fm} +{lo:.0f}-{hi:.0f} m ahead: {len(wells)} offset well(s) "
            f"({wells_str}) reported {len(dists)} {hazard_plural} in this window"
        )
        if mws:
            msg += f"; MW at events {min(mws):.1f}-{max(mws):.1f} ppg"
        return msg

    # ------------------------------------------------------------------ #
    # (b) param_anomaly -- N-of-M consecutive flags + dwell
    # ------------------------------------------------------------------ #
    def process_param_anomaly(
        self, well_id: str, t_s: float, depth_md_m: float,
        formation: Optional[str], window_df,
    ) -> list[Alert]:
        n_req, m_win = self.config.alert_n_of_m
        flags = anom.evaluate_flags(window_df)
        new_alerts: list[Alert] = []

        for rule_name, (flag, strength, evidence) in flags.items():
            key = (well_id, rule_name)
            dq = self._flag_hist.setdefault(key, deque(maxlen=m_win))
            dq.append(flag)
            armed = sum(dq) >= n_req

            if not armed:
                self._armed_since.pop(key, None)
                continue
            self._armed_since.setdefault(key, t_s)

            dwell_ok = (t_s - self._armed_since[key]) >= self.config.alert_dwell_s
            if not dwell_ok:
                continue

            hazard = anom.HAZARD_MAP[rule_name]
            cooldown_key = (well_id, hazard.value, "param_anomaly")
            if self._already_open(cooldown_key) or self._in_cooldown(cooldown_key, t_s):
                continue

            severity = _severity_from_strength(strength)
            alert = Alert(
                alert_id=_new_id(), well_id=well_id, t_s=t_s, depth_md_m=depth_md_m, formation=formation,
                rule="param_anomaly", hazard=hazard, severity=severity,
                message=f"{rule_name} anomaly: {evidence}",
            )
            self._try_recommend(alert, precedent_events=[])
            self._register(alert, cooldown_key)
            new_alerts.append(alert)
        return new_alerts

    # ------------------------------------------------------------------ #
    # (c) model_risk -- escalate only, never gate on its own (A7)
    # ------------------------------------------------------------------ #
    def process_model_risk(
        self, well_id: str, t_s: float, depth_md_m: float, threshold: float = MODEL_RISK_THRESHOLD,
    ) -> list[Alert]:
        try:
            from nwis.predict.risk_ahead import score as risk_score
        except ImportError:
            self.signals_unavailable.add("model_risk")
            return []
        try:
            intervals = risk_score(well_id, depth_md_m)
        except Exception:
            self.signals_unavailable.add("model_risk")
            return []

        escalated: list[Alert] = []
        for interval in intervals or []:
            s = _get(interval, "score")
            hazard = _get(interval, "hazard")
            if s is None or hazard is None or s < threshold:
                continue
            hazard_val = hazard.value if hasattr(hazard, "value") else str(hazard)
            for rule in ("param_anomaly", "precedent_zone"):
                key = (well_id, hazard_val, rule)
                aid = self._open_key_to_id.get(key)
                if not aid or aid not in self.open:
                    continue
                alert = self.open[aid]
                if alert.severity != Severity.critical:
                    alert.severity = _bump_severity(alert.severity)
                for reason in (_get(interval, "top_reasons") or []):
                    if reason not in alert.citations:
                        alert.citations.append(str(reason))
                escalated.append(alert)
        return escalated

    # ------------------------------------------------------------------ #
    def _try_recommend(self, alert: Alert, precedent_events: list) -> None:
        """Fire-and-forget: the LLM/RAG recommendation runs on a daemon thread so an alert
        never blocks the replay loop or the API (26 Sep: a busy Ollama froze /live for 80 s).
        The alert is published immediately with a placeholder; the poller sees the real
        recommendation once the thread fills it in."""
        import threading
        if alert.recommendation is None:
            alert.recommendation = "Recommendation pending..."
        if not alert.citations:
            alert.citations = [f"{alert.well_id} live sensors @ t={alert.t_s:.0f}s"]
        t = threading.Thread(target=self._recommend_blocking, args=(alert, precedent_events), daemon=True)
        t.start()

    def _recommend_blocking(self, alert: Alert, precedent_events: list) -> None:
        try:
            # Work on a copy so the API never serialises a half-updated Alert; then merge
            # back in one step, keeping any citation the escalation path appended meanwhile
            # (review 26 Sep: thread/API interleaving on the live object).
            work = alert.model_copy(deep=True)
            reco.recommend_for_alert(work, precedent_events=precedent_events)
            merged = list(work.citations) + [c for c in alert.citations
                                             if c not in work.citations and "live sensors" not in c]
            alert.recommendation, alert.citations = work.recommendation, merged
        except Exception:
            # recommendation is best-effort; never let it block an alert firing
            if alert.recommendation is None:
                alert.recommendation = "Recommendation unavailable; see evidence and citations."
            if not alert.citations:
                alert.citations = [f"{alert.well_id} live sensors @ t={alert.t_s:.0f}s"]

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def open_alerts(self, include_acked: bool = True) -> list[Alert]:
        alerts = list(self.open.values())
        if not include_acked:
            alerts = [a for a in alerts if not a.acknowledged]
        return sorted(alerts, key=lambda a: a.t_s, reverse=True)

    def ack(self, alert_id: str) -> bool:
        a = self.open.get(alert_id)
        if a is None:
            return False
        a.acknowledged = True
        return True

    def shelve(self, alert_id: str) -> bool:
        """Remove from the active/open set without marking as acked (ISA-18.2 'shelve')."""
        a = self.open.pop(alert_id, None)
        if a is None:
            return False
        for key, aid in list(self._open_key_to_id.items()):
            if aid == alert_id:
                del self._open_key_to_id[key]
        return True
