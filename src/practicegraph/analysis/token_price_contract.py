"""Closed token-price snapshot contract shared by the reader and release builder."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
IDENTITY = (
    "model_id",
    "provider",
    "channel",
    "upstream_provider",
    "api_model_id",
    "region",
    "service_tier",
    "precision",
    "context_band",
)
MONEY = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?$")


def need(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def obj(value: Any, fields: str, where: str) -> None:
    need(
        isinstance(value, dict) and set(value) == set(fields.split()),
        f"{where}: expected exactly {fields}",
    )


def string(value: Any, where: str, nullable: bool = False) -> None:
    need(
        (nullable and value is None)
        or (isinstance(value, str) and bool(value.strip()) and len(value) <= 2000),
        f"{where}: expected nonempty text" + (" or null" if nullable else ""),
    )


def choice(value: Any, choices: str, where: str) -> None:
    need(value in choices.split(), f"{where}: expected one of {choices}")


def number(value: Any, where: str, nullable: bool = False, minimum: int = 0) -> None:
    need(
        (nullable and value is None) or (type(value) is int and value >= minimum),
        f"{where}: expected integer >= {minimum}" + (" or null" if nullable else ""),
    )


def boolean(value: Any, where: str) -> None:
    need(value is None or type(value) is bool, f"{where}: expected boolean or null")


def decimal(value: Any, where: str, nullable: bool = False) -> None:
    need(
        (nullable and value is None)
        or (isinstance(value, str) and len(value) <= 28 and MONEY.fullmatch(value) is not None),
        f"{where}: expected nonnegative decimal string" + (" or null" if nullable else ""),
    )


def timestamp(value: Any, where: str, nullable: bool = False) -> Any:
    if nullable and value is None:
        return None
    need(
        isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
        f"{where}: expected UTC timestamp ending Z",
    )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{where}: invalid timestamp") from exc
    need(parsed.tzinfo == UTC, f"{where}: expected UTC")
    return parsed


def items(value: Any, where: str, nonempty: bool = False) -> Any:
    need(isinstance(value, list) and (bool(value) or not nonempty), f"{where}: expected list")
    return value


def index(rows: Any, where: str) -> Any:
    result = {}
    for row in items(rows, where, True):
        need(isinstance(row, dict) and "id" in row, f"{where}: missing row id")
        string(row["id"], f"{where}.id")
        need(row["id"] not in result, f"{where}: duplicate id {row['id']}")
        result[row["id"]] = row
    return result


def references(ids: Any, sources: Any, where: str, official: bool = False) -> None:
    items(ids, where, True)
    need(all(isinstance(i, str) for i in ids), f"{where}: reference ids must be strings")
    need(len(ids) == len(set(ids)), f"{where}: duplicate source ids")
    need(all(i in sources for i in ids), f"{where}: dangling source reference")
    need(
        not official or any(sources[i]["kind"] == "official" for i in ids),
        f"{where}: official evidence required",
    )


def validate(data: Any) -> Any:
    need(isinstance(data, dict), "expected object")
    version = data.get("schema")
    need(
        version
        in ("practicegraph.token-prices.research/1", "practicegraph.token-prices.research/2"),
        "unsupported research schema",
    )
    # Recommendation evidence was retired on 2026-09-15: a v2 edition may carry
    # both arrays (validated as before), or neither. One without the other is
    # still an error, so a half-edited edition cannot pass.
    evidence = [key for key in ("benchmarks", "offer_evidence") if key in data]
    need(
        not version.endswith("/2") or len(evidence) != 1,
        "benchmarks and offer_evidence travel together",
    )
    extended = version.endswith("/2") and len(evidence) == 2
    obj(
        data,
        "schema edition_version observed_at scope sources models offers coverage"
        + (" benchmarks offer_evidence" if extended else ""),
        "root",
    )
    string(data["edition_version"], "edition_version")
    need(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", data["edition_version"]), "invalid edition version")
    observed = timestamp(data["observed_at"], "observed_at")
    need(observed <= datetime.now(UTC), "snapshot observation is in the future")
    obj(data["scope"], "model_selection provider_selection coverage_notes", "scope")
    for key, value in data["scope"].items():
        string(value, f"scope.{key}")
    sources = index(data["sources"], "sources")
    for sid, source in sources.items():
        obj(source, "id url title publisher kind checked_at notes", f"source {sid}")
        for key in ("url", "title", "publisher", "notes"):
            string(source[key], f"{sid}.{key}")
        url = urlsplit(source["url"])
        need(
            url.scheme == "https" and url.hostname and not url.username and not url.password,
            f"{sid}: source must be a public HTTPS URL without credentials",
        )
        choice(source["kind"], "official independent", f"{sid}.kind")
        need(
            timestamp(source["checked_at"], f"{sid}.checked_at") <= observed,
            f"{sid}: source check is later than edition",
        )
    models = index(data["models"], "models")
    for mid, model in models.items():
        obj(model, "id developer name revision weights license source_ids", f"model {mid}")
        for key in ("developer", "name"):
            string(model[key], f"{mid}.{key}")
        for key in ("revision", "license"):
            string(model[key], f"{mid}.{key}", True)
        choice(model["weights"], "open closed unknown", f"{mid}.weights")
        references(model["source_ids"], sources, f"{mid}.source_ids", True)
    offers = index(data["offers"], "offers")
    for oid, offer in offers.items():
        obj(
            offer,
            "id model_id provider channel upstream_provider api_model_id region service_tier "
            "precision context_band status observed_at effective_from effective_to limits "
            "capabilities pricing terms measurements source_ids notes",
            f"offer {oid}",
        )
        for key in ("model_id", "provider", "api_model_id", "notes"):
            string(offer[key], f"{oid}.{key}")
        need(offer["model_id"] in models, f"{oid}: unknown model")
        for key in ("upstream_provider", "region", "precision"):
            string(offer[key], f"{oid}.{key}", True)
        choice(offer["channel"], "direct router", f"{oid}.channel")
        need(
            offer["channel"] != "direct" or offer["upstream_provider"] is None,
            f"{oid}: a direct offer has no separate upstream",
        )
        choice(
            offer["service_tier"], "standard batch priority dedicated other", f"{oid}.service_tier"
        )
        choice(offer["status"], "available preview deprecated unavailable unknown", f"{oid}.status")
        checked = timestamp(offer["observed_at"], f"{oid}.observed_at")
        need(checked <= observed, f"{oid}: observation is later than edition")
        start = timestamp(offer["effective_from"], f"{oid}.effective_from", True)
        end = timestamp(offer["effective_to"], f"{oid}.effective_to", True)
        need(not (start and end) or start < end, f"{oid}: invalid effective interval")
        obj(offer["context_band"], "min max", f"{oid}.context_band")
        for key, value in offer["context_band"].items():
            number(value, f"{oid}.context_band.{key}", True)
        low, high = offer["context_band"]["min"], offer["context_band"]["max"]
        need(low is None or high is None or low <= high, f"{oid}: inverted context band")
        obj(
            offer["limits"],
            "context_tokens output_tokens requests_per_minute tokens_per_minute",
            f"{oid}.limits",
        )
        for key, value in offer["limits"].items():
            number(value, f"{oid}.limits.{key}", True, 1)
        obj(offer["capabilities"], "tools structured_outputs vision", f"{oid}.capabilities")
        for key, value in offer["capabilities"].items():
            boolean(value, f"{oid}.capabilities.{key}")
        pricing = offer["pricing"]
        obj(
            pricing,
            "currency unit input output cache_read cache_write cache_ttl_seconds "
            "reasoning_billing extra_charges conditions source_ids",
            f"{oid}.pricing",
        )
        need(
            pricing["currency"] == "USD" and pricing["unit"] == "usd_per_1m_tokens",
            f"{oid}: unsupported pricing units",
        )
        for key in PRICE_FIELDS:
            decimal(pricing[key], f"{oid}.pricing.{key}", True)
        number(pricing["cache_ttl_seconds"], f"{oid}.cache_ttl_seconds", True)
        choice(
            pricing["reasoning_billing"],
            "included_in_output separate not_applicable unknown",
            f"{oid}.reasoning_billing",
        )
        string(pricing["conditions"], f"{oid}.pricing.conditions")
        references(pricing["source_ids"], sources, f"{oid}.pricing.source_ids", True)
        for fee in items(pricing["extra_charges"], f"{oid}.extra_charges"):
            obj(fee, "name amount unit conditions", f"{oid}.fee")
            for key in ("name", "unit", "conditions"):
                string(fee[key], f"{oid}.fee.{key}")
            decimal(fee["amount"], f"{oid}.fee.amount", True)
        terms = offer["terms"]
        obj(terms, "training_use retention_days zero_retention notes source_ids", f"{oid}.terms")
        choice(terms["training_use"], "yes no opt_in unknown", f"{oid}.training_use")
        number(terms["retention_days"], f"{oid}.retention_days", True)
        boolean(terms["zero_retention"], f"{oid}.zero_retention")
        string(terms["notes"], f"{oid}.terms.notes")
        if (
            terms["source_ids"]
            or terms["training_use"] != "unknown"
            or terms["retention_days"] is not None
            or terms["zero_retention"] is not None
        ):
            references(terms["source_ids"], sources, f"{oid}.terms.source_ids", True)
        else:
            items(terms["source_ids"], f"{oid}.terms.source_ids")
        references(offer["source_ids"], sources, f"{oid}.source_ids", True)
        evidence_ids = [*offer["source_ids"], *pricing["source_ids"], *terms["source_ids"]]
        need(
            all(timestamp(sources[s]["checked_at"], s) <= checked for s in evidence_ids),
            f"{oid}: source check is later than offer observation",
        )
        for measure in items(offer["measurements"], f"{oid}.measurements"):
            obj(
                measure,
                "metric value unit kind measured_at sample_size basis source_id",
                f"{oid}.measurement",
            )
            choice(
                measure["metric"],
                "output_tokens_per_second time_to_first_token_ms uptime_percent error_percent",
                f"{oid}.metric",
            )
            units = {
                "output_tokens_per_second": "tokens/s",
                "time_to_first_token_ms": "ms",
                "uptime_percent": "percent",
                "error_percent": "percent",
            }
            need(measure["unit"] == units[measure["metric"]], f"{oid}: measurement unit mismatch")
            decimal(measure["value"], f"{oid}.measurement.value")
            if measure["unit"] == "percent":
                need(Decimal(measure["value"]) <= 100, f"{oid}: percentage exceeds 100")
            choice(measure["kind"], "measured advertised", f"{oid}.measurement.kind")
            need(
                timestamp(measure["measured_at"], f"{oid}.measured_at") <= checked,
                f"{oid}: future measurement",
            )
            number(measure["sample_size"], f"{oid}.sample_size", True, 1)
            string(measure["basis"], f"{oid}.basis")
            references([measure["source_id"]], sources, f"{oid}.measurement.source_id")
            need(
                timestamp(sources[measure["source_id"]]["checked_at"], oid) <= checked,
                f"{oid}: measurement source check is later than observation",
            )
    providers = set()
    for row in items(data["coverage"], "coverage", True):
        obj(row, "provider status notes source_ids", "coverage row")
        string(row["provider"], "coverage.provider")
        need(row["provider"] not in providers, "duplicate coverage provider")
        providers.add(row["provider"])
        choice(
            row["status"],
            "checked partial blocked no_matching_offer out_of_scope",
            "coverage.status",
        )
        string(row["notes"], "coverage.notes")
        if row["source_ids"]:
            references(row["source_ids"], sources, "coverage.source_ids")
        else:
            items(row["source_ids"], "coverage.source_ids")
    need(
        all(o["provider"] in providers for o in offers.values()),
        "offer provider missing from coverage",
    )
    if extended:
        validate_recommendation_evidence(data, sources, models, offers, observed)
    return offers, models


def validate_recommendation_evidence(
    data: Any, sources: Any, models: Any, offers: Any, observed: datetime
) -> None:
    """Scores describe tested configurations; provider compatibility is separate evidence."""
    benchmarks = index(data["benchmarks"], "benchmarks")
    variants = set()
    for bid, row in benchmarks.items():
        obj(
            row,
            "id model_id task version score reasoning_effort harness observed_at source_ids notes",
            bid,
        )
        need(row["model_id"] in models, f"{bid}: unknown model")
        choice(row["task"], "general coding", bid)
        for key in ("version", "reasoning_effort", "notes"):
            string(row[key], f"{bid}.{key}")
        string(row["harness"], f"{bid}.harness", True)
        need(
            row["task"] != "coding" or row["harness"] is not None,
            "coding score requires tested harness",
        )
        decimal(row["score"], f"{bid}.score")
        need(Decimal(row["score"]) <= 100, "index score exceeds 100")
        references(row["source_ids"], sources, bid)
        need(
            any(
                urlsplit(sources[s]["url"]).hostname == "artificialanalysis.ai"
                for s in row["source_ids"]
            ),
            "AA score requires Artificial Analysis source",
        )
        checked = timestamp(row["observed_at"], bid)
        need(
            checked <= observed
            and all(timestamp(sources[s]["checked_at"], s) <= checked for s in row["source_ids"]),
            "benchmark timestamp mismatch",
        )
        variant = tuple(
            row[k] for k in ("model_id", "task", "version", "reasoning_effort", "harness")
        )
        need(variant not in variants, "duplicate benchmark configuration")
        variants.add(variant)
    seen = set()
    for row in items(data["offer_evidence"], "offer_evidence"):
        obj(
            row,
            "offer_id reasoning modes benchmark_ids match observed_at source_ids notes",
            "offer evidence",
        )
        oid = row["offer_id"]
        need(
            isinstance(oid, str) and oid in offers and oid not in seen,
            "unknown or duplicate evidence offer",
        )
        seen.add(oid)
        boolean(row["reasoning"], oid)
        choice(row["match"], "documented uncertain", oid)
        string(row["notes"], oid)
        modes = items(row["modes"], oid)
        for mode in modes:
            string(mode, oid)
        need(len(set(modes)) == len(modes), "duplicate reasoning mode")
        need(not modes or row["reasoning"] is not None, "unknown reasoning cannot assert modes")
        need(row["reasoning"] is not False or modes == ["none"], "non-reasoning mode must be none")
        references(row["source_ids"], sources, oid, True)
        checked = timestamp(row["observed_at"], oid)
        need(
            checked <= observed
            and all(timestamp(sources[s]["checked_at"], s) <= checked for s in row["source_ids"]),
            "offer evidence timestamp mismatch",
        )
        bids = items(row["benchmark_ids"], oid)
        for bid in bids:
            need(isinstance(bid, str) and bid in benchmarks, "unknown benchmark binding")
            need(benchmarks[bid]["model_id"] == offers[oid]["model_id"], "benchmark model mismatch")
            if row["match"] == "documented":
                need(
                    benchmarks[bid]["reasoning_effort"] in modes,
                    "tested reasoning setting not supported by offer",
                )
        need(len(set(bids)) == len(bids), "duplicate benchmark binding")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def summarize(data: Any, previous: Any = None) -> Any:
    offers, models = validate(data)
    result = {
        "valid": True,
        "readiness": "research_only",
        "edition_version": data["edition_version"],
        "sha256_canonical_json": hashlib.sha256(canonical(data)).hexdigest(),
        "models": len(models),
        "offers": len(offers),
        "providers": len({o["provider"] for o in offers.values()}),
        "coverage_status_counts": {},
        "new_offers": [],
        "not_reverified": [],
        "observed_quote_changes": [],
        "stale_offers_over_48h": [],
        "announced_future_offers": [],
        "expired_offers": [],
    }
    asof = timestamp(data["observed_at"], "observed_at")
    for row in data["coverage"]:
        status = row["status"]
        result["coverage_status_counts"][status] = (
            result["coverage_status_counts"].get(status, 0) + 1
        )
    for oid, row in offers.items():
        relevant_sources = set(row["pricing"]["source_ids"])
        dates = [timestamp(row["observed_at"], oid)] + [
            timestamp(s["checked_at"], s["id"])
            for s in data["sources"]
            if s["id"] in relevant_sources
        ]
        if any((asof - date).total_seconds() > 48 * 3600 for date in dates):
            result["stale_offers_over_48h"].append(oid)
        if row["effective_from"] and timestamp(row["effective_from"], oid) > asof:
            result["announced_future_offers"].append(oid)
        if row["effective_to"] and timestamp(row["effective_to"], oid) <= asof:
            result["expired_offers"].append(oid)
    if previous is None:
        result["new_offers"] = sorted(offers)
        return result
    old, old_models = validate(previous)
    need(
        timestamp(previous["observed_at"], "previous.observed_at") <= asof,
        "previous snapshot is newer",
    )
    if data["edition_version"] == previous["edition_version"]:
        need(
            canonical(data) == canonical(previous),
            "changed content reused an immutable edition version",
        )
    for mid in set(models) & set(old_models):
        need(
            all(models[mid][k] == old_models[mid][k] for k in ("developer", "revision", "weights")),
            f"{mid}: model identity changed; assign a new id",
        )
    result["new_offers"] = sorted(set(offers) - set(old))
    result["not_reverified"] = sorted(set(old) - set(offers))
    for oid in sorted(set(offers) & set(old)):
        row, before = offers[oid], old[oid]
        need(
            all(row[k] == before[k] for k in IDENTITY),
            f"{oid}: offer identity changed; assign a new id",
        )
        need(
            timestamp(row["observed_at"], oid) >= timestamp(before["observed_at"], oid),
            f"{oid}: observation moved backwards",
        )
        a, b = before["pricing"], row["pricing"]
        need(
            all(
                a[k] == b[k] for k in ("currency", "unit", "cache_ttl_seconds", "reasoning_billing")
            ),
            f"{oid}: billing basis changed; assign a new id",
        )
        changed = {
            k: {"before": a[k], "after": b[k]}
            for k in PRICE_FIELDS
            if ((a[k] is None or b[k] is None) and a[k] != b[k])
            or (a[k] is not None and b[k] is not None and Decimal(a[k]) != Decimal(b[k]))
        }
        conditions_changed = (
            a["conditions"] != b["conditions"] or a["extra_charges"] != b["extra_charges"]
        )
        if changed or conditions_changed:
            result["observed_quote_changes"].append(
                {
                    "offer_id": oid,
                    "rates": changed,
                    "conditions_changed": conditions_changed,
                    "previous_observed_at": before["observed_at"],
                    "observed_at": row["observed_at"],
                    "effective_from": row["effective_from"],
                }
            )
    return result
