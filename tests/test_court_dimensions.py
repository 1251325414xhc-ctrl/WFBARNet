from __future__ import annotations

import unittest

from src.court.dimensions import BWF_COURT


class BwfCourtDimensionsTest(unittest.TestCase):
    def test_bwf_standard_dimensions(self) -> None:
        self.assertEqual(BWF_COURT.length_cm, 1340.0)
        self.assertEqual(BWF_COURT.doubles_width_cm, 610.0)
        self.assertEqual(BWF_COURT.singles_width_cm, 518.0)
        self.assertEqual(BWF_COURT.line_width_cm, 4.0)

    def test_derived_dimensions(self) -> None:
        self.assertEqual(BWF_COURT.singles_side_margin_cm, 46.0)
        self.assertEqual(BWF_COURT.net_y_cm, 670.0)
        self.assertEqual(BWF_COURT.short_service_from_net_cm, 198.0)
        self.assertEqual(BWF_COURT.doubles_long_service_from_back_cm, 76.0)
        self.assertEqual(BWF_COURT.length_mm, 13400.0)
        self.assertEqual(BWF_COURT.doubles_width_mm, 6100.0)


if __name__ == "__main__":
    unittest.main()
