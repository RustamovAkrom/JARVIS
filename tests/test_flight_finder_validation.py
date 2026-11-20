import unittest
from unittest.mock import patch

from plugins import flight_finder


class FlightFinderValidationTests(unittest.TestCase):
    def test_invalid_departure_date_returns_user_error(self):
        with patch.object(flight_finder, "_parse_date", side_effect=ValueError("invalid calendar date")):
            result = flight_finder.flight_finder({
                "origin": "TAS", "destination": "IST", "date": "2026-02-31"
            })
        self.assertIn("Invalid flight date", result)
        self.assertNotIn("Traceback", result)

    def test_invalid_return_date_returns_user_error(self):
        def parse(raw):
            if raw == "bad":
                raise ValueError("could not parse departure date: bad")
            return "2026-12-10"

        with patch.object(flight_finder, "_parse_date", side_effect=parse):
            result = flight_finder.flight_finder({
                "origin": "TAS", "destination": "IST",
                "date": "2026-12-10", "return_date": "bad",
            })
        self.assertIn("Invalid flight date", result)

    def test_past_departure_date_is_rejected(self):
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        with patch.object(flight_finder, "_parse_date", return_value=yesterday):
            result = flight_finder.flight_finder({
                "origin": "TAS", "destination": "IST", "date": "yesterday"
            })
        self.assertEqual(result, "Departure date must be today or in the future.")

    def test_return_date_before_departure_is_rejected(self):
        with patch.object(flight_finder, "_parse_date", side_effect=["2026-12-20", "2026-12-19"]):
            result = flight_finder.flight_finder({
                "origin": "TAS", "destination": "IST",
                "date": "departure", "return_date": "return",
            })
        self.assertEqual(result, "Return date must be on or after the departure date.")

    def test_passenger_count_is_bounded(self):
        for value in (0, 10, -1):
            with self.subTest(value=value):
                result = flight_finder.flight_finder({
                    "origin": "TAS", "destination": "IST",
                    "date": "2026-12-20", "passengers": value,
                })
                self.assertIn("between 1 and 9", result)


if __name__ == "__main__":
    unittest.main()
