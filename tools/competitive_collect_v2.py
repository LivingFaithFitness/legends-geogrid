"""Generic lane-bound v2 collector interfaces for geogrid-study/v2.

This module contains no host policy values and establishes no trust by
itself. A trusted host integration must supply a binding it derived
independently, captured lane/study/source bytes, and recognized authority
records. The CLI in this file cannot establish that trust and therefore
always refuses v2 collection. The v1 collector in competitive_collect.py is
unchanged.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from competitive_collect import fingerprint_task, make_task_payload
from competitive_queue import POST_ENDPOINT
from study_contract_v2 import validate_v2

LIVE_ENDPOINT = "/serp/google/maps/live/advanced"


def _require(ok, message):
    if not ok:
        raise ValueError(message)


@dataclass(frozen=True)
class ExpectedQuery:
    query: str
    audience_intent: str
    service_availability: str


@dataclass(frozen=True)
class LaneBinding:
    lane_id: str
    sha256: str
    tier: int
    cohort_id: str
    device: str
    os: str


@dataclass(frozen=True)
class ExpectedTask:
    endpoint: str
    task: Mapping[str, Any]
    fingerprint: str


@dataclass(frozen=True)
class AuthorityRecord:
    record_id: str
    source_id: str
    sha256: str
    kind: str
    purpose: str
    request_sha256: str
    source_run_id: str | None
    observed_at: str
    endpoint: str = ""


@dataclass(frozen=True)
class TrustedBinding:
    market_id: str
    approved_reference: str
    manifest_file_sha256: str
    manifest_sha256: str
    producer_stamp_sha256: str
    origin_set_sha256: str
    lanes: tuple[LaneBinding, ...]
    expected_queries: tuple[ExpectedQuery, ...]
    probe_origins: tuple[Mapping[str, Any], ...]
    settings: Mapping[str, Any]
    demand_location_code: int
    expected_tasks: tuple[ExpectedTask, ...]
    target_business: Mapping[str, Any] = None


def _parse(raw: bytes, what: str):
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"{what} unparseable: {exc}") from None
    _require(isinstance(value, dict), what + " must be an object")
    return value


def _normalize_name(value) -> str:
    return " ".join(str(value or "").casefold().split())


def _check_target(study, lane_plan, binding: TrustedBinding):
    target = binding.target_business
    _require(isinstance(target, Mapping) and target,
             "trusted target business missing from binding")
    business = study.get("business") or {}
    lane_business = lane_plan.get("target_business") or lane_plan.get("business") or {}
    for label, candidate in (("study", business), ("lane", lane_business)):
        _require(_normalize_name(candidate.get("name")) == _normalize_name(target.get("name")),
                 f"{label} business name differs from trusted target")
        _require(str(candidate.get("cid") or "") == str(target.get("cid") or ""),
                 f"{label} business CID differs from trusted target")
        _require(str(candidate.get("domain") or "").casefold().removeprefix("www.") ==
                 str(target.get("domain") or "").casefold().removeprefix("www."),
                 f"{label} business domain differs from trusted target")
        if target.get("place_id"):
            _require(str(candidate.get("place_id") or "") == str(target["place_id"]),
                     f"{label} business place_id differs from trusted target")
        for field, target_key in (("lat", "lat"), ("lng", "lng")):
            try:
                delta = abs(float(candidate[field]) - float(target[target_key]))
            except Exception:
                _require(False, f"{label} business coordinates unusable")
            _require(delta <= 0.000001,
                     f"{label} business coordinates differ from trusted target")


def _lane_id_for(plan: Mapping[str, Any]) -> str:
    ms = plan.get("measurement_settings") or {}
    return (f"{plan.get('market_id')}-t{plan.get('tier')}-{ms.get('device')}-"
            f"{ms.get('os')}-{plan.get('cohort_id')}").replace(" ", "-")


def _check_authority(study, source_bytes, authority_records):
    records = {r.record_id: r for r in authority_records}
    _require(len(records) == len(authority_records), "duplicate authority record id")
    for source in study.get("sources") or []:
        sid = source.get("id")
        record = records.get(source.get("authority_record_id"))
        _require(record is not None, f"source {sid} has no recognized authority record")
        raw = source_bytes.get(source.get("file"))
        _require(raw is not None, f"source {sid} bytes missing")
        digest = hashlib.sha256(raw).hexdigest()
        _require(record.source_id == sid, f"authority record source mismatch: {sid}")
        _require(record.sha256 == digest == source.get("sha256"),
                 f"authority digest mismatch: {sid}")
        _require(record.kind == source.get("kind"), f"authority kind mismatch: {sid}")
        _require(record.purpose == "expanded-study-evidence",
                 f"authority purpose mismatch: {sid}")
        _require(bool(record.request_sha256), f"authority request identity missing: {sid}")
        _require(record.observed_at == source.get("observed_at"),
                 f"authority observation time mismatch: {sid}")


def _check_study_binding(study, lane_plan, lane_id, lane_sha, binding: TrustedBinding):
    claimed = study.get("binding") or {}
    scalar = {
        "market_id": binding.market_id,
        "approved_reference": binding.approved_reference,
        "manifest_file_sha256": binding.manifest_file_sha256,
        "manifest_sha256": binding.manifest_sha256,
        "producer_stamp_sha256": binding.producer_stamp_sha256,
        "origin_set_sha256": binding.origin_set_sha256,
    }
    for key, expected in scalar.items():
        _require(claimed.get(key) == expected, f"study binding mismatch: {key}")
    claimed_lanes = {
        (l.get("lane_id"), l.get("sha256"), l.get("tier"), l.get("cohort_id"),
         l.get("device"), l.get("os"))
        for l in claimed.get("lanes") or [] if isinstance(l, dict)
    }
    expected_lanes = {
        (l.lane_id, l.sha256, l.tier, l.cohort_id, l.device, l.os) for l in binding.lanes
    }
    _require(claimed_lanes == expected_lanes, "study lane binding mismatch")
    claimed_points = {
        (p.get("origin_id"), p.get("lat"), p.get("lng"))
        for p in claimed.get("probe_origins") or [] if isinstance(p, dict)
    }
    expected_points = {
        (p.get("origin_id"), p.get("lat"), p.get("lng")) for p in binding.probe_origins
    }
    _require(claimed_points == expected_points, "study probe-origin binding mismatch")
    _check_target(study, lane_plan, binding)
    settings = study.get("settings") or {}
    for key, expected in binding.settings.items():
        _require(settings.get(key) == expected, f"study settings mismatch: {key}")
    _require(settings.get("demand_location_code") == binding.demand_location_code,
             "study demand location mismatch")
    _require((settings.get("geometry") or {}).get("origin_set_sha256") ==
             binding.origin_set_sha256, "study geometry origin-set digest mismatch")
    _require(lane_plan.get("market_id") == binding.market_id, "lane market mismatch")
    lane_record = next((l for l in binding.lanes if l.lane_id == lane_id), None)
    _require(lane_record is not None, "lane is not in trusted binding")
    _require(lane_sha == lane_record.sha256, "verified lane hash differs from binding")
    _require(_lane_id_for(lane_plan) == lane_id, "lane identity mismatch")


def _check_expected_queries(study, binding: TrustedBinding):
    actual = {
        q.get("query"): (q.get("audience_intent"), q.get("service_availability"))
        for q in study.get("queries") or [] if isinstance(q, dict) and q.get("selected")
    }
    expected = {
        q.query: (q.audience_intent, q.service_availability)
        for q in binding.expected_queries
    }
    _require(actual == expected, "selected literal-query union or axes differ from binding")


def _generate_tasks(lane_plan):
    settings = lane_plan.get("measurement_settings") or {}
    normalized = {
        "depth": int(settings.get("depth", 30)), "zoom": int(settings.get("zoom", 14)),
        "device": str(settings.get("device", "desktop")),
        "language_code": str(settings.get("language_code", "en")),
        "se_domain": str(settings.get("se_domain", "google.com")),
        "search_this_area": bool(settings.get("search_this_area", False)),
        "search_places": False, "method": str(settings.get("method", "standard")),
    }
    if settings.get("os") is not None:
        normalized["os"] = str(settings["os"]).strip().lower()
    endpoint = POST_ENDPOINT if normalized["method"] == "standard" else LIVE_ENDPOINT
    origins = []
    for idx, orig in enumerate(lane_plan.get("origins") or [], start=1):
        origins.append({
            "tag": str(orig.get("origin_id") or orig.get("tag") or f"csp_{idx:03d}"),
            "lat": float(orig["lat"]), "lng": float(orig["lng"]),
        })
    tasks = []
    for item in lane_plan.get("query_selection") or lane_plan.get("queries") or []:
        query = item["query"] if isinstance(item, dict) else str(item)
        for orig in origins:
            task = make_task_payload(query, orig, normalized)
            tasks.append(ExpectedTask(endpoint, task, fingerprint_task(task, endpoint)))
    return tasks


def _items(envelope):
    tasks = envelope.get("tasks") or [] if isinstance(envelope, dict) else []
    if not tasks:
        return []
    out = []
    for result in tasks[0].get("result") or []:
        if isinstance(result, dict):
            out.extend(x for x in result.get("items") or [] if isinstance(x, dict))
    return out


def _assemble_output(study, lane_plan, tasks, completed_responses, target_business):
    axes = {
        q.query: q for q in (ExpectedQuery(
            x.get("query"), x.get("audience_intent"), x.get("service_availability"))
            for x in study.get("queries") or [] if isinstance(x, dict) and x.get("selected"))
    }
    business = target_business or {}
    records = []
    for expected in tasks:
        envelope = completed_responses[expected.fingerprint]
        task = expected.task
        query = task["keyword"]
        meta = axes[query]
        items = _items(envelope)
        record = {
            "query": query,
            "audience_intent": meta.audience_intent,
            "service_availability": meta.service_availability,
            "origin_id": task["tag"],
            "location_coordinate": task["location_coordinate"],
            "returned_items_count": len(items),
            "competitor_titles": [str(x.get("title") or "") for x in items],
        }
        if meta.service_availability == "current":
            rank, matched = None, None
            for idx, item in enumerate(items, start=1):
                if str(item.get("cid") or "") == str(business.get("cid")) or \
                        _normalize_name(item.get("title")) == _normalize_name(business.get("name")):
                    rank, matched = idx, item
                    break
            record["rank"] = rank
            record["matched_item"] = matched
        records.append(record)
    return records


def collect_v2(*, lane_bytes: bytes, study_bytes: bytes,
               source_bytes: Mapping[str, bytes], binding: TrustedBinding,
               authority_records: tuple[AuthorityRecord, ...],
               completed_responses: Mapping[str, Mapping[str, Any]] | None = None,
               execute: bool = False, now=None):
    """Offline/estimate v2 lane consumption against a host-supplied binding.

    The binding and authority records are trusted host inputs; this generic
    function cannot authenticate their provenance. Hosts must derive them
    independently. Provider execution is intentionally unavailable here.
    """
    _require(not execute, "provider execution requires separately approved transport integration")
    # One capture only: later mutation of caller-held buffers/mappings has
    # no effect on validation, hashing, task generation, or output.
    lane_bytes = bytes(lane_bytes)
    study_bytes = bytes(study_bytes)
    source_bytes = {str(k): bytes(v) for k, v in dict(source_bytes).items()}
    if completed_responses is not None:
        completed_responses = dict(completed_responses)
    lane_sha = hashlib.sha256(lane_bytes).hexdigest()
    lane_plan = _parse(lane_bytes, "verified lane plan")
    lane_id = _lane_id_for(lane_plan)
    study, receipt = validate_v2(study_bytes, dict(source_bytes), now=now)
    _check_authority(study, source_bytes, authority_records)
    _check_study_binding(study, lane_plan, lane_id, lane_sha, binding)
    _check_expected_queries(study, binding)
    generated = _generate_tasks(lane_plan)
    generated_ids = [(t.endpoint, t.task, t.fingerprint) for t in generated]
    expected_ids = [(t.endpoint, t.task, t.fingerprint) for t in binding.expected_tasks]
    _require(generated_ids == expected_ids,
             "generated task identities differ from frozen lane binding")
    output = {
        "status": "estimate_only" if completed_responses is None else "offline_collected",
        "lane_id": lane_id, "lane_sha256": lane_sha,
        "study_receipt": receipt, "task_count": len(generated),
        "task_fingerprints": [t.fingerprint for t in generated],
    }
    if completed_responses is not None:
        missing = [t.fingerprint for t in generated if t.fingerprint not in completed_responses]
        _require(not missing, "completed responses missing for generated tasks")
        output["records"] = _assemble_output(
            study, lane_plan, generated, completed_responses, binding.target_business)
    return output


def main(argv=None) -> int:
    # A file-based CLI cannot establish the trusted binding or evidence
    # authority required above. Refuse rather than degrading to v1.
    print("Collection stopped: geogrid-study/v2 requires trusted host integration; "
          "this CLI cannot establish trust", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
