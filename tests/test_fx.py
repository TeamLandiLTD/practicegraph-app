"""Display currency: ECB reference rates parsed strictly, used only to show
amounts. Money stays integer micro-USD everywhere else."""

from __future__ import annotations

from decimal import Decimal

import pytest

from practicegraph.analysis import fx

ECB = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
  <Cube>
    <Cube time='2026-09-29'>
      <Cube currency='USD' rate='1.1355'/>
      <Cube currency='JPY' rate='178.41'/>
      <Cube currency='GBP' rate='0.85718'/>
      <Cube currency='CHF' rate='0.9461'/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


def rates() -> fx.FxRates:
    parsed = fx.parse_ecb_daily(ECB)
    assert parsed is not None
    return parsed


def test_parses_the_ecb_daily_file() -> None:
    r = rates()
    assert r.date == "2026-09-29"
    assert r.per_eur == {"EUR": Decimal(1), "USD": Decimal("1.1355"), "JPY": Decimal("178.41"),
                         "GBP": Decimal("0.85718"), "CHF": Decimal("0.9461")}


@pytest.mark.parametrize("bad", [
    b"",
    b"not xml",
    ECB.replace(b"currency='USD' rate='1.1355'", b"currency='AUD' rate='1.62'"),  # no USD
    ECB.replace(b"time='2026-09-29'", b"time='yesterday'"),
    ECB.replace(b"rate='178.41'", b"rate='-1'"),
    ECB.replace(b"rate='178.41'", b"rate='NaN'"),
    ECB.replace(b"currency='JPY'", b"currency='usd'"),
    ECB.replace(b"currency='GBP'", b"currency='JPY'"),  # duplicate currency
    ECB.replace(b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
                b"<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY a \"aaaa\">]>"),
    # An explicit id: pytest publishes the running test id in an environment
    # variable, and this 64 KB parameter would exceed the Windows limit of
    # 32,767 characters and error the test at setup.
    pytest.param(b"<x>" + b"a" * (fx.MAX_FX_BYTES + 1) + b"</x>", id="oversized"),
    # UTF-16 hides "<!DOCTYPE" from a byte-level check while expat still
    # honours it; the wide encoding itself is refused.
    pytest.param(
        ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
         + ECB.decode("utf-8").split("?>", 1)[1]).encode("utf-16"),
        id="utf-16-doctype",
    ),
    pytest.param(ECB.decode("utf-8").encode("utf-16"), id="utf-16-plain"),
])
def test_anything_unexpected_is_refused(bad: bytes) -> None:
    assert fx.parse_ecb_daily(bad) is None


def test_usd_factor_goes_through_the_euro() -> None:
    r = rates()
    assert fx.usd_factor(r, "USD") == Decimal(1)
    assert fx.usd_factor(r, "EUR") == Decimal(1) / Decimal("1.1355")
    assert fx.usd_factor(r, "GBP") == Decimal("0.85718") / Decimal("1.1355")
    assert fx.usd_factor(r, "SEK") is None  # not in this file
    assert fx.usd_factor(None, "EUR") is None


def test_every_ecb_currency_has_a_display_format() -> None:
    for code in ("USD", "EUR", "JPY", "CZK", "DKK", "GBP", "HUF", "PLN", "RON", "SEK", "CHF",
                 "ISK", "NOK", "TRY", "AUD", "BRL", "CAD", "CNY", "HKD", "IDR", "ILS", "INR",
                 "KRW", "MXN", "MYR", "NZD", "PHP", "SGD", "THB", "ZAR"):
        prefix, digits, name = fx.CURRENCIES[code]
        assert prefix and name and digits in (0, 2)


def test_format_money_follows_each_currency() -> None:
    assert fx.format_money(Decimal("1234.5"), "EUR") == "€1,234.50"
    assert fx.format_money(Decimal("1234.5"), "JPY") == "¥1,235"
    assert fx.format_money(Decimal("12.3"), "CHF") == "CHF\u00a012.30"
    assert fx.format_money(Decimal("1234.5"), "EUR", digits=0) == "€1,235"


