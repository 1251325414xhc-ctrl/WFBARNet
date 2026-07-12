"""Badminton Hawkeye-style line-call judgement.

The module works in court-plane centimetres. Existing court projection code in
WFBARNet uses the standard badminton coordinate frame:

* x: left to right, 0..610 cm on a doubles court
* y: near to far baseline, 0..1340 cm

Badminton lines are part of the court, so the configurable tolerance is treated
as an "in" margin around the legal boundary. This intentionally favours a
stable call when the projection is very close to the line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Literal, Mapping, Sequence

from src.court.dimensions import BWF_COURT


CourtMode = Literal["singles", "doubles"]
LineCallValue = Literal["in", "out", "unknown"]


@dataclass(frozen=True)
class HawkeyeCourtSpec:
    """BWF badminton court dimensions in centimetres."""

    length_cm: float = BWF_COURT.length_cm
    doubles_width_cm: float = BWF_COURT.doubles_width_cm
    singles_width_cm: float = BWF_COURT.singles_width_cm
    line_width_cm: float = BWF_COURT.line_width_cm

    def bounds_for(self, court_mode: CourtMode) -> tuple[float, float, float, float]:
        """Return legal play bounds as (left, right, top, bottom)."""

        if court_mode == "doubles":
            left = 0.0
            right = self.doubles_width_cm
        elif court_mode == "singles":
            inset = (self.doubles_width_cm - self.singles_width_cm) / 2.0
            left = inset
            right = self.doubles_width_cm - inset
        else:
            raise ValueError(f"unsupported court mode: {court_mode!r}")
        return left, right, 0.0, self.length_cm


BWF_HAWKEYE_COURT = HawkeyeCourtSpec()


@dataclass(frozen=True)
class LineCall:
    """Result of a Hawkeye-style landing judgement."""

    valid: bool
    call: LineCallValue
    court_mode: CourtMode
    landing_xy: tuple[float, float] | None
    normalized_xy: tuple[float, float] | None
    distance_to_boundary_cm: float | None
    out_by_cm: float
    nearest_boundary: str
    confidence: float
    reason: str
    margins_cm: dict[str, float] = field(default_factory=dict)

    @property
    def is_in(self) -> bool:
        return self.call == "in"

    @property
    def is_out(self) -> bool:
        return self.call == "out"

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "call": self.call,
            "court_mode": self.court_mode,
            "landing_xy": list(self.landing_xy) if self.landing_xy is not None else None,
            "normalized_xy": list(self.normalized_xy) if self.normalized_xy is not None else None,
            "distance_to_boundary_cm": self.distance_to_boundary_cm,
            "out_by_cm": self.out_by_cm,
            "nearest_boundary": self.nearest_boundary,
            "confidence": self.confidence,
            "reason": self.reason,
            "margins_cm": dict(self.margins_cm),
        }


def _clamp01(value: float) -> float:
    if not isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _coerce_xy(court_xy: Sequence[float] | None) -> tuple[float, float] | None:
    if court_xy is None or len(court_xy) < 2:
        return None
    try:
        x = float(court_xy[0])
        y = float(court_xy[1])
    except (TypeError, ValueError):
        return None
    if not isfinite(x) or not isfinite(y):
        return None
    return x, y


def _unknown(
    *,
    court_mode: CourtMode,
    reason: str,
    confidence: float = 0.0,
) -> LineCall:
    return LineCall(
        valid=False,
        call="unknown",
        court_mode=court_mode,
        landing_xy=None,
        normalized_xy=None,
        distance_to_boundary_cm=None,
        out_by_cm=0.0,
        nearest_boundary="unknown",
        confidence=_clamp01(confidence),
        reason=reason,
    )


def judge_landing(
    court_xy: Sequence[float] | None,
    *,
    court_mode: CourtMode = "doubles",
    spec: HawkeyeCourtSpec = BWF_HAWKEYE_COURT,
    line_tolerance_cm: float | None = None,
    confidence: float = 1.0,
) -> LineCall:
    """Judge whether a projected shuttle landing point is in or out."""

    landing_xy = _coerce_xy(court_xy)
    if landing_xy is None:
        return _unknown(
            court_mode=court_mode,
            reason="no valid projected landing point",
            confidence=0.0,
        )

    try:
        left, right, top, bottom = spec.bounds_for(court_mode)
    except ValueError:
        return _unknown(
            court_mode=court_mode,
            reason=f"unsupported court mode: {court_mode!r}",
            confidence=0.0,
        )

    tolerance = spec.line_width_cm / 2.0 if line_tolerance_cm is None else max(0.0, float(line_tolerance_cm))
    x, y = landing_xy
    margins = {
        "left_sideline": x - left,
        "right_sideline": right - x,
        "front_boundary": y - top,
        "back_boundary": bottom - y,
    }
    nearest_boundary = min(margins, key=lambda key: abs(margins[key]))
    signed_distance = min(margins.values())
    out_by = max(0.0, -signed_distance - tolerance)
    call: LineCallValue = "in" if signed_distance >= -tolerance else "out"
    normalized_xy = (
        (x - left) / max(right - left, 1e-6),
        (y - top) / max(bottom - top, 1e-6),
    )
    reason = (
        "inside boundary or on line tolerance"
        if call == "in"
        else f"outside {nearest_boundary} by {out_by:.1f} cm"
    )
    return LineCall(
        valid=True,
        call=call,
        court_mode=court_mode,
        landing_xy=(x, y),
        normalized_xy=normalized_xy,
        distance_to_boundary_cm=signed_distance,
        out_by_cm=out_by,
        nearest_boundary=nearest_boundary,
        confidence=_clamp01(confidence),
        reason=reason,
        margins_cm=margins,
    )


def judge_landing_event(
    event: Mapping[str, Any] | None,
    *,
    ball_court_xy: Sequence[float] | None = None,
    court_mode: CourtMode = "doubles",
    spec: HawkeyeCourtSpec = BWF_HAWKEYE_COURT,
    line_tolerance_cm: float | None = None,
    confidence: float | None = None,
) -> LineCall:
    """Judge a trajectory landing event, optionally with a projected court point."""

    if not isinstance(event, Mapping) or event.get("event_type") != "landing":
        return _unknown(court_mode=court_mode, reason="not a landing event", confidence=0.0)
    source_confidence = confidence
    if source_confidence is None:
        try:
            source_confidence = float(event.get("confidence", 0.0))
        except (TypeError, ValueError):
            source_confidence = 0.0
    return judge_landing(
        ball_court_xy,
        court_mode=court_mode,
        spec=spec,
        line_tolerance_cm=line_tolerance_cm,
        confidence=source_confidence,
    )


judge_ball_landing = judge_landing
