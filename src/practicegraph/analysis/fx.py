"""Display currency: show amounts in the person's currency, never store them.

Money stays integer micro-USD everywhere it is computed, stored and reported
(INV-6). A chosen display currency changes only what the page shows: the
dashboard converts numbers with the factor published in the view, and the
engine converts the dollar amounts inside its own sentences as the view is
sent. The CLI and file reports stay in USD.

Rates are the European Central Bank's daily euro reference rates — official,
free, keyless, about 1.5 KB. They are downloaded only once someone chooses a
currency other than USD, so an install that keeps USD never contacts the ECB.
The file is parsed strictly (closed shape, no DTDs, bounded size); anything
unexpected is refused and the page keeps showing USD.

Quoted editorial content (news, community findings, build ideas, guides,
curated skills and playbooks) is shown as written: a price inside someone
else's headline is part of the quote.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

FX_SOURCE_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FX_SOURCE_NAME = "European Central Bank"
FX_FILE_NAME = "fx-rates.json"
# The ECB publishes once per business day; a few attempts a day catch it.
FX_PULL_INTERVAL_S = 3 * 60 * 60
MAX_FX_BYTES = 65_536

# code -> (prefix, fraction digits, name), matching the dashboard's en-US
# Intl currency formatting for every ECB currency plus the euro. The view
# carries prefix and digits so the page and the engine format identically.
CURRENCIES: dict[str, tuple[str, int, str]] = {
    "USD": ("$", 2, "US Dollar"),
    "EUR": ("€", 2, "Euro"),
    "AUD": ("A$", 2, "Australian Dollar"),
    "BRL": ("R$", 2, "Brazilian Real"),
    "CAD": ("CA$", 2, "Canadian Dollar"),
    "CHF": ("CHF\u00a0", 2, "Swiss Franc"),
    "CNY": ("CN¥", 2, "Chinese Yuan"),
    "CZK": ("CZK\u00a0", 2, "Czech Koruna"),
    "DKK": ("DKK\u00a0", 2, "Danish Krone"),
    "GBP": ("£", 2, "British Pound"),
    "HKD": ("HK$", 2, "Hong Kong Dollar"),
    "HUF": ("HUF\u00a0", 0, "Hungarian Forint"),
    "IDR": ("IDR\u00a0", 0, "Indonesian Rupiah"),
    "ILS": ("₪", 2, "Israeli New Shekel"),
    "INR": ("₹", 2, "Indian Rupee"),
    "ISK": ("ISK\u00a0", 0, "Icelandic Króna"),
    "JPY": ("¥", 0, "Japanese Yen"),
    "KRW": ("₩", 0, "South Korean Won"),
    "MXN": ("MX$", 2, "Mexican Peso"),
    "MYR": ("MYR\u00a0", 2, "Malaysian Ringgit"),
    "NOK": ("NOK\u00a0", 2, "Norwegian Krone"),
    "NZD": ("NZ$", 2, "New Zealand Dollar"),
    "PHP": ("₱", 2, "Philippine Peso"),
    "PLN": ("PLN\u00a0", 2, "Polish Zloty"),
    "RON": ("RON\u00a0", 2, "Romanian Leu"),
    "SEK": ("SEK\u00a0", 2, "Swedish Krona"),
    "SGD": ("SGD\u00a0", 2, "Singapore Dollar"),
    "THB": ("THB\u00a0", 2, "Thai Baht"),
    "TRY": ("TRY\u00a0", 2, "Turkish Lira"),
    "ZAR": ("ZAR\u00a0", 2, "South African Rand"),
}

# Top-level view sections that quote other people's words.
EDITORIAL_SECTIONS = frozenset({
    "news", "news_days", "community", "build_ideas", "build_ideas_days", "build_repos",
    "docs", "skills", "playbook",
})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^[A-Z]{3}$")
_RATE_RE = re.compile(r"^\d{1,7}(\.\d{1,8})?$")
# "$1,128", "$444.10", "$40.51" — a dollar sign followed by a number. A shell
# variable ("$HOME"), a command ("$(id -u)") or a lone "$" is not money.
_MONEY_RE = re.compile(r"\$(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")


@dataclass(frozen=True)
class FxRates:
    """One ECB publication: units of each currency per euro (EUR itself is 1)."""

    date: str
    per_eur: dict[str, Decimal]


def _rate(text: object) -> Decimal | None:
    if not isinstance(text, str) or not _RATE_RE.match(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _checked(date: object, pairs: list[tuple[object, object]]) -> FxRates | None:
    if not isinstance(date, str) or not _DATE_RE.match(date) or not 1 <= len(pairs) <= 60:
        return None
    per_eur: dict[str, Decimal] = {"EUR": Decimal(1)}
    for code, text in pairs:
        rate = _rate(text)
        if not isinstance(code, str) or not _CODE_RE.match(code) or code in per_eur or rate is None:
            return None
        per_eur[code] = rate
    return FxRates(date, per_eur) if "USD" in per_eur else None


def parse_ecb_daily(raw: bytes) -> FxRates | None:
    """Strictly parse eurofxref-daily.xml; None on any deviation."""
    # The ECB file is ASCII-only UTF-8. A NUL byte means a UTF-16 (or other
    # wide) encoding, under which the byte-level DOCTYPE/ENTITY checks below
    # would not fire even though expat would still honour the declaration.
    if not raw or len(raw) > MAX_FX_BYTES or b"\x00" in raw:
        return None
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    dated = [e for e in root.iter() if e.tag.endswith("Cube") and "time" in e.attrib]
    if len(dated) != 1:
        return None
    pairs: list[tuple[object, object]] = [
        (e.get("currency"), e.get("rate")) for e in dated[0] if e.tag.endswith("Cube")
    ]
    return _checked(dated[0].get("time"), pairs)


def rates_to_json(rates: FxRates) -> dict[str, object]:
    return {
        "source": FX_SOURCE_NAME,
        "date": rates.date,
        "rates": {code: str(rate) for code, rate in rates.per_eur.items() if code != "EUR"},
    }


def rates_from_json(doc: object) -> FxRates | None:
    if not isinstance(doc, dict) or set(doc) != {"source", "date", "rates"}:
        return None
    raw_rates = doc["rates"]
    if doc["source"] != FX_SOURCE_NAME or not isinstance(raw_rates, dict):
        return None
    return _checked(doc["date"], list(raw_rates.items()))


def load_fx(data_dir: Path) -> FxRates | None:
    try:
        path = data_dir / "catalog" / FX_FILE_NAME
        return rates_from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def usd_factor(rates: FxRates | None, code: str) -> Decimal | None:
    """Units of `code` per US dollar, through the euro; None when unknown."""
    if code == "USD":
        return Decimal(1)
    if rates is None or code not in rates.per_eur:
        return None
    return rates.per_eur[code] / rates.per_eur["USD"]


def format_money(amount: Decimal, code: str, digits: int | None = None) -> str:
    prefix, currency_digits, _name = CURRENCIES[code]
    places = currency_digits if digits is None else min(digits, currency_digits)
    rounded = amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{prefix}{rounded:,.{places}f}"


def convert_money_text(text: str, factor: Decimal, code: str) -> str:
    """Rewrite each dollar amount in `text`, keeping its precision: an amount
    written with cents keeps the currency's decimals, a whole one stays whole."""

    def swap(match: re.Match[str]) -> str:
        whole, cents = match.group(1), match.group(2)
        amount = Decimal(whole.replace(",", "") + (cents or "")) * factor
        return format_money(amount, code, None if cents else 0)

    return _MONEY_RE.sub(swap, text)