def test_money_in_sentences_is_converted_keeping_its_precision() -> None:
    factor = Decimal("0.5")
    text = "Of the cache spend, $1,128 read context back and $444.10 wrote it in."
    assert fx.convert_money_text(text, factor, "EUR") == (
        "Of the cache spend, €564 read context back and €222.05 wrote it in.")
    assert fx.convert_money_text("about $130.73 on Claude Code", factor, "JPY") == (
        "about ¥65 on Claude Code")
    # Not money: shell variables, commands, a bare dollar sign.
    for untouched in ("$skill-installer install x", "launchctl gui/$(id -u)", "costs $ nothing"):
        assert fx.convert_money_text(untouched, factor, "EUR") == untouched


def test_the_view_block_describes_what_is_shown() -> None:
    r = rates()
    block = fx.display_currency("GBP", r)
    assert block["code"] == "GBP" and block["requested"] == "GBP" and not block["missing"]
    assert Decimal(block["factor"]) == pytest.approx(Decimal("0.85718") / Decimal("1.1355"))
    assert block["prefix"] == "£" and block["digits"] == 2
    assert block["date"] == "2026-09-29" and block["source"] == "European Central Bank"
    assert [c["code"] for c in block["available"]][:2] == ["USD", "EUR"]
    assert {"code": "JPY", "name": "Japanese Yen"} in block["available"]
    # Asked for a currency this file lacks, or no rates yet: show USD, say so.
    for requested, source in (("SEK", r), ("GBP", None)):
        missing = fx.display_currency(requested, source)
        assert missing["code"] == "USD" and missing["factor"] == "1" and missing["missing"]
    # The list never depends on having rates: choosing a currency is what
    # triggers the first download (nobody who keeps USD contacts the ECB).
    usd = fx.display_currency("USD", None)
    assert usd["code"] == "USD" and not usd["missing"] and usd["date"] is None
    assert [c["code"] for c in usd["available"]] == [c["code"] for c in block["available"]]
    assert len(usd["available"]) == len(fx.CURRENCIES)


def test_the_view_converts_engine_sentences_but_never_quoted_editions() -> None:
    view = {
        "economy": {"churn": {"line": "Of the cache spend, $1,128 read context back."}},
        "sessions": {"receipt": {"line": "704 assistant turns, about $130.73 on Claude Code."}},
        "advisor": {"takes": [{"cost": "$12.30"}]},
        "news": [{"summary": "The new plan costs $20 a month."}],
        "community": [{"hook": "Someone spent $400 in a day."}],
        "build_ideas": [{"title": "A $5 tool"}],
        "totals": {"cost_micro_usd": 25_000_000},
    }
    shown = fx.apply_display_currency(view, "EUR", rates())
    factor = Decimal(1) / Decimal("1.1355")
    assert shown["economy"]["churn"]["line"] == (
        f"Of the cache spend, {fx.format_money(Decimal(1128) * factor, 'EUR', digits=0)} "
        "read context back.")
    assert "$" not in shown["sessions"]["receipt"]["line"]
    assert shown["advisor"]["takes"][0]["cost"].startswith("€")
    assert shown["news"] == view["news"] and shown["community"] == view["community"]
    assert shown["build_ideas"] == view["build_ideas"]
    assert shown["totals"] == {"cost_micro_usd": 25_000_000}  # numbers stay USD
    assert shown["currency"]["code"] == "EUR"
    # USD shows exactly what the engine wrote.
    same = fx.apply_display_currency(view, "USD", rates())
    assert {k: v for k, v in same.items() if k != "currency"} == view


# ---- preference and download --------------------------------------------

import json  # noqa: E402
import urllib.error  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from practicegraph import catalog as catalog_module  # noqa: E402
from practicegraph.config import Config, read_prefs  # noqa: E402
from practicegraph.store import Store  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _setup(tmp_path: Path, currency: str | None, api_base_url: str | None = None):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    if currency is not None:
        (data / "config.json").write_text(json.dumps({"currency": currency}), encoding="utf-8")
    store = Store(data / "state.db")
    store.migrate()
    config = Config(
        data_dir=data, data_dir_source="env", api_base_url=api_base_url,
        api_base_url_source="env" if api_base_url else "default", org_id=None,
        org_id_source="default", org_token_present=False, org_token_source="default",
        config_file_state="ok",
    )
    return store, config


