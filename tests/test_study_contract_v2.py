"""Wholly synthetic generic fixtures for geogrid-study/v2.

No host-specific policy values, identities, coordinates from any real
market, or integration receipts appear in this file.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from competitive_collect import fingerprint_task, make_task_payload
from competitive_collect_v2 import (
    AuthorityRecord, ExpectedQuery, ExpectedTask, LaneBinding, TrustedBinding,
    collect_v2, main)
from study_contract_v2 import validate_v2

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)
OBSERVED = "2026-01-14T00:00:00+00:00"
BUSINESS = {"name": "Example Studio", "cid": "12345", "domain": "example.invalid",
            "location_label": "Exampletown", "lat": 10.0, "lng": 20.0}
SETTINGS = {"method": "standard", "depth": 30, "zoom": 14, "device": "desktop",
            "os": "windows", "language_code": "en", "se_domain": "google.com",
            "search_this_area": False, "search_places": False}
POINTS = tuple({"origin_id": f"p{i}", "lat": 10.0 + i / 100, "lng": 20.0}
               for i in range(3))


def _b(obj):
    return (json.dumps(obj, indent=2) + "\n").encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def make_fixture():
    sources, source_bytes = [], {}

    def add(sid, kind, doc, url="https://provider.invalid/x"):
        raw = _b(doc)
        source_bytes[f"{sid}.json"] = raw
        sources.append({"id": sid, "kind": kind, "file": f"{sid}.json",
                        "sha256": _sha(raw), "observed_at": OBSERVED, "url": url,
                        "authority_record_id": f"auth-{sid}"})
        return sid

    add("website", "website",
        {"url": "https://example.invalid/", "text": "Example Studio offers alpha training."},
        "https://example.invalid/")
    add("gbp", "gbp", {"status_code": 20000, "tasks": [{
        "status_code": 20000, "result": [{
            "cid": "12345", "title": "Example Studio", "latitude": 10.0,
            "longitude": 20.0, "url": "https://example.invalid/"}]}]})
    add("demand", "demand", {"status_code": 20000, "tasks": [{
        "status_code": 20000,
        "data": {"keywords": ["alpha training", "beta research"],
                 "location_code": 2840, "language_code": "en"},
        "result": [
            {"keyword": "alpha training", "location_code": 2840,
             "keyword_info": {"search_volume": 100}},
            {"keyword": "beta research", "location_code": None,
             "keyword_info": {"search_volume": None}}]}]})
    pilot_ids = {}
    for term, slug in (("Alpha training", "alpha"), ("Beta research", "beta")):
        ids = []
        for idx, point in enumerate(POINTS):
            sid = f"probe-{slug}-{idx}"
            ids.append(sid)
            add(sid, "maps_probe", {"status_code": 20000, "tasks": [{
                "status_code": 20000,
                "data": {"keyword": term, "language_code": "en",
                         "location_coordinate": f"{point['lat']:.4f},{point['lng']:.4f},14z",
                         "device": "desktop", "os": "windows",
                         "se_domain": "google.com", "search_this_area": False,
                         "search_places": False, "depth": 30},
                "result": [{"items": [{"title": "Example Studio", "cid": "12345"}]}]}]})
        pilot_ids[term] = ids

    def cite(sid, pointer, quote):
        return {"source_id": sid, "pointer": pointer, "quote": quote}

    theme = {"id": "shared-entry", "label": "Entry training",
             "customer_need": "Find training", "distinct_value": "Example value"}
    study = {
        "schema": "geogrid-study/v2",
        "business": BUSINESS,
        "binding": {
            "market_id": "example-market", "approved_reference": "fixture-ref",
            "manifest_file_sha256": "f" * 64, "manifest_sha256": "e" * 64,
            "producer_stamp_sha256": "d" * 64, "origin_set_sha256": "c" * 64,
            "lanes": [], "probe_origins": list(POINTS)},
        "sources": sources,
        "identity": {"source_id": "gbp", "pointer": "/tasks/0/result/0",
                     "website_reference": cite("website", "/text", "Example Studio"),
                     "reconciliation": "Synthetic identity fields agree."},
        "settings": {**SETTINGS, "demand_location_code": 2840,
                     "geography_reason": "Fixture market", "decision": "Fixture decision",
                     "geometry": {"mode": "frozen_irregular_origins",
                                  "origin_set_sha256": "c" * 64}},
        "queries": [
            {"query": "Alpha training", "provider_normalized_query": "alpha training",
             "selected": True, "role": "service", "reason": "Fixture current term.",
             "audience_intent": "adult", "service_availability": "current",
             "evidence_role": "current_offering",
             "offering_evidence": [cite("website", "/text", "alpha training")],
             "demand_source": "demand", "demand_scope": "Fixture national scope",
             "intent_assessment": "Returned titles support training intent.",
             "limitations": "Synthetic fixture.",
             "pilot_sources": pilot_ids["Alpha training"],
             "intent_evidence": [cite(pilot_ids["Alpha training"][0],
                                      "/tasks/0/result/0/items/0/title", "Example Studio")],
             "theme": theme},
            {"query": "Beta research", "provider_normalized_query": "beta research",
             "selected": True, "role": "category", "reason": "Fixture research term.",
             "audience_intent": "adult", "service_availability": "planned",
             "evidence_role": "research_only",
             "market_evidence": [cite(pilot_ids["Beta research"][0],
                                      "/tasks/0/result/0/items/0/title", "Example Studio")],
             "demand_source": "demand", "demand_scope": "Fixture national scope",
             "intent_assessment": "Returned titles support market research only.",
             "limitations": "Synthetic fixture; not an offering claim.",
             "pilot_sources": pilot_ids["Beta research"],
             "intent_evidence": [cite(pilot_ids["Beta research"][0],
                                      "/tasks/0/result/0/items/0/title", "Example Studio")],
             "theme": theme},
        ],
        "selection": {"coverage_summary": "Synthetic shared-theme fixture.",
                      "evidence": [cite("website", "/text", "Example Studio"),
                                   cite("gbp", "/tasks/0/result/0/title", "Example Studio")]},
    }
    lane = {
        "schema": "example-plan/v1", "market_id": "example-market", "tier": 2,
        "cohort_id": "A", "target_business": BUSINESS,
        "measurement_settings": SETTINGS,
        "query_selection": [
            {"query_id": "q1", "query": "Alpha training",
             "audience_intent": "adult", "service_availability": "current"},
            {"query_id": "q2", "query": "Beta research",
             "audience_intent": "adult", "service_availability": "planned"}],
        "origins": [{"origin_id": p["origin_id"], "lat": p["lat"], "lng": p["lng"],
                     "cohort_id": "A"} for p in POINTS],
    }
    lane_bytes = _b(lane)
    lane_id = "example-market-t2-desktop-windows-A"
    study["binding"]["lanes"] = [{
        "lane_id": lane_id, "sha256": _sha(lane_bytes), "tier": 2,
        "cohort_id": "A", "device": "desktop", "os": "windows"}]
    study_bytes = _b(study)
    records = tuple(AuthorityRecord(
        s["authority_record_id"], s["id"], s["sha256"], s["kind"],
        "expanded-study-evidence", "a" * 64, "fixture-run", OBSERVED)
        for s in sources)
    tasks = []
    for q in lane["query_selection"]:
        for origin in lane["origins"]:
            task = make_task_payload(q["query"], {
                "tag": origin["origin_id"], "lat": origin["lat"], "lng": origin["lng"]},
                SETTINGS)
            tasks.append(ExpectedTask("/serp/google/maps/task_post", task,
                                      fingerprint_task(task, "/serp/google/maps/task_post")))
    binding = TrustedBinding(
        market_id="example-market", approved_reference="fixture-ref",
        manifest_file_sha256="f" * 64, manifest_sha256="e" * 64,
        producer_stamp_sha256="d" * 64, origin_set_sha256="c" * 64,
        lanes=(LaneBinding(lane_id, _sha(lane_bytes), 2, "A", "desktop", "windows"),),
        expected_queries=(
            ExpectedQuery("Alpha training", "adult", "current"),
            ExpectedQuery("Beta research", "adult", "planned")),
        probe_origins=POINTS, settings=SETTINGS,
        demand_location_code=2840, expected_tasks=tuple(tasks))
    completed = {t.fingerprint: {"status_code": 20000, "tasks": [{
        "status_code": 20000,
        "result": [{"items": [{"title": "Example Studio", "cid": "12345"}]}]}]}
        for t in tasks}
    return lane_bytes, study_bytes, source_bytes, binding, records, completed


class StudyContractV2Tests(unittest.TestCase):
    def test_shared_theme_and_research_only_output(self):
        lane_bytes, study_bytes, source_bytes, binding, records, completed = make_fixture()
        _plan, receipt = validate_v2(study_bytes, source_bytes, now=NOW)
        self.assertEqual(receipt["selected_queries"], ["Alpha training", "Beta research"])
        self.assertEqual(receipt["demand_states"]["Beta research"], "missing")
        out = collect_v2(lane_bytes=lane_bytes, study_bytes=study_bytes,
                         source_bytes=source_bytes, binding=binding,
                         authority_records=records, completed_responses=completed, now=NOW)
        self.assertEqual(out["task_count"], 6)
        research = [r for r in out["records"] if r["service_availability"] == "planned"]
        self.assertTrue(research)
        self.assertTrue(all("rank" not in r and "matched_item" not in r for r in research))
        current = [r for r in out["records"] if r["service_availability"] == "current"]
        self.assertTrue(all(r["rank"] == 1 for r in current))

    def test_cli_cannot_establish_trust(self):
        self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