def _plain(value: Decimal) -> str:
    return format(value.quantize(Decimal("1e-10")).normalize(), "f")


def display_currency(requested: str, rates: FxRates | None) -> dict[str, Any]:
    """What the page shows: the requested currency when its rate is known,
    otherwise USD with `missing` set so the page can say why."""
    wanted = requested if requested in CURRENCIES else "USD"
    factor = usd_factor(rates, wanted)
    code = wanted if factor is not None else "USD"
    prefix, digits, _name = CURRENCIES[code]
    order = ["USD", "EUR", *sorted(c for c in CURRENCIES if c not in ("USD", "EUR"))]
    return {
        "code": code,
        "requested": wanted,
        "missing": code != wanted,
        "factor": _plain(factor) if factor is not None else "1",
        "prefix": prefix,
        "digits": digits,
        "date": rates.date if rates is not None else None,
        "source": FX_SOURCE_NAME,
        "source_url": FX_SOURCE_URL,
        "available": [{"code": c, "name": CURRENCIES[c][2]} for c in order],
    }


def _convert(node: Any, factor: Decimal, code: str) -> Any:
    if isinstance(node, str):
        return convert_money_text(node, factor, code) if "$" in node else node
    if isinstance(node, list):
        return [_convert(item, factor, code) for item in node]
    if isinstance(node, dict):
        return {key: _convert(value, factor, code) for key, value in node.items()}
    return node


def apply_display_currency(
    view: dict[str, Any], requested: str, rates: FxRates | None,
) -> dict[str, Any]:
    """The view as sent to the page: the currency block added and, for a
    currency other than USD, dollar amounts in the engine's own sentences
    converted. Numbers (micro-USD fields) are left for the page to convert."""
    block = display_currency(requested, rates)
    if block["code"] == "USD":
        return {**view, "currency": block}
    factor = usd_factor(rates, block["code"])
    assert factor is not None
    shown = {
        key: value if key in EDITORIAL_SECTIONS else _convert(value, factor, block["code"])
        for key, value in view.items()
    }
    shown["currency"] = block
    return shown