@pytest.mark.parametrize("raw,expected", [
    (None, "USD"), ("EUR", "EUR"), ("GBP", "GBP"), ("eur", "USD"), ("XXX", "USD"), (5, "USD"),
])
def test_the_currency_preference_is_closed(tmp_path: Path, raw: object, expected: str) -> None:
    if raw is not None:
        (tmp_path / "config.json").write_text(json.dumps({"currency": raw}), encoding="utf-8")
    assert read_prefs(tmp_path).currency == expected


def test_an_install_that_keeps_usd_never_contacts_the_ecb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("no download while the currency is USD")

    monkeypatch.setattr(catalog_module.nethttp, "fetch_bounded", refuse)
    store, config = _setup(tmp_path, None)
    assert catalog_module.pull_fx_rates(store, config, NOW) == "skipped_unconfigured"
    # Skipping claimed nothing: choosing a currency downloads straight away.
    (config.data_dir / "config.json").write_text(json.dumps({"currency": "EUR"}))
    monkeypatch.setattr(catalog_module.nethttp, "fetch_bounded", lambda *_a, **_k: ECB)
    assert catalog_module.pull_fx_rates(store, config, NOW) == "pulled"


def test_chosen_currency_downloads_caches_and_paces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fetch(url: str, max_bytes: int, timeout_s: float, **_kwargs: object) -> bytes:
        calls.append(url)
        assert max_bytes == fx.MAX_FX_BYTES
        return ECB

    monkeypatch.setattr(catalog_module.nethttp, "fetch_bounded", fetch)
    store, config = _setup(tmp_path, "GBP")
    assert catalog_module.pull_fx_rates(store, config, NOW) == "pulled"
    assert calls == [fx.FX_SOURCE_URL]
    cached = fx.load_fx(config.data_dir)
    assert cached is not None and cached.date == "2026-09-29"
    assert catalog_module.pull_fx_rates(store, config, NOW + timedelta(hours=1)) == (
        "skipped_recently")
    later = NOW + timedelta(seconds=fx.FX_PULL_INTERVAL_S + 1)
    assert catalog_module.pull_fx_rates(store, config, later) == "pulled"


def test_managed_installs_skip_and_bad_answers_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_store, managed = _setup(tmp_path / "managed", "EUR", "https://pg.example.test")
    assert catalog_module.pull_fx_rates(managed_store, managed, NOW) == (
        "skipped_enterprise_configured")
    store, config = _setup(tmp_path / "bad", "EUR")
    monkeypatch.setattr(catalog_module.nethttp, "fetch_bounded", lambda *_a, **_k: b"<html/>")
    assert catalog_module.pull_fx_rates(store, config, NOW) == "invalid_artifact"
    assert fx.load_fx(config.data_dir) is None

    def blocked(*_a: object, **_k: object) -> bytes:
        raise urllib.error.HTTPError(fx.FX_SOURCE_URL, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(catalog_module.nethttp, "fetch_bounded", blocked)
    later = NOW + timedelta(seconds=fx.FX_PULL_INTERVAL_S + 1)
    assert catalog_module.pull_fx_rates(store, config, later) == "http_error"


def test_cached_rates_survive_only_in_their_exact_shape(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    doc = fx.rates_to_json(rates())
    (catalog / fx.FX_FILE_NAME).write_text(json.dumps(doc), encoding="utf-8")
    assert fx.load_fx(tmp_path) == rates()
    broken_docs = (
        {**doc, "extra": 1}, {**doc, "source": "someone"}, {**doc, "rates": {"GBP": "1"}},
    )
    for broken in broken_docs:
        (catalog / fx.FX_FILE_NAME).write_text(json.dumps(broken), encoding="utf-8")
        assert fx.load_fx(tmp_path) is None
