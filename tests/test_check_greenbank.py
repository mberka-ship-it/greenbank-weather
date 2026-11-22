"""Offline, standard-library validation of the draft read-only health checker."""
import copy
from datetime import datetime
import json
import unittest
from zoneinfo import ZoneInfo
from scripts.check_greenbank import Unverifiable, evaluate

NOW = datetime(2026, 11, 22, 8, 0, tzinfo=ZoneInfo("Australia/Brisbane"))


def sample():
    return {
        "generatedAt": "2026-11-22T07:00:00+10:00",
        "queries": {
            "status": {"rows": [{
                "latest_id": "20261121T210000Z", "station": "IBRISB6274",
                "captured_at": "2026-11-22T07:00:00+10:00",
                "end": "2026-12-06T16:00:00+10:00", "last_observation": "2026-11-21",
            }]},
            "observations": {"rows": [
                {"date": "2026-11-20", "rain_mm": 0.0},
                {"date": "2026-11-21", "rain_mm": 0.254},
            ]},
            "models": {"rows": [
                {"model": "ECMWF IFS", "members": 51},
                {"model": "ECMWF AIFS", "members": 51},
                {"model": "GFS seamless", "members": 31},
            ]},
            "archive": {"rows": [{"forecast_id": "20261121T210000Z"}]},
        },
    }


def html(snapshot):
    return '<html><script type="application/json" id="data-app-reviewed-snapshot">' + json.dumps(snapshot) + '</script></html>'


class GreenbankCheckerTests(unittest.TestCase):
    def test_october_export_metadata_fails_freshness(self):
        """Dates verified in the 4 October 2026 repository export."""
        s = sample()
        s["generatedAt"] = "2026-10-04T16:49:12.460616+10:00"
        state = s["queries"]["status"]["rows"][0]
        state["latest_id"] = "20261004T061200Z"
        state["captured_at"] = "2026-10-04T16:12:00+10:00"
        state["end"] = "2026-10-18T16:00:00+10:00"
        state["last_observation"] = "2026-09-30"
        s["queries"]["observations"]["rows"] = [{"date": "2026-09-30", "rain_mm": 0.0}]
        s["queries"]["archive"]["rows"] = [{"forecast_id": "20261004T061200Z"}]
        report = evaluate(html(s), NOW)
        failures = {r["name"] for r in report["checks"] if not r["ok"]}
        self.assertEqual(failures, {"capture_freshness", "forecast_horizon", "observation_freshness"})

    def test_external_snapshot_storage_is_unverifiable(self):
        marked = '<meta name="data-app-snapshot-storage" content="external-v1">' + html(sample())
        with self.assertRaises(Unverifiable):
            evaluate(marked, NOW)

    def test_fresh_valid_snapshot(self):
        self.assertEqual(evaluate(html(sample()), NOW)["result"], "PASS")

    def test_old_forecast_is_detected(self):
        s = sample()
        s["queries"]["status"]["rows"][0]["captured_at"] = "2026-10-04T16:12:00+10:00"
        r = evaluate(html(s), NOW)
        self.assertEqual(r["result"], "FAIL")
        self.assertFalse(next(c["ok"] for c in r["checks"] if c["name"] == "capture_freshness"))

    def test_old_observation_is_detected(self):
        s = sample()
        s["queries"]["status"]["rows"][0]["last_observation"] = "2026-09-30"
        self.assertEqual(evaluate(html(s), NOW)["result"], "FAIL")

    def test_missing_model_fails(self):
        s = sample()
        s["queries"]["models"]["rows"].pop()
        self.assertEqual(evaluate(html(s), NOW)["result"], "FAIL")

    def test_negative_rain_fails(self):
        s = sample()
        s["queries"]["observations"]["rows"][0]["rain_mm"] = -3
        self.assertEqual(evaluate(html(s), NOW)["result"], "FAIL")

    def test_duplicate_dates_fail(self):
        s = sample()
        s["queries"]["observations"]["rows"].append(copy.deepcopy(s["queries"]["observations"]["rows"][1]))
        self.assertEqual(evaluate(html(s), NOW)["result"], "FAIL")

    def test_missing_snapshot_is_unverifiable(self):
        with self.assertRaises(Unverifiable):
            evaluate('<html>Hosted site uses a different format</html>', NOW)

    def test_malformed_json_is_unverifiable(self):
        with self.assertRaises(Unverifiable):
            evaluate('<script id="data-app-reviewed-snapshot">{!</script>', NOW)

    def test_expected_post_publish_id(self):
        self.assertEqual(evaluate(html(sample()), NOW, "unpublished-id")["result"], "FAIL")
        self.assertEqual(evaluate(html(sample()), NOW, "20261121T210000Z")["result"], "PASS")

    def test_archive_must_contain_latest(self):
        s = sample()
        s["queries"]["archive"]["rows"][0]["forecast_id"] = "other-id"
        self.assertEqual(evaluate(html(s), NOW)["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()
