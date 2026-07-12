"""World Badminton Federation standard court dimensions.

The project uses court-plane centimetres for model output, statistics and
Hawkeye-style line calls. Millimetre aliases are provided for UI drawing code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BadmintonCourtDimensions:
    length_cm: float = 1340.0
    doubles_width_cm: float = 610.0
    singles_width_cm: float = 518.0
    line_width_cm: float = 4.0
    short_service_from_net_cm: float = 198.0
    doubles_long_service_from_back_cm: float = 76.0

    @property
    def singles_side_margin_cm(self) -> float:
        return (self.doubles_width_cm - self.singles_width_cm) / 2.0

    @property
    def net_y_cm(self) -> float:
        return self.length_cm / 2.0

    @property
    def length_mm(self) -> float:
        return self.length_cm * 10.0

    @property
    def doubles_width_mm(self) -> float:
        return self.doubles_width_cm * 10.0

    @property
    def singles_width_mm(self) -> float:
        return self.singles_width_cm * 10.0

    @property
    def line_width_mm(self) -> float:
        return self.line_width_cm * 10.0

    @property
    def singles_side_margin_mm(self) -> float:
        return self.singles_side_margin_cm * 10.0

    @property
    def short_service_from_net_mm(self) -> float:
        return self.short_service_from_net_cm * 10.0

    @property
    def doubles_long_service_from_back_mm(self) -> float:
        return self.doubles_long_service_from_back_cm * 10.0


BWF_COURT = BadmintonCourtDimensions()

COURT_LENGTH_CM = BWF_COURT.length_cm
COURT_WIDTH_CM = BWF_COURT.doubles_width_cm
DOUBLES_WIDTH_CM = BWF_COURT.doubles_width_cm
SINGLES_WIDTH_CM = BWF_COURT.singles_width_cm
LINE_WIDTH_CM = BWF_COURT.line_width_cm
SINGLES_MARGIN_CM = BWF_COURT.singles_side_margin_cm
NET_Y_CM = BWF_COURT.net_y_cm
SHORT_SERVICE_FROM_NET_CM = BWF_COURT.short_service_from_net_cm
DOUBLES_LONG_SERVICE_FROM_BACK_CM = BWF_COURT.doubles_long_service_from_back_cm

COURT_LENGTH_MM = BWF_COURT.length_mm
COURT_WIDTH_MM = BWF_COURT.doubles_width_mm
DOUBLES_WIDTH_MM = BWF_COURT.doubles_width_mm
SINGLES_WIDTH_MM = BWF_COURT.singles_width_mm
LINE_WIDTH_MM = BWF_COURT.line_width_mm
SINGLES_MARGIN_MM = BWF_COURT.singles_side_margin_mm
SHORT_SERVICE_FROM_NET_MM = BWF_COURT.short_service_from_net_mm
DOUBLES_LONG_SERVICE_FROM_BACK_MM = BWF_COURT.doubles_long_service_from_back_mm
