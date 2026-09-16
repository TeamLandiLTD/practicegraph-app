"""The Advisor's market layer: curated model economics + editorial verdicts.

Phase 2 of docs/ADVISOR_PLAN.md. One weekly-curated artifact carries (a) per
model-family measurements with individual sources and observation dates
(the corroboration layer) and (b) human-written
verdict cards (tier + verdict + boundary + action, the editorial layer, same
curation trust bar as news).

Trust posture is identical to news/models: the artifact is UNTRUSTED input —
closed schema, integer-only metrics, length-capped copy under the lexicon +
leak scans, https handled by the catalog pull, families drawn from the closed
wire vocabulary. None on any deviation; the fusion engine (Phase 3) treats an
absent artifact as "market unverified" and degrades honestly, never guesses.

Expiry is load-bearing (docs/ADVISOR_PLAN.md §7): past ``expires`` the market
rows stop feeding switch advice — local receipts never rot, market claims do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from ipaddress import ip_address
from urllib.parse import urlsplit

from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.wire import ModelFamily

LEGACY_SCHEMA = "practicegraph.advisor/1"
SCHEMA = "practicegraph.advisor/2"
SOURCE_NAME = "Artificial Analysis"
SOURCE_URL = "https://artificialanalysis.ai/models"
ATTRIBUTION = "Model benchmark and price data: Artificial Analysis"

# Closed verdict tiers (docs/ADVISOR_PLAN.md §3): the Thoughtworks-radar shape
# with the calm anti-FOMO lane ("wait") as a first-class outcome.
VERDICT_TIERS: tuple[str, ...] = ("use", "try", "wait", "avoid", "watch")

MAX_MODELS = 24
MAX_VERDICTS = 12
MAX_VERDICT_LEN = 160
MAX_BOUNDARY_LEN = 160
MAX_STEELMAN_LEN = 200
MAX_ACTION_LEN = 180
MAX_EXPERIMENT_LEN = 180

_FAMILY_VALUES = frozenset(
    family.value for family in ModelFamily if family is not ModelFamily.OTHER
)
_TOOL_VALUES = frozenset({"codex", "claude_code"})

_ROOT_KEYS = frozenset(
    {"schema", "artifact_version", "as_of", "expires", "source", "models", "verdicts"}
)
_SOURCE_KEYS = frozenset({"name", "url", "attribution"})
_MODEL_KEYS = frozenset(
    {
        "family",
        "aa_slug",
        "released_on",
        "coding_index_tenths",
        "agentic_index_tenths",
        "price_in_micro",
        "price_out_micro",
        "price_cache_read_micro",
        "median_tps_tenths",
        "ttft_ms",
        "context_window",
    }
)
_VERDICT_KEYS = frozenset(
    {
        "id",
        "tier",
        "families",
        "tools",
        "verdict",
        "boundary",
        "steelman",
        "action",
        "experiment",
        "as_of",
        "expires",
    }
)

_VERSION = re.compile(r"[a-z0-9][a-z0-9.-]{0,79}\Z")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9.]+)*\Z")

# Integer sanity bounds. Prices are micro-USD per 1M tokens (the ratecard
# unit); the cap is $1,000/Mtok — far above any real rate, low enough to
# reject nonsense.
_MAX_PRICE_MICRO = 1_000_000_000
_MAX_TPS_TENTHS = 100_000  # 10,000 tokens/s
_MAX_TTFT_MS = 600_000
_MAX_CONTEXT = 100_000_000

# All prices describe comparable standard API rates in USD per million tokens.
# A basis identifies the context/service tier or exact benchmark methodology.
METRIC_BOUNDS = {
    "coding_index_tenths": 1000,
    "agentic_index_tenths": 1000,
    "price_in_micro": _MAX_PRICE_MICRO,
    "price_out_micro": _MAX_PRICE_MICRO,
    "price_cache_read_micro": _MAX_PRICE_MICRO,
    "median_tps_tenths": _MAX_TPS_TENTHS,
    "ttft_ms": _MAX_TTFT_MS,
    "context_window": _MAX_CONTEXT,
}
METRICS = ("released_on", *METRIC_BOUNDS)
PRICE_METRICS = ("price_in_micro", "price_out_micro", "price_cache_read_micro")
METRIC_REVIEW_DAYS = 14


@dataclass(frozen=True, slots=True)
class MetricEvidence:
    metric: str
    source_name: str
    source_url: str
    observed_on: date
    basis: str


@dataclass(frozen=True, slots=True)
class CitedMeasurement:
    family: str
    model_id: str
    metric: str
    value: int | str
    source_name: str
    source_url: str
    observed_on: str
    basis: str
    valid_until: str


@dataclass(frozen=True, slots=True)
class MarketModel:
    """One family's market row — corroboration data, never primary evidence."""

    family: str  # closed ModelFamily value (never "other")
    aa_slug: str
    released_on: date | None
    coding_index_tenths: int | None
    agentic_index_tenths: int | None
    price_in_micro: int | None
    price_out_micro: int | None
    price_cache_read_micro: int | None
    median_tps_tenths: int | None
    ttft_ms: int | None
    context_window: int | None
    model_id: str = ""
    evidence: tuple[MetricEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class AdvisorVerdict:
    """One curated verdict card: the editorial layer, human-signed-off.

    ``steelman`` and ``experiment`` may be empty; verdict/boundary/action are
    the mandatory Theo-shape (verdict + boundary welded, one imperative)."""

    verdict_id: str
    tier: str
    families: tuple[str, ...]
    tools: tuple[str, ...]
    verdict: str
    boundary: str
    steelman: str
    action: str
    experiment: str
    as_of: date
    expires: date
    evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AdvisorArtifact:
    artifact_version: str
    as_of: date
    expires: date
    models: tuple[MarketModel, ...]
    verdicts: tuple[AdvisorVerdict, ...]
    schema: str = LEGACY_SCHEMA


def market_stale(artifact: AdvisorArtifact, today: date) -> bool:
    """Past expiry the market layer degrades to "unverified since" — switch
    detectors stop firing (docs/ADVISOR_PLAN.md §7)."""
    return today < artifact.as_of or today > artifact.expires


def model_metrics(model: MarketModel) -> dict[str, int | date | None]:
    return {name: getattr(model, name) for name in METRICS}


def metric_current(model: MarketModel, metric: str, today: date) -> bool:
    if metric not in METRICS or model_metrics(model)[metric] is None:
        return False
    if not model.evidence:  # v1 keeps its original whole-edition expiry semantics.
        return True
    evidence = next((item for item in model.evidence if item.metric == metric), None)
    if evidence is None or today < evidence.observed_on:
        return False
    return metric == "released_on" or (today - evidence.observed_on).days <= METRIC_REVIEW_DAYS


def metric_basis(model: MarketModel, metric: str) -> str:
    evidence = next((item for item in model.evidence if item.metric == metric), None)
    return evidence.basis if evidence else "legacy-aa-standard-api"


def cited_measurements(
    model: MarketModel, metrics: tuple[str, ...],
) -> tuple[CitedMeasurement, ...]:
    citations = []
    for metric in metrics:
        value = model_metrics(model).get(metric)
        if value is None:
            continue
        evidence = next((item for item in model.evidence if item.metric == metric), None)
        citations.append(CitedMeasurement(
            family=model.family, model_id=model.model_id or model.aa_slug, metric=metric,
            value=value.isoformat() if isinstance(value, date) else value,
            source_name=evidence.source_name if evidence else SOURCE_NAME,
            source_url=evidence.source_url if evidence else SOURCE_URL,
            observed_on=evidence.observed_on.isoformat() if evidence else "",
            basis=evidence.basis if evidence else "Legacy edition; measurement basis not recorded",
            valid_until=(date.fromordinal(min(
                date.max.toordinal(), evidence.observed_on.toordinal() + METRIC_REVIEW_DAYS,
            )).isoformat() if evidence and metric != "released_on" else ""),
        ))
    return tuple(citations)


def evidence_attribution(evidence: tuple[CitedMeasurement, ...]) -> str:
    names = sorted({item.source_name for item in evidence})
    if names == [SOURCE_NAME]:
        return ATTRIBUTION
    return "Market data: " + ", ".join(names) if names else ""


def evidence_expiry(expires: str, evidence: tuple[CitedMeasurement, ...]) -> str:
    return min([expires, *(item.valid_until for item in evidence if item.valid_until)])


def safe_evidence_url(value: object) -> bool:
    """Public HTTPS citations only; no credentials, local hosts or tracking queries."""
    if not isinstance(value, str) or len(value) > 1000 or any(
        ord(c) <= 32 or ord(c) >= 127 or c == "\\" for c in value
    ):
        return False
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.port not in (None, 443)
                or "." not in hostname or hostname.endswith((".local", ".localhost", ".internal"))
                or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", hostname)
                or any(not label or label.startswith("-") or label.endswith("-")
                       for label in hostname.split("."))
                or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", value, re.IGNORECASE)):
            return False
        try:
            ip_address(hostname)
        except ValueError:
            return not all(label.isdigit() for label in hostname.split("."))
        return False
    except ValueError:
        return False


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _bounded_int(value: object, low: int, high: int) -> int | None:
    if type(value) is not int or not low <= value <= high:
        return None
    return value


def _clean_copy(value: object, max_len: int, *, required: bool) -> str | None:
    """A copy field under the caps and the lexicon + leak scans. Optional
    fields accept "" (present but empty); None on any deviation."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None if required else ""
    if len(text) > max_len:
        return None
    if lexicon_violations(text) or leak_findings(text):
        return None
    return text


def _parse_model(raw: object) -> MarketModel | None:
    if not isinstance(raw, dict) or set(raw) != _MODEL_KEYS:
        return None
    family = raw["family"]
    slug = raw["aa_slug"]
    released_on = _parse_date(raw["released_on"])
    coding = _bounded_int(raw["coding_index_tenths"], 0, 1000)
    agentic = _bounded_int(raw["agentic_index_tenths"], 0, 1000)
    price_in = _bounded_int(raw["price_in_micro"], 0, _MAX_PRICE_MICRO)
    price_out = _bounded_int(raw["price_out_micro"], 0, _MAX_PRICE_MICRO)
    price_cache = _bounded_int(raw["price_cache_read_micro"], 0, _MAX_PRICE_MICRO)
    tps = _bounded_int(raw["median_tps_tenths"], 0, _MAX_TPS_TENTHS)
    ttft = _bounded_int(raw["ttft_ms"], 0, _MAX_TTFT_MS)
    context = _bounded_int(raw["context_window"], 0, _MAX_CONTEXT)
    if (
        not isinstance(family, str)
        or family not in _FAMILY_VALUES
        or not isinstance(slug, str)
        or _SLUG.fullmatch(slug) is None
        or released_on is None
        or coding is None
        or agentic is None
        or price_in is None
        or price_out is None
        or price_cache is None
        or tps is None
        or ttft is None
        or context is None
    ):
        return None
    return MarketModel(
        family=str(family),
        aa_slug=slug,
        released_on=released_on,
        coding_index_tenths=coding,
        agentic_index_tenths=agentic,
        price_in_micro=price_in,
        price_out_micro=price_out,
        price_cache_read_micro=price_cache,
        median_tps_tenths=tps,
        ttft_ms=ttft,
        context_window=context,
    )


def _parse_model_v2(raw: object, as_of: date) -> MarketModel | None:
    if not isinstance(raw, dict) or set(raw) != {"family", "model_id", "metrics"}:
        return None
    family, model_id, metrics = raw["family"], raw["model_id"], raw["metrics"]
    if (not isinstance(family, str) or family not in _FAMILY_VALUES
            or not isinstance(model_id, str) or len(model_id) > 100
            or _SLUG.fullmatch(model_id) is None
            or not isinstance(metrics, dict) or not set(metrics).issubset(METRICS)):
        return None
    values: dict[str, int | None] = dict.fromkeys(METRIC_BOUNDS)
    released_on = None
    evidence = []
    for metric in METRICS:
        measurement = metrics.get(metric)
        if measurement is None:
            continue
        if not isinstance(measurement, dict) or set(measurement) != {
            "value", "source_name", "source_url", "observed_on", "basis",
        }:
            return None
        name = _clean_copy(measurement["source_name"], 80, required=True)
        basis = _clean_copy(measurement["basis"], 160, required=True)
        observed = _parse_date(measurement["observed_on"])
        if (name is None or basis is None or observed is None or observed > as_of
                or any(ord(c) < 32 for c in name + basis)
                or not safe_evidence_url(measurement["source_url"])):
            return None
        if metric == "released_on":
            released_on = _parse_date(measurement["value"])
            if released_on is None or released_on > observed:
                return None
        else:
            value = _bounded_int(measurement["value"], 0, METRIC_BOUNDS[metric])
            if value is None:
                return None
            values[metric] = value
        evidence.append(MetricEvidence(
            metric, name, str(measurement["source_url"]), observed, basis,
        ))
    if not evidence:
        return None  # A family needs at least one supported fact, not an empty placeholder.
    return MarketModel(
        family=family, aa_slug="", model_id=model_id, released_on=released_on,
        coding_index_tenths=values["coding_index_tenths"],
        agentic_index_tenths=values["agentic_index_tenths"],
        price_in_micro=values["price_in_micro"], price_out_micro=values["price_out_micro"],
        price_cache_read_micro=values["price_cache_read_micro"],
        median_tps_tenths=values["median_tps_tenths"], ttft_ms=values["ttft_ms"],
        context_window=values["context_window"], evidence=tuple(evidence),
    )


def _closed_value_tuple(
    raw: object, allowed: frozenset[str], max_len: int
) -> tuple[str, ...] | None:
    """A non-empty, duplicate-free list of closed vocabulary values."""
    if not isinstance(raw, list) or not 0 < len(raw) <= max_len:
        return None
    values: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in allowed or value in values:
            return None
        values.append(str(value))
    return tuple(values)


def _parse_verdict(raw: object, *, v2: bool = False) -> AdvisorVerdict | None:
    keys = _VERDICT_KEYS | {"evidence"} if v2 else _VERDICT_KEYS
    if not isinstance(raw, dict) or set(raw) != keys:
        return None
    verdict_id = raw["id"]
    families = _closed_value_tuple(raw["families"], _FAMILY_VALUES, 4)
    tools = _closed_value_tuple(raw["tools"], _TOOL_VALUES, len(_TOOL_VALUES))
    verdict = _clean_copy(raw["verdict"], MAX_VERDICT_LEN, required=True)
    boundary = _clean_copy(raw["boundary"], MAX_BOUNDARY_LEN, required=True)
    steelman = _clean_copy(raw["steelman"], MAX_STEELMAN_LEN, required=False)
    action = _clean_copy(raw["action"], MAX_ACTION_LEN, required=True)
    experiment = _clean_copy(raw["experiment"], MAX_EXPERIMENT_LEN, required=False)
    as_of = _parse_date(raw["as_of"])
    expires = _parse_date(raw["expires"])
    if (
        not isinstance(verdict_id, str)
        or _SLUG.fullmatch(verdict_id) is None
        or raw["tier"] not in VERDICT_TIERS
        or families is None
        or tools is None
        or verdict is None
        or boundary is None
        or steelman is None
        or action is None
        or experiment is None
        or as_of is None
        or expires is None
        or expires < as_of
    ):
        return None
    references: list[tuple[str, str]] = []
    if v2:
        raw_references = raw["evidence"]
        if not isinstance(raw_references, list) or not 0 < len(raw_references) <= 36:
            return None
        for reference in raw_references:
            if not isinstance(reference, dict) or set(reference) != {"family", "metric"}:
                return None
            family, metric = reference["family"], reference["metric"]
            if (not isinstance(family, str) or family not in families
                    or not isinstance(metric, str) or metric not in METRICS
                    or (family, metric) in references):
                return None
            references.append((family, metric))
    return AdvisorVerdict(
        verdict_id=verdict_id,
        tier=str(raw["tier"]),
        families=families,
        tools=tools,
        verdict=verdict,
        boundary=boundary,
        steelman=steelman,
        action=action,
        experiment=experiment,
        as_of=as_of,
        expires=expires,
        evidence=tuple(references),
    )


def parse_advisor_artifact(raw: object) -> AdvisorArtifact | None:
    """Strict closed validation; None on any deviation (never fail-open)."""
    if not isinstance(raw, dict):
        return None
    schema = raw.get("schema")
    if schema not in (LEGACY_SCHEMA, SCHEMA):
        return None
    v2 = schema == SCHEMA
    if set(raw) != (_ROOT_KEYS - {"source"} if v2 else _ROOT_KEYS):
        return None
    version = raw["artifact_version"]
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        return None
    as_of = _parse_date(raw["as_of"])
    expires = _parse_date(raw["expires"])
    if as_of is None or expires is None or expires < as_of:
        return None
    if not v2 and raw["source"] != {
        "name": SOURCE_NAME,
        "url": SOURCE_URL,
        "attribution": ATTRIBUTION,
    }:
        return None

    models_raw = raw["models"]
    if not isinstance(models_raw, list) or not 0 < len(models_raw) <= MAX_MODELS:
        return None
    models: list[MarketModel] = []
    families_seen: set[str] = set()
    for entry in models_raw:
        model = _parse_model_v2(entry, as_of) if v2 else _parse_model(entry)
        if model is None or model.family in families_seen:
            return None
        families_seen.add(model.family)
        models.append(model)

    verdicts_raw = raw["verdicts"]
    if not isinstance(verdicts_raw, list) or len(verdicts_raw) > MAX_VERDICTS:
        return None
    verdicts: list[AdvisorVerdict] = []
    ids_seen: set[str] = set()
    for entry in verdicts_raw:
        verdict = _parse_verdict(entry, v2=v2)
        if verdict is None or verdict.verdict_id in ids_seen:
            return None
        # A verdict may only reference families the artifact prices — the
        # fusion engine joins them and must never dangle.
        if any(family not in families_seen for family in verdict.families):
            return None
        if v2:
            by_family = {model.family: model for model in models}
            if verdict.as_of > as_of or any(
                not metric_current(by_family[family], metric, verdict.as_of)
                for family, metric in verdict.evidence
            ):
                return None
        ids_seen.add(verdict.verdict_id)
        verdicts.append(verdict)

    return AdvisorArtifact(
        artifact_version=version,
        as_of=as_of,
        expires=expires,
        models=tuple(models),
        verdicts=tuple(verdicts),
        schema=str(schema),
    )


def advisor_artifact_to_dict(artifact: AdvisorArtifact) -> dict[str, object]:
    """The publish-side serializer (curate tool); round-trips through parse."""
    document: dict[str, object] = {
        "schema": artifact.schema,
        "artifact_version": artifact.artifact_version,
        "as_of": artifact.as_of.isoformat(),
        "expires": artifact.expires.isoformat(),
        "source": {
            "name": SOURCE_NAME,
            "url": SOURCE_URL,
            "attribution": ATTRIBUTION,
        },
        "models": [
            {
                "family": model.family,
                "aa_slug": model.aa_slug,
                "released_on": model.released_on.isoformat() if model.released_on else None,
                "coding_index_tenths": model.coding_index_tenths,
                "agentic_index_tenths": model.agentic_index_tenths,
                "price_in_micro": model.price_in_micro,
                "price_out_micro": model.price_out_micro,
                "price_cache_read_micro": model.price_cache_read_micro,
                "median_tps_tenths": model.median_tps_tenths,
                "ttft_ms": model.ttft_ms,
                "context_window": model.context_window,
            }
            for model in artifact.models
        ],
        "verdicts": [
            {
                "id": verdict.verdict_id,
                "tier": verdict.tier,
                "families": list(verdict.families),
                "tools": list(verdict.tools),
                "verdict": verdict.verdict,
                "boundary": verdict.boundary,
                "steelman": verdict.steelman,
                "action": verdict.action,
                "experiment": verdict.experiment,
                "as_of": verdict.as_of.isoformat(),
                "expires": verdict.expires.isoformat(),
            }
            for verdict in artifact.verdicts
        ],
    }
    if artifact.schema == SCHEMA:
        del document["source"]
        document["models"] = [
            {
                "family": model.family, "model_id": model.model_id,
                "metrics": {
                    metric: next(({
                        "value": value.isoformat() if isinstance(value, date) else value,
                        "source_name": evidence.source_name,
                        "source_url": evidence.source_url,
                        "observed_on": evidence.observed_on.isoformat(),
                        "basis": evidence.basis,
                    } for evidence in model.evidence if evidence.metric == metric), None)
                    for metric, value in model_metrics(model).items()
                },
            }
            for model in artifact.models
        ]
        verdict_documents = document["verdicts"]
        assert isinstance(verdict_documents, list)
        for verdict_document, verdict in zip(verdict_documents, artifact.verdicts, strict=True):
            verdict_document["evidence"] = [
                {"family": family, "metric": metric} for family, metric in verdict.evidence
            ]
    return document
