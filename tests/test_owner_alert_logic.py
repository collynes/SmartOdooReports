import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import app


class OwnerAlertLogicTests(unittest.TestCase):
    def test_sales_pace_scales_with_opening_hours(self):
        now = datetime(2026, 7, 20, 14, 0, tzinfo=ZoneInfo('Africa/Nairobi'))

        elapsed_share, expected = app._daily_sales_pace(now, 20_000)

        self.assertEqual(elapsed_share, 0.5)
        self.assertEqual(expected, 10_000)

    def test_sales_pace_is_zero_before_opening(self):
        now = datetime(2026, 7, 20, 7, 0, tzinfo=ZoneInfo('Africa/Nairobi'))

        elapsed_share, expected = app._daily_sales_pace(now, 20_000)

        self.assertEqual(elapsed_share, 0)
        self.assertEqual(expected, 0)


if __name__ == '__main__':
    unittest.main()
