"""OS end-to-end tests for competitive_collect (Package C patch).

Offline only: payload capture via the established mock pattern from
test_competitive_collect.py. No network/API calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from competitive_collect import adapt_plan, collect, fingerprint_task, make_task_payload
from competitive_queue import POST_ENDPOINT
from test_competitive_collect import make_mock_serp_response, make_sample_plan


def _plan_with(device, os_marker, method="standard"):
    plan = {
        "target_business": {"name": "Apex Climate Control", "cid": "999000111",
                            "domain": "apexclimate.example.com", "lat": 38.6442, "lng": -121.2678},
        "query_selection": [{"query": "hvac contractor", "query_id": "q1"}],
        "measurement_settings": {"depth": 30, "zoom": 14, "device": device,
                                 "language_code": "en", "se_domain": "google.com",
                                 "search_this_area": False, "method": method},
        "origins": [{"origin_id": "csp_001", "lat": 38.6442, "lng": -121.2678, "phase": 1}],
    }
    if os_marker is not None:
        plan["measurement_settings"]["os"] = os_marker
    return plan


class OsEndToEndTests(unittest.TestCase):
    def test_mobile_android_survives_adaptation_validation_and_payload(self):
        adapted = adapt_plan(_plan_with("mobile", "android"))
        self.assertEqual(adapted["settings"]["os"], "android")
        task = make_task_payload("hvac contractor", adapted["origins"][0], adapted["settings"])
        self.assertEqual(task["device"], "mobile")
        self.assertEqual(task["os"], "android")

    def test_mobile_ios_survives_adaptation_validation_and_payload(self):
        adapted = adapt_plan(_plan_with("mobile", "ios"))
        self.assertEqual(adapted["settings"]["os"], "ios")
        task = make_task_payload("hvac contractor", adapted["origins"][0], adapted["settings"])
        self.assertEqual(task["device"], "mobile")
        self.assertEqual(task["os"], "ios")

    def test_desktop_windows_explicit_survives(self):
        adapted = adapt_plan(_plan_with("desktop", "windows"))
        self.assertEqual(adapted["settings"]["os"], "windows")
        task = make_task_payload("hvac contractor", adapted["origins"][0], adapted["settings"])
        self.assertEqual(task["os"], "windows")

    def test_desktop_no_os_payload_and_fingerprint_byte_identical_shape(self):
        adapted = adapt_plan(_plan_with("desktop", None))
        self.assertNotIn("os", adapted["settings"])
        task = make_task_payload("hvac contractor", adapted["origins"][0], adapted["settings"])
        self.assertEqual(task, {
            "keyword": "hvac contractor",
            "location_coordinate": "38.6442000,-121.2678000,14z",
            "depth": 30, "device": "desktop", "language_code": "en",
            "se_domain": "google.com", "search_this_area": False,
            "search_places": False, "tag": "csp_001", "priority": 1,
        })
        expected = hashlib.sha256(
            json.dumps([POST_ENDPOINT, task], sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(fingerprint_task(task, POST_ENDPOINT), expected)

    def test_os_is_inside_task_fingerprint(self):
        origin = {"tag": "csp_001", "lat": 38.6442, "lng": -121.2678}
        base = {"depth": 30, "zoom": 14, "device": "mobile", "language_code": "en",
                "se_domain": "google.com", "search_this_area": False, "method": "standard"}
        fps = {os_name: fingerprint_task(
            make_task_payload("hvac contractor", origin, {**base, "os": os_name}), POST_ENDPOINT)
            for os_name in ("android", "ios")}
        self.assertNotEqual(fps["android"], fps["ios"])
        task = make_task_payload("hvac contractor", origin, {**base, "os": "android"})
        manual = hashlib.sha256(
            json.dumps([POST_ENDPOINT, task], sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(fps["android"], manual)
        self.assertIn('"os": "android"', json.dumps(task, sort_keys=True))

    def test_unsupported_device_os_combinations_refuse(self):
        for device, os_name in [("desktop", "android"), ("desktop", "ios"),
                                ("desktop", "macos"), ("mobile", "windows"),
                                ("mobile", "macos"), ("mobile", None),
                                ("mobile", ""), ("desktop", "linux")]:
            with self.subTest(device=device, os=os_name):
                with self.assertRaisesRegex(ValueError, "Unsupported device/os combination"):
                    adapt_plan(_plan_with(device, os_name))

    def test_collection_timestamps_retained_with_os(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path, plan = make_sample_plan(root)
            plan["measurement_settings"].update(
                {"device": "mobile", "os": "android", "method": "live", "depth": 30})
            plan_path.write_text(json.dumps(plan, indent=2))
            lineage = root / "source-study.json"
            lineage.write_text("{}")
            plan["source_study_plan"] = str(lineage)
            plan_path.write_text(json.dumps(plan, indent=2))
            from study_contract import digest
            validation = patch("competitive_collect.validate_study_plan", return_value=(
                {"business": plan["target_business"],
                 "queries": [dict(q, selected=True) for q in plan["query_selection"]]},
                {"plan_sha256": digest(lineage)}))
            fake_mod = types.ModuleType("legends_dataforseo")
            fake_mod.api_request = MagicMock(side_effect=lambda endpoint, payload, **kw:
                make_mock_serp_response(payload[0]["keyword"], payload[0]["tag"], 38.6442, -121.2678))
            with validation, patch.dict(sys.modules, legends_dataforseo=fake_mod):
                res = collect(allow_live=True, plan_path=plan_path,
                              output_dir=root / "run", execute=True, confirm_cost_usd=0.50)
            self.assertEqual(res["status"], "collected")
            captured = [c.args[1][0] for c in fake_mod.api_request.call_args_list]
            self.assertTrue(all(t["os"] == "android" and t["device"] == "mobile" for t in captured))
            parsed = json.loads((root / "run" / "lane-1" / "parsed-grid.json").read_text())
            self.assertEqual(parsed["results"][0]["sampled_at"], "2026-10-18T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
