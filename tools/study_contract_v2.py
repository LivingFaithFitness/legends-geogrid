"""Generic geogrid-study/v2 validation for lane-bound expanded studies.

This module is deliberately policy-free: it validates the shape, captured
evidence bytes, freshness, citations, probe settings, demand coverage, and
research-only encoding of a v2 study. It does not decide which market,
manifest, lane, or evidence authority is approved; a trusted host supplies
and checks that binding separately. The v1 contract in study_contract.py is
unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

SCHEMA = "geogrid-study/v2"
_ROLES = ("category", "service", "product", "user_requested")
_SOURCE_KINDS = ("website", "website_attempt", "gbp", "demand", "maps_probe", "organic_probe")
_PERFORMANCE_KEYS = {
    "score", "performance", "target_rank", "target_metrics", "rank", "matched_item",
    "lff_score", "performance_score",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _number(value, low, high, name):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            name + " out of range")
    return value


def _nonempty(value, name):
    require(isinstance(value, str) and bool(value.strip()), name + " is required")
    return value


def _pointer(doc, path):
    require(isinstance(path, str) and (path == "" or path.startswith("/")),
            "invalid evidence JSON pointer")
    for token in path.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            doc = doc[int(token)] if isinstance(doc, list) else doc[token]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ValueError("evidence pointer not found") from None
    return doc


def _parse(raw: bytes, what: str):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"{what} unparseable: {exc}") from None


def _age_seconds(value, now, source_id):
    stamp = datetime.fromisoformat(
        _nonempty(value, "source observed_at").replace("Z", "+00:00"))
    require(stamp.tzinfo is not None, "source timestamp needs timezone")
    age = (now - stamp).total_seconds()
    require(-300 <= age <= 30 * 86400,
            "source is future dated or older than 30 days: " + source_id)
    return stamp


def validate_v2(study_bytes: bytes, source_bytes: dict[str, bytes], now=None):
    """Validate one captured v2 study and its captured source bytes.

    source_bytes is keyed by each source record's ``file`` value. The same
    bytes are hashed and parsed; this function never opens a path.
    Returns (study, receipt). The receipt intentionally contains no market
    approval: study binding claims are returned for a trusted host to compare
    against independently derived values.
    """
    now = now or datetime.now(timezone.utc)
    plan = _parse(study_bytes, "v2 study")
    require(isinstance(plan, dict), "v2 study must be an object")
    require(plan.get("schema") == SCHEMA, "unsupported study schema")
    business = plan.get("business") or {}
    require(isinstance(business, dict), "business must be an object")
    for key in ("name", "cid", "domain", "location_label"):
        _nonempty(business.get(key), "business." + key)
    _number(business.get("lat"), -85, 85, "latitude")
    _number(business.get("lng"), -180, 180, "longitude")

    binding = plan.get("binding") or {}
    require(isinstance(binding, dict), "binding must be an object")
    for key in ("market_id", "approved_reference", "manifest_file_sha256",
                "manifest_sha256", "producer_stamp_sha256", "origin_set_sha256"):
        _nonempty(binding.get(key), "binding." + key)
    require(isinstance(binding.get("lanes"), list) and binding["lanes"],
            "binding needs at least one consumed lane")
    require(isinstance(binding.get("probe_origins"), list) and len(binding["probe_origins"]) == 3,
            "binding needs exactly three probe origins")

    sources, docs = {}, {}
    for source in plan.get("sources") or []:
        sid = _nonempty(source.get("id"), "source id")
        require(sid not in sources, "duplicate source id")
        require(source.get("kind") in _SOURCE_KINDS, "unknown source kind")
        file_name = _nonempty(source.get("file"), "source file")
        require(file_name in source_bytes, "evidence file missing: " + file_name)
        raw = source_bytes[file_name]
        require(_digest(raw) == source.get("sha256"), "source hash mismatch: " + sid)
        _nonempty(source.get("authority_record_id"), "authority record id")
        _age_seconds(source.get("observed_at"), now, sid)
        _nonempty(source.get("url"), "source URL")
        doc = _parse(raw, "evidence source " + sid)
        if source["kind"] not in ("website", "website_attempt"):
            require(isinstance(doc, dict) and doc.get("status_code") == 20000,
                    "provider envelope failed: " + sid)
            tasks = doc.get("tasks") if isinstance(doc, dict) else None
            require(isinstance(tasks, list) and tasks, "missing provider tasks")
            require(all(t.get("status_code") == 20000 for t in tasks),
                    "pending/failed provider task: " + sid)
        sources[sid] = source
        docs[sid] = doc
    require({"website", "gbp", "demand", "maps_probe"} <= {s["kind"] for s in sources.values()},
            "required website, GBP, demand and Maps pilot evidence missing")

    def cite(ref, allowed):
        require(isinstance(ref, dict), "citation must be an object")
        sid = ref.get("source_id")
        require(sid in sources, "unknown citation source")
        require(sources[sid]["kind"] in allowed, "wrong evidence type")
        value = _pointer(docs[sid], ref.get("pointer"))
        require(isinstance(value, str), "citation must point to text")
        quote = _nonempty(ref.get("quote"), "evidence quote")
        require(len(quote) >= 4 and quote in value, "quote is not present in source")
        return sid

    identity = plan.get("identity") or {}
    sid = identity.get("source_id")
    require(sid in sources and sources[sid]["kind"] == "gbp",
            "identity needs business-profile evidence")
    item = _pointer(docs[sid], identity.get("pointer"))
    require(isinstance(item, dict) and str(item.get("cid")) == business["cid"],
            "business CID differs from profile evidence")
    for key in ("latitude", "longitude"):
        expected = business["lat" if key == "latitude" else "lng"]
        require(type(item.get(key)) in (int, float) and abs(item[key] - expected) < 0.0001,
                "center differs from profile coordinates")
    cite(identity.get("website_reference"), {"website"})
    _nonempty(identity.get("reconciliation"), "identity reconciliation")

    settings = plan.get("settings") or {}
    require(isinstance(settings, dict), "settings must be an object")
    require(settings.get("method") in ("standard", "priority", "live"),
            "invalid collection method")
    geometry = settings.get("geometry") or {}
    require(isinstance(geometry, dict) and geometry.get("mode") == "frozen_irregular_origins",
            "v2 lane settings need frozen_irregular_origins geometry")
    require("grid_size" not in settings and "radius_km" not in settings,
            "square-grid settings may not be asserted for a frozen irregular lane")
    _nonempty(geometry.get("origin_set_sha256"), "geometry origin-set digest")
    _number(settings.get("depth"), 1, 100, "depth")
    require(type(settings.get("depth")) == int, "depth must be integer")
    _number(settings.get("zoom"), 1, 21, "zoom")
    require(type(settings.get("zoom")) == int, "zoom must be integer")
    require(settings.get("device") in ("desktop", "mobile"), "device required")
    _nonempty(settings.get("os"), "os")
    require(type(settings.get("search_this_area")) == bool, "search_this_area required")
    require(settings.get("search_places") is False, "search_places must be false")
    for key in ("language_code", "se_domain", "geography_reason", "decision"):
        _nonempty(settings.get(key), key)
    require(type(settings.get("demand_location_code")) == int, "demand location code required")

    bound_points = []
    for point in binding["probe_origins"]:
        require(isinstance(point, dict), "probe origin must be an object")
        _nonempty(point.get("origin_id"), "probe origin_id")
        _number(point.get("lat"), -85, 85, "probe latitude")
        _number(point.get("lng"), -180, 180, "probe longitude")
        bound_points.append((point["origin_id"], point["lat"], point["lng"]))
    require(len(set(bound_points)) == 3, "bound probe origins must be distinct")

    selected, selected_norm = [], set()
    theme_defs = {}
    demand_states = {}
    for query in plan.get("queries") or []:
        require(isinstance(query, dict), "query must be an object")
        term = _nonempty(query.get("query"), "query")
        normalized = _nonempty(query.get("provider_normalized_query"),
                                "provider-normalized query")
        require(type(query.get("selected")) == bool, "selection must be explicit")
        _nonempty(query.get("reason"), "selection/rejection reason")
        _nonempty(query.get("audience_intent"), "audience_intent")
        _nonempty(query.get("service_availability"), "service_availability")
        if not query["selected"]:
            continue
        require(normalized.casefold() not in selected_norm,
                "duplicate selected query identity")
        selected_norm.add(normalized.casefold())
        require(query.get("role") in _ROLES, "nonbrand query role required")
        role = query.get("evidence_role")
        current = query["service_availability"] == "current"
        require(role == ("current_offering" if current else "research_only"),
                "evidence_role does not match service_availability")
        forbidden = sorted(_PERFORMANCE_KEYS & set(query))
        if current:
            refs = query.get("offering_evidence") or []
            require(refs, "offering evidence required")
            for ref in refs:
                cite(ref, {"website", "gbp"})
            require(not query.get("market_evidence"),
                    "current query may not substitute market evidence for offering evidence")
        else:
            require(not query.get("offering_evidence"),
                    "research-only query may not carry offering evidence")
            require(not forbidden,
                    "research-only query may not carry LFF performance fields: "
                    + ", ".join(forbidden))
            refs = query.get("market_evidence") or []
            require(refs, "market/competitor evidence required")
            for ref in refs:
                cite(ref, {"maps_probe", "demand", "organic_probe"})
        if query["role"] == "user_requested":
            _nonempty(query.get("user_instruction"), "user keyword provenance")

        demand_id = query.get("demand_source")
        require(demand_id in sources and sources[demand_id]["kind"] == "demand",
                "demand research required")
        tasks = docs[demand_id].get("tasks") or []
        require(tasks, "demand envelope has no tasks")
        task0 = tasks[0]
        data = task0.get("data") or {}
        submitted = data.get("keywords") or []
        require(normalized in submitted, "demand request does not include query")
        task_loc = data.get("location_code")
        require(task_loc == settings["demand_location_code"],
                "demand task location_code mismatch")
        items = []
        for result in task0.get("result") or []:
            if isinstance(result, dict):
                items.append(result)
                item_loc = result.get("location_code")
                if item_loc is not None:
                    require(item_loc == settings["demand_location_code"],
                            "demand result location_code mismatch")
        matched = [r for r in items
                   if str(r.get("keyword", "")).casefold() == normalized.casefold()]
        volume = None
        if matched:
            info = matched[0].get("keyword_info") or {}
            volume = info.get("search_volume", matched[0].get("search_volume"))
        demand_states[term] = "present" if volume is not None else "missing"
        _nonempty(query.get("demand_scope"), "demand geography/time scope")
        _nonempty(query.get("intent_assessment"), "intent assessment")
        _nonempty(query.get("limitations"), "query limitations")

        pilot = query.get("pilot_sources") or []
        require(len(set(pilot)) >= 3, "at least three pilot origins required per selected query")
        points = set()
        for pid in pilot:
            require(pid in sources and sources[pid]["kind"] == "maps_probe",
                    "pilot evidence required")
            ptasks = docs[pid].get("tasks") or []
            require(ptasks, "pilot envelope has no tasks")
            pdata = ptasks[0].get("data") or {}
            require(pdata.get("keyword") == term, "pilot query mismatch")
            require(pdata.get("language_code") == settings["language_code"],
                    "pilot language mismatch")
            coordinate = str(pdata.get("location_coordinate", "")).split(",")
            require(len(coordinate) == 3, "pilot coordinates required")
            lat, lng = float(coordinate[0]), float(coordinate[1])
            _number(lat, -85, 85, "pilot latitude")
            _number(lng, -180, 180, "pilot longitude")
            require(abs(lat - business["lat"]) < 1 and abs(lng - business["lng"]) < 1,
                    "pilot is in a different market")
            require(coordinate[2].rstrip("z") == str(settings["zoom"]), "pilot zoom mismatch")
            require(pdata.get("device") == settings["device"], "pilot device mismatch")
            require(pdata.get("os") == settings["os"], "pilot os mismatch")
            require(pdata.get("se_domain") == settings["se_domain"], "pilot domain mismatch")
            require(pdata.get("search_this_area") == settings["search_this_area"],
                    "pilot settings mismatch")
            require(pdata.get("search_places", False) is False, "pilot search_places mismatch")
            require(pdata.get("depth") == settings["depth"], "pilot depth mismatch")
            require(ptasks[0].get("result") and
                    any(r.get("items") for r in ptasks[0]["result"] if isinstance(r, dict)),
                    "empty pilot cannot establish intent")
            points.add((lat, lng))
        require(points == {(lat, lng) for _, lat, lng in bound_points},
                "pilot points differ from bound probe origins")
        refs = query.get("intent_evidence") or []
        require(refs, "intent assessment needs returned-result citations")
        for ref in refs:
            require(cite(ref, {"maps_probe"}) in pilot,
                    "intent evidence must come from this query pilot")
        theme = query.get("theme") or {}
        require(isinstance(theme, dict), "theme must be an object")
        for key in ("id", "label", "customer_need", "distinct_value"):
            _nonempty(theme.get(key), "theme." + key)
        theme_key = " ".join(theme["id"].casefold().split())
        definition = (theme["label"], theme["customer_need"], theme["distinct_value"])
        if theme_key in theme_defs:
            require(theme_defs[theme_key] == definition,
                    "conflicting definitions for one theme id")
        theme_defs[theme_key] = definition
        selected.append(query)
    require(1 <= len(selected) <= 1000, "selected query count out of range")

    selection = plan.get("selection") or {}
    _nonempty(selection.get("coverage_summary"), "selection coverage_summary")
    covered = {sources[cite(ref, {"website", "gbp"})]["kind"]
               for ref in selection.get("evidence") or []}
    require({"website", "gbp"} <= covered, "selection needs website and GBP evidence")
    receipt = {
        "status": "plan_ready_v2",
        "plan_sha256": _digest(study_bytes),
        "selected_queries": [q["query"] for q in selected],
        "selected_themes": [dict(q["theme"], query=q["query"]) for q in selected],
        "demand_states": demand_states,
        "source_count": len(sources),
        "qualification": "Evidence lineage validated; agent interpretations are not independently certified.",
    }
    return plan, receipt
