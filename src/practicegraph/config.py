"""Connection/configuration resolution (FR-CFG-1, FR-CFG-2).

Resolution order per field: environment variable > config file > built-in
default. The config file lives *inside* the data directory (``config.json``)
so the per-machine service and per-user shell read the same file. The data
directory itself can only come from env or default (the file lives in it).

The org token is a credential (FR-CFG-2): this module never exposes its value,
only a presence flag. Nothing here logs or echoes field values.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ENV_DATA_DIR = "PRACTICEGRAPH_DATA_DIR"
ENV_API_BASE_URL = "PRACTICEGRAPH_API_BASE_URL"
ENV_ORG_ID = "PRACTICEGRAPH_ORG_ID"
ENV_ORG_TOKEN = "PRACTICEGRAPH_ORG_TOKEN"
ENV_SKILLS_SOURCE_URL = "PRACTICEGRAPH_SKILLS_URL"
ENV_NEWS_SOURCE_URL = "PRACTICEGRAPH_NEWS_URL"
ENV_MODELS_SOURCE_URL = "PRACTICEGRAPH_MODELS_URL"
ENV_ADVISOR_SOURCE_URL = "PRACTICEGRAPH_ADVISOR_URL"
ENV_RATECARD_SOURCE_URL = "PRACTICEGRAPH_RATECARD_URL"
ENV_LICENSE_SOURCE_URL = "PRACTICEGRAPH_LICENSE_URL"
ENV_UPDATE_SOURCE_URL = "PRACTICEGRAPH_UPDATE_URL"
ENV_DOCS_SOURCE_URL = "PRACTICEGRAPH_DOCS_URL"
ENV_MODEL_CATALOG_SOURCE_URL = "PRACTICEGRAPH_MODEL_CATALOG_URL"
ENV_BUILD_IDEAS_SOURCE_URL = "PRACTICEGRAPH_BUILD_IDEAS_URL"
ENV_COMMUNITY_SOURCE_URL = "PRACTICEGRAPH_COMMUNITY_URL"
ENV_CONTENT_BASE_URL = "PRACTICEGRAPH_CONTENT_BASE_URL"
ENV_PLAYBOOKS_SOURCE_URL = "PRACTICEGRAPH_HARNESS_PLAYBOOKS_URL"
ENV_TOKEN_PRICES_SOURCE_URL = "PRACTICEGRAPH_TOKEN_PRICES_URL"
ENV_TRAINING_SOURCE_URL = "PRACTICEGRAPH_TRAINING_URL"
DEFAULT_CONTENT_BASE_URL = "https://practicegraph-dev.vercel.app"
DEFAULT_PLAYBOOKS_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/harness-playbooks.json"
DEFAULT_TOKEN_PRICES_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/token-prices.json"
DEFAULT_TRAINING_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/training.json"
DEFAULT_COMMUNITY_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/community.json"
DEFAULT_NEWS_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/news.json"
DEFAULT_BUILD_IDEAS_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/build-ideas.json"
# Models and advisor moved to the SERVING host on 2026-08-21: they pointed at
# the parked apex, so neither artifact had ever arrived on any install — the
# exact silent-never-refreshes failure the rate-card note below describes.
# Swap all of these to the apex together once it is pointed.
DEFAULT_MODELS_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/models.json"
DEFAULT_ADVISOR_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/advisor.json"
# The rate card is served, not just bundled: model prices change between
# releases (the bundled card went stale six days after publication), and a
# stale card mis-prices silently. Publishing beats shipping a client.
#
# This points at the SAME host as the news artifact rather than the apex,
# because that is the host that actually serves today: the apex is registered
# but parked for the design phase, and on 2026-07-25 it answered nothing while
# the Vercel deployment answered news.json. A default that resolves nowhere
# means the card silently never refreshes, which is the exact failure this
# feature exists to prevent. Swap both to the apex together once it is pointed.
DEFAULT_RATECARD_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/rate-card.json"
# License activation/renewal signer (LICENSING_PLAN §9): the one endpoint the
# app talks to at activation + daily renewal; offline in between.
DEFAULT_LICENSE_SOURCE_URL = "https://practicegraph.dev"
# Release manifest. GitHub serves /releases/latest/download/<asset> as a
# stable redirect to the newest release, so the client needs no API token
# and no rate-limited API call. Overriding this URL buys an attacker
# nothing: the manifest is Ed25519-verified against a key compiled into
# the build (analysis/update.py), and the asset host is pinned there too.
DEFAULT_UPDATE_SOURCE_URL = (
    "https://github.com/TeamLandiLTD/practicegraph-app/releases/latest/download/update.json"
)
DEFAULT_SKILLS_SOURCE_URL = (
    "https://raw.githubusercontent.com/TeamLandiLTD/skill-registry/main/skills.json"
)
# The documentation shelf: same serving host as news and the rate card (the
# apex is still parked — see the rate-card note above; swap together).
DEFAULT_DOCS_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/docs.json"
# The model catalog (available models + effort ladders): same host, same
# swap-together rule.
DEFAULT_MODEL_CATALOG_SOURCE_URL = f"{DEFAULT_CONTENT_BASE_URL}/model-catalog.json"
# Test seam: lets the registry-bootstrap import run against HKCU.
ENV_BOOTSTRAP_HIVE = "PRACTICEGRAPH_BOOTSTRAP_HIVE"

CONFIG_FILE_NAME = "config.json"
ORG_TOKEN_FILE_NAME = "org_token.bin"
BOOTSTRAP_REG_PATH = r"SOFTWARE\PracticeGraph\Bootstrap"

# Closed labels — these appear in doctor output, so they must stay enumerable.
FieldSource = Literal["env", "protected", "file", "default"]
ConfigFileState = Literal["absent", "ok", "invalid"]


@dataclass(frozen=True, slots=True)
class Config:
    data_dir: Path
    data_dir_source: FieldSource
    api_base_url: str | None
    api_base_url_source: FieldSource
    org_id: str | None
    org_id_source: FieldSource
    org_token_present: bool
    org_token_source: FieldSource
    config_file_state: ConfigFileState
    # Independent-mode skill source: the built-in TeamLandi public registry by
    # default, overridden by environment/config. A same-day enterprise catalog
    # remains authoritative. Every source uses the same artifact validation.
    skills_source_url: str | None = None
    skills_source_url_source: FieldSource = "default"
    news_source_url: str = DEFAULT_NEWS_SOURCE_URL
    news_source_url_source: FieldSource = "default"
    community_source_url: str = DEFAULT_COMMUNITY_SOURCE_URL
    community_source_url_source: FieldSource = "default"
    build_ideas_source_url: str = DEFAULT_BUILD_IDEAS_SOURCE_URL
    build_ideas_source_url_source: FieldSource = "default"
    models_source_url: str = DEFAULT_MODELS_SOURCE_URL
    models_source_url_source: FieldSource = "default"
    advisor_source_url: str = DEFAULT_ADVISOR_SOURCE_URL
    advisor_source_url_source: FieldSource = "default"
    ratecard_source_url: str = DEFAULT_RATECARD_SOURCE_URL
    ratecard_source_url_source: FieldSource = "default"
    license_source_url: str = DEFAULT_LICENSE_SOURCE_URL
    license_source_url_source: FieldSource = "default"
    update_source_url: str = DEFAULT_UPDATE_SOURCE_URL
    update_source_url_source: FieldSource = "default"
    docs_source_url: str = DEFAULT_DOCS_SOURCE_URL
    docs_source_url_source: FieldSource = "default"
    model_catalog_source_url: str = DEFAULT_MODEL_CATALOG_SOURCE_URL
    model_catalog_source_url_source: FieldSource = "default"
    playbooks_source_url: str = DEFAULT_PLAYBOOKS_SOURCE_URL
    playbooks_source_url_source: FieldSource = "default"
    token_prices_source_url: str = DEFAULT_TOKEN_PRICES_SOURCE_URL
    token_prices_source_url_source: FieldSource = "default"
    training_source_url: str = DEFAULT_TRAINING_SOURCE_URL
    training_source_url_source: FieldSource = "default"


def resolve_models_url(config: Config) -> str:
    """Return the configured standalone model-guidance artifact URL."""
    return config.models_source_url


def default_data_dir(env: dict[str, str]) -> Path:
    """Platform-conventional default data directory (FR-SRC-2 conventions)."""
    if os.name == "nt":
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "PracticeGraph"
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "practicegraph"


def _read_config_file(path: Path) -> tuple[dict[str, Any], ConfigFileState]:
    if not path.is_file():
        return {}, "absent"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "invalid"
    if not isinstance(raw, dict):
        return {}, "invalid"
    return raw, "ok"


def read_config_object(data_dir: Path, key: str) -> dict[str, object]:
    """Return one object-valued local config entry, failing closed to empty."""
    values, _state = _read_config_file(data_dir / CONFIG_FILE_NAME)
    value = values.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _resolve_field(
    env: dict[str, str], env_key: str, file_values: dict[str, Any], file_key: str
) -> tuple[str | None, FieldSource]:
    env_value = env.get(env_key)
    if env_value:
        return env_value, "env"
    file_value = file_values.get(file_key)
    if isinstance(file_value, str) and file_value:
        return file_value, "file"
    return None, "default"


DEFAULT_ALERT_TOGGLES: dict[str, bool] = {
    "daily_report_ready": False,
    "spend_pace": True,
    "source_health": True,
    "focus_break": True,
}
DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "07:00"
# Daily reflection restyle (report/restyle.py): OFF by default. When set to a
# known provider key, the local CLI of the user's own agent rewrites the
# recognition band's wording once a day (facts and numbers are never touched
# — a rewrite that moves a statistic is rejected). "off"/unknown -> templates.
REFLECTION_STYLE_PROVIDERS: tuple[str, ...] = ("claude", "codex")
DEFAULT_REFLECTION_STYLE = "off"
# Billing mode (economy reading, AM-1 dual denomination): how this person pays
# for model use. "subscription" leads the economy surfaces with window share
# and labels every dollar figure API-equivalent (list-price equivalence is
# never presented as actual spend); "api" leads with dollars; "unknown" keeps
# today's estimate framing. Confirmed once via the app; unrecognised values
# degrade to "unknown", never an error.
BILLING_MODES: tuple[str, ...] = ("unknown", "subscription", "api", "mixed")
DEFAULT_BILLING_MODE = "unknown"
BILLING_TOOLS: tuple[str, ...] = ("claude_code", "codex")
# The two audiences the page can speak to (owner naming, 2026-08-22).
PROFILES: tuple[str, ...] = ("coding", "productivity")
DEFAULT_PROFILE = "coding"
CAPABILITY_PATHS: tuple[str, ...] = ("knowledge", "software")


@dataclass(frozen=True, slots=True)
class Prefs:
    """User preferences from config.json (all optional, safe defaults):
    the focus master switch (FR-FOC-5), monthly budget (FR-ANL-6), per-category
    alert toggles and quiet hours (FR-ALR-2)."""

    focus_coaching: bool
    monthly_budget_micro_usd: int | None
    alerts_enabled: dict[str, bool]
    quiet_start: str
    quiet_end: str
    # "off" (default) or a REFLECTION_STYLE_PROVIDERS key: the local agent CLI
    # that restyles the reflection band's wording daily (facts stay fixed).
    reflection_style: str = DEFAULT_REFLECTION_STYLE
    # BILLING_MODES value: the economy reading's denomination lens (AM-1).
    billing_mode: str = DEFAULT_BILLING_MODE
    # PROFILES value: which audience the page speaks to. "coding" is
    # today's page; "productivity" re-reads the same stats for someone
    # whose work is documents, reports and analysis (PRODUCTIVITY_PROFILE
    # plan, owner-named 2026-08-22). A profile never changes what is
    # collected — only what is said and which served content lane loads.
    profile: str = DEFAULT_PROFILE
    capability_paths: tuple[str, ...] = CAPABILITY_PATHS
    capability_paths_confirmed: bool = False
    billing_by_tool: dict[str, str] = field(default_factory=dict)


def read_prefs(data_dir: Path) -> Prefs:
    values, _state = _read_config_file(data_dir / CONFIG_FILE_NAME)
    focus = values.get("focus_coaching")
    budget = values.get("monthly_budget_usd")
    alerts_raw = values.get("alerts")
    alerts = dict(DEFAULT_ALERT_TOGGLES)
    if isinstance(alerts_raw, dict):
        for key in DEFAULT_ALERT_TOGGLES:
            if isinstance(alerts_raw.get(key), bool):
                alerts[key] = alerts_raw[key]
    quiet_raw = values.get("quiet_hours")
    quiet_start, quiet_end = DEFAULT_QUIET_START, DEFAULT_QUIET_END
    if isinstance(quiet_raw, dict):
        if isinstance(quiet_raw.get("start"), str):
            quiet_start = quiet_raw["start"]
        if isinstance(quiet_raw.get("end"), str):
            quiet_end = quiet_raw["end"]
    style_raw = values.get("reflection_style")
    reflection_style = (
        style_raw
        if isinstance(style_raw, str) and style_raw in REFLECTION_STYLE_PROVIDERS
        else DEFAULT_REFLECTION_STYLE
    )
    billing_raw = values.get("billing_mode")
    billing_mode = (
        billing_raw
        if isinstance(billing_raw, str) and billing_raw in BILLING_MODES
        else DEFAULT_BILLING_MODE
    )
    profile_raw = values.get("profile")
    profile = (
        profile_raw if isinstance(profile_raw, str) and profile_raw in PROFILES else DEFAULT_PROFILE
    )
    paths_raw = values.get("capability_paths")
    paths_valid = (
        isinstance(paths_raw, list)
        and bool(paths_raw)
        and all(isinstance(path, str) and path in CAPABILITY_PATHS for path in paths_raw)
    )
    if paths_valid:
        assert isinstance(paths_raw, list)
        paths = tuple(path for path in CAPABILITY_PATHS if path in paths_raw)
    else:
        paths = CAPABILITY_PATHS
    paths_confirmed = paths_valid and values.get("capability_paths_confirmed") is True
    return Prefs(
        focus_coaching=focus if isinstance(focus, bool) else True,
        monthly_budget_micro_usd=(
            budget * 1_000_000 if isinstance(budget, int) and budget > 0 else None
        ),
        alerts_enabled=alerts,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        reflection_style=reflection_style,
        billing_mode=billing_mode,
        profile=profile,
        capability_paths=paths,
        capability_paths_confirmed=paths_confirmed,
        billing_by_tool={
            tool: mode for tool, mode in read_config_object(data_dir, "billing_by_tool").items()
            if tool in BILLING_TOOLS and isinstance(mode, str) and mode in BILLING_MODES
        },
    )


def store_org_token(data_dir: Path, token: str) -> None:
    """Persist the org token in the protected store: DPAPI machine scope on
    Windows, permission-restricted file elsewhere (NFR-SEC-1)."""
    from practicegraph import winsec
    from practicegraph.secureio import atomic_write, private_directory

    private_directory(data_dir)
    path = data_dir / ORG_TOKEN_FILE_NAME
    atomic_write(path, winsec.protect(token.encode("utf-8")))
    # Machine-scope DPAPI stops off-machine reads, but ANY local process can
    # decrypt the blob — so in a shared data dir (%ProgramData%) the file must
    # also be permission-locked to its owner, or a second local user just reads
    # and decrypts it (NFR-SEC-1). Windows: a protected DACL; POSIX: 0600.
    if os.name == "nt":
        winsec.restrict_to_owner_and_admins(path)
    else:
        path.chmod(0o600)


def load_org_token(data_dir: Path) -> str | None:
    from practicegraph import winsec

    path = data_dir / ORG_TOKEN_FILE_NAME
    if not path.is_file():
        return None
    try:
        return winsec.unprotect(path.read_bytes()).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def resolve_org_token(env: dict[str, str], config: Config) -> str:
    """Resolve the org token value at the moment of use, from the same env the
    Config came from (env > protected store > file). Never stored on Config
    (FR-CFG-2)."""
    env_value = env.get(ENV_ORG_TOKEN)
    if env_value:
        return env_value
    protected = load_org_token(config.data_dir)
    if protected:
        return protected
    file_values, _ = _read_config_file(config.data_dir / CONFIG_FILE_NAME)
    token = file_values.get("org_token")
    return token if isinstance(token, str) else ""


def write_config_updates(data_dir: Path, updates: Mapping[str, object]) -> None:
    """Merge settings into config.json — empty values are omitted and existing
    values are never blanked (FR-DEP-2 semantics). Booleans are written as-is
    (preference switches like focus_coaching)."""
    from practicegraph.secureio import atomic_write, private_directory

    private_directory(data_dir)
    path = data_dir / CONFIG_FILE_NAME
    existing, _ = _read_config_file(path)
    for key, value in updates.items():
        if value is None or value == "":
            continue
        existing[key] = value
    atomic_write(path, (json.dumps(existing, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def save_capability_paths(data_dir: Path, requested: tuple[str, ...]) -> Prefs:
    if not requested or any(path not in CAPABILITY_PATHS for path in requested):
        raise ValueError("invalid capability paths")
    canonical = [path for path in CAPABILITY_PATHS if path in requested]
    write_config_updates(
        data_dir,
        {
            "capability_paths": canonical,
            "capability_paths_confirmed": True,
        },
    )
    return read_prefs(data_dir)


def import_bootstrap(env: dict[str, str], data_dir: Path) -> bool:
    """Consume MSI-seeded provisioning values from the registry (FR-DEP-2):
    api_base_url/org_id merge into config.json, the token moves into the
    protected store and its registry value is deleted. Windows-only; the
    bootstrap key is ACL'd to SYSTEM/Administrators by the installer."""
    if os.name != "nt":
        return False
    import winreg

    hive = (
        winreg.HKEY_CURRENT_USER
        if env.get(ENV_BOOTSTRAP_HIVE) == "HKCU"
        else winreg.HKEY_LOCAL_MACHINE
    )
    try:
        key = winreg.OpenKey(hive, BOOTSTRAP_REG_PATH, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
    except OSError:
        return False

    def _read(name: str) -> str | None:
        try:
            value, _kind = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return value if isinstance(value, str) and value else None

    imported = False
    with key:
        updates = {}
        api_base_url = _read("api_base_url")
        org_id = _read("org_id")
        if api_base_url:
            updates["api_base_url"] = api_base_url
        if org_id:
            updates["org_id"] = org_id
        if updates:
            write_config_updates(data_dir, updates)
            imported = True
        token = _read("org_token")
        if token:
            store_org_token(data_dir, token)
            # Failure to delete is tolerable: the key is admin-only readable.
            with contextlib.suppress(OSError):
                winreg.DeleteValue(key, "org_token")
            imported = True
    return imported


def resolve(env: dict[str, str]) -> Config:
    """Resolve connection settings. Fail-open: an invalid config file degrades
    to defaults and is reported via ``config_file_state`` (NFR-REL-1)."""
    env_data_dir = env.get(ENV_DATA_DIR)
    data_dir_source: FieldSource
    if env_data_dir:
        data_dir, data_dir_source = Path(env_data_dir), "env"
    else:
        data_dir, data_dir_source = default_data_dir(env), "default"

    file_values, file_state = _read_config_file(data_dir / CONFIG_FILE_NAME)

    api_base_url, api_base_url_source = _resolve_field(
        env, ENV_API_BASE_URL, file_values, "api_base_url"
    )
    org_id, org_id_source = _resolve_field(env, ENV_ORG_ID, file_values, "org_id")
    skills_source_url, skills_source_url_source = _resolve_field(
        env, ENV_SKILLS_SOURCE_URL, file_values, "skills_source_url"
    )
    news_source_url, news_source_url_source = _resolve_field(
        env, ENV_NEWS_SOURCE_URL, file_values, "news_source_url"
    )
    community_source_url, community_source_url_source = _resolve_field(
        env, ENV_COMMUNITY_SOURCE_URL, file_values, "community_source_url"
    )
    build_ideas_source_url, build_ideas_source_url_source = _resolve_field(
        env, ENV_BUILD_IDEAS_SOURCE_URL, file_values, "build_ideas_source_url"
    )
    models_source_url, models_source_url_source = _resolve_field(
        env, ENV_MODELS_SOURCE_URL, file_values, "models_source_url"
    )
    advisor_source_url, advisor_source_url_source = _resolve_field(
        env, ENV_ADVISOR_SOURCE_URL, file_values, "advisor_source_url"
    )
    ratecard_source_url, ratecard_source_url_source = _resolve_field(
        env, ENV_RATECARD_SOURCE_URL, file_values, "ratecard_source_url"
    )
    update_source_url, update_source_url_source = _resolve_field(
        env, ENV_UPDATE_SOURCE_URL, file_values, "update_source_url"
    )
    license_source_url, license_source_url_source = _resolve_field(
        env, ENV_LICENSE_SOURCE_URL, file_values, "license_source_url"
    )
    docs_source_url, docs_source_url_source = _resolve_field(
        env, ENV_DOCS_SOURCE_URL, file_values, "docs_source_url"
    )
    model_catalog_source_url, model_catalog_source_url_source = _resolve_field(
        env, ENV_MODEL_CATALOG_SOURCE_URL, file_values, "model_catalog_source_url"
    )
    # Per-channel overrides win over a single catalog-host override. An
    # explicitly chosen base also counts as explicit intent in enterprise mode.
    content_base, content_source = _resolve_field(
        env,
        ENV_CONTENT_BASE_URL,
        file_values,
        "content_base_url",
    )
    playbooks_source_url, playbooks_source_url_source = _resolve_field(
        env, ENV_PLAYBOOKS_SOURCE_URL, file_values, "harness_playbooks_url",
    )
    token_prices_source_url, token_prices_source_url_source = _resolve_field(
        env, ENV_TOKEN_PRICES_SOURCE_URL, file_values, "token_prices_url",
    )
    training_source_url, training_source_url_source = _resolve_field(
        env, ENV_TRAINING_SOURCE_URL, file_values, "training_url",
    )
    if content_base:
        content_base = content_base.rstrip("/")
        if token_prices_source_url is None:
            token_prices_source_url = content_base + "/token-prices.json"
            token_prices_source_url_source = content_source
        if training_source_url is None:
            training_source_url = content_base + "/training.json"
            training_source_url_source = content_source
        if playbooks_source_url is None:
            playbooks_source_url = content_base + "/harness-playbooks.json"
            playbooks_source_url_source = content_source
        if news_source_url is None:
            news_source_url = content_base + "/news.json"
            news_source_url_source = content_source
        if community_source_url is None:
            community_source_url = content_base + "/community.json"
            community_source_url_source = content_source
        if build_ideas_source_url is None:
            build_ideas_source_url = content_base + "/build-ideas.json"
            build_ideas_source_url_source = content_source
        if models_source_url is None:
            models_source_url = content_base + "/models.json"
            models_source_url_source = content_source
        if advisor_source_url is None:
            advisor_source_url = content_base + "/advisor.json"
            advisor_source_url_source = content_source
        if ratecard_source_url is None:
            ratecard_source_url = content_base + "/rate-card.json"
            ratecard_source_url_source = content_source
        if docs_source_url is None:
            docs_source_url = content_base + "/docs.json"
            docs_source_url_source = content_source
        if model_catalog_source_url is None:
            model_catalog_source_url = content_base + "/model-catalog.json"
            model_catalog_source_url_source = content_source
        if skills_source_url is None:
            skills_source_url = content_base + "/skills.json"
            skills_source_url_source = content_source
    if skills_source_url is None:
        skills_source_url = DEFAULT_SKILLS_SOURCE_URL

    # Token resolution adds the protected store between env and file
    # (FR-CFG-2: presence and source only, never the value).
    org_token_source: FieldSource
    if env.get(ENV_ORG_TOKEN):
        org_token_present, org_token_source = True, "env"
    elif (data_dir / ORG_TOKEN_FILE_NAME).is_file():
        org_token_present, org_token_source = True, "protected"
    else:
        file_token = file_values.get("org_token")
        org_token_present = isinstance(file_token, str) and bool(file_token)
        org_token_source = "file" if org_token_present else "default"

    return Config(
        data_dir=data_dir,
        data_dir_source=data_dir_source,
        api_base_url=api_base_url,
        api_base_url_source=api_base_url_source,
        org_id=org_id,
        org_id_source=org_id_source,
        org_token_present=org_token_present,
        org_token_source=org_token_source,
        config_file_state=file_state,
        skills_source_url=skills_source_url,
        skills_source_url_source=skills_source_url_source,
        news_source_url=news_source_url or DEFAULT_NEWS_SOURCE_URL,
        news_source_url_source=news_source_url_source,
        community_source_url=community_source_url or DEFAULT_COMMUNITY_SOURCE_URL,
        community_source_url_source=community_source_url_source,
        build_ideas_source_url=(build_ideas_source_url or DEFAULT_BUILD_IDEAS_SOURCE_URL),
        build_ideas_source_url_source=build_ideas_source_url_source,
        models_source_url=models_source_url or DEFAULT_MODELS_SOURCE_URL,
        models_source_url_source=models_source_url_source,
        advisor_source_url=advisor_source_url or DEFAULT_ADVISOR_SOURCE_URL,
        advisor_source_url_source=advisor_source_url_source,
        ratecard_source_url=ratecard_source_url or DEFAULT_RATECARD_SOURCE_URL,
        update_source_url=update_source_url or DEFAULT_UPDATE_SOURCE_URL,
        update_source_url_source=update_source_url_source,
        ratecard_source_url_source=ratecard_source_url_source,
        license_source_url=license_source_url or DEFAULT_LICENSE_SOURCE_URL,
        license_source_url_source=license_source_url_source,
        docs_source_url=docs_source_url or DEFAULT_DOCS_SOURCE_URL,
        docs_source_url_source=docs_source_url_source,
        model_catalog_source_url=(model_catalog_source_url or DEFAULT_MODEL_CATALOG_SOURCE_URL),
        model_catalog_source_url_source=model_catalog_source_url_source,
        playbooks_source_url=playbooks_source_url or DEFAULT_PLAYBOOKS_SOURCE_URL,
        playbooks_source_url_source=playbooks_source_url_source,
        token_prices_source_url=token_prices_source_url or DEFAULT_TOKEN_PRICES_SOURCE_URL,
        token_prices_source_url_source=token_prices_source_url_source,
        training_source_url=training_source_url or DEFAULT_TRAINING_SOURCE_URL,
        training_source_url_source=training_source_url_source,
    )
