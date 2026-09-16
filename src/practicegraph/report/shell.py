"""Multi-view HTML report shell (FR-RPT-1): Today, Insights, Sources, Skills,
Privacy Center — one self-contained file in the product design language
(mytokenindex design system: warm paper ground, teal signal, mono figures,
quiet suppressed states).

Script-free by construction (FR-RPT-2, NFR-UX-2): view switching is CSS-only
(`:target` panels with anchor-tab navigation — the accepted a11y limit), all
charts are inline SVG, every dynamic value is escaped, and static surfaces
never mutate state (FR-RPT-5).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from practicegraph import __version__
from practicegraph.analysis.advisor import (
    AdvisorBoard,
    advisor_audit,
    build_advisor_board,
    covered_findings,
)
from practicegraph.analysis.advisor_market import CitedMeasurement, safe_evidence_url
from practicegraph.analysis.advisor_receipts import (
    RECEIPTS_WINDOW_DAYS,
    gather_receipts,
)
from practicegraph.analysis.aggregate import DailySnapshot
from practicegraph.analysis.attention import quiet_hours_reading
from practicegraph.analysis.briefings import Briefing
from practicegraph.analysis.calibration import (
    CALIBRATION_COPY,
    CalibrationReading,
    compose_calibration,
)
from practicegraph.analysis.capability import CapabilityReading, compose_capability_reading
from practicegraph.analysis.community import CommunityItem
from practicegraph.analysis.conditioning import (
    ConditioningIndicator,
    compose_conditioning,
    reliance_daily_metrics,
)
from practicegraph.analysis.dayclose import is_day_closed
from practicegraph.analysis.docs import EMPTY_DOCS, DocsArtifact
from practicegraph.analysis.drain import DrainSurface, compose_drain
from practicegraph.analysis.economy import (
    ECONOMY_COPY,
    EconomyReading,
    compose_economy,
)
from practicegraph.analysis.focus import (
    APPROVAL_WAVED_MIN_COUNT,
    APPROVAL_WAVED_MIN_SHARE_PCT,
    REFIRE_MIN_COUNT,
    REFIRE_WINDOW_MIN,
    REFLEX_GAP_MAX_S,
    RHYTHM_WINDOW_DAYS,
    TIP_COPY,
    FocusMetrics,
    RhythmStats,
    block_counters,
    compute_metrics,
    interactive_marks,
    quiet_hours_drift,
    rhythm_stats,
    triggered_tips,
    visible_tips,
)
from practicegraph.analysis.harness_features import (
    HarnessFeatures,
    read_features,
)
from practicegraph.analysis.harness_inventory import (
    HarnessConnectors,
    HarnessSkills,
    ToolVersion,
    installed_versions,
    read_connectors,
    read_skills,
)
from practicegraph.analysis.insights import (
    APPROACHING_QUOTA_PCT,
    MATURITY_LABELS,
    WORK_TYPE_LABELS,
    Finding,
    MaturityLevel,
    Severity,
    SpendPace,
    Suggestion,
    derive_suggestions,
    detect,
    marathon_finding,
    maturity_signals,
    spend_pace,
    visible_suggestions,
    work_type_mix,
)
from practicegraph.analysis.install_health import (
    InstallNotice,
    compose_install_health,
    legacy_service_present,
)
from practicegraph.analysis.model_catalog import EMPTY_CATALOG, ModelCatalog
from practicegraph.analysis.model_intelligence import (
    ModelRecommendation,
    build_model_recommendations,
)
from practicegraph.analysis.news import NewsItem
from practicegraph.analysis.performance import (
    DIMENSION_LABELS,
    PERFORMANCE_WINDOW_DAYS,
    STRONG_MIN_SCORE,
    PerformanceProfile,
    WindowInputs,
    dimension_scores,
    gather_performance,
    level_for,
    weekly_windows,
)
from practicegraph.analysis.pin_model import recent_claude_project_dirs
from practicegraph.analysis.playbook import playbook_register
from practicegraph.analysis.practices import Practice, relevant_practices
from practicegraph.analysis.reliance import (
    RELIANCE_COPY,
    RelianceReading,
    compose_reliance,
)
from practicegraph.analysis.runway import RunwaySnapshot, compose_runway
from practicegraph.analysis.schedule import (
    ScheduleProfile,
    classify_time,
    compatibility_utc_schedule,
    marks_for_local_day,
)
from practicegraph.analysis.sessions import (
    SESSION_COPY,
    SessionReading,
    compose_sessions,
)
from practicegraph.analysis.sessiontail import (
    TAIL_WINDOW_WEEKS,
    SessionTailReading,
    compose_session_tail,
)
from practicegraph.analysis.skill_ledger import SkillOutcome, skill_audit
from practicegraph.analysis.skills import (
    WEAK_DIM_MAX_SCORE,
    SkillMatch,
    recently_copied_skills,
    relevant_skills,
)
from practicegraph.analysis.tool_defaults import ToolDefault, read_tool_defaults
from practicegraph.analysis.update import UpdateOffer
from practicegraph.analysis.verification import (
    VerificationReading,
    compose_verification,
)
from practicegraph.analysis.vocabulary import VocabularyCard, build_vocabulary_card
from practicegraph.analysis.workunits import (
    WorkUnitsReading,
    compose_work_units,
)
from practicegraph.config import Config, Prefs
from practicegraph.consent import read_consent
from practicegraph.emit import SYNTHETIC_EXAMPLE_LABEL, synthetic_example_payload
from practicegraph.history import (
    RANGE_KINDS,
    DayPoint,
    ProfileStats,
    RangeSummary,
    WeekOverWeek,
    gather_ranges,
    profile_stats,
    spend_series,
    week_over_week,
)
from practicegraph.report.brief import Brief, build_brief
from practicegraph.report.coach import (
    AckLine,
    CoachPillar,
    apply_styled_cues,
    coaching_as_style_items,
    compose_acknowledgment,
    compose_coaching,
    training_load,
)
from practicegraph.report.design import (
    CHEVRON_SVG,
    LOCK_SVG,
    SHIELD_SVG,
    TOKENS_CSS,
    brand_header,
    esc,
    est_pill,
)
from practicegraph.report.format import compact, count, duration_hm, percent, usd
from practicegraph.report.observation import PracticeObservation, compose_observation
from practicegraph.report.reflections import compose_reflections
from practicegraph.report.restyle import (
    COACH_STYLE_META_KEY,
    STYLE_META_KEY,
    cached_styled,
)
from practicegraph.report.viewmodel import (
    RIBBON_SCALE,
    day_ribbon_shape,
    queue_entries,
)
from practicegraph.report.weekly import WeeklyReading, compose_weekly
from practicegraph.store import (
    MARK_INTERACTIVE,
    QUEUE_STATUSES,
    ContributionRow,
    MarkRow,
    Store,
)

# One recommendation, not six. A page could carry 3 Advisor takes + 3 queue
# entries + 6 skills = 12 things asking to be acted on, and Ferraro & Price
# (N~100k) found generic technical advice not statistically significant on its
# own — volume is the arm that failed. What earns an effect is advice tied to a
# measured behaviour, so the shelf shows the single skill that answers the
# finding actually observed, and the outcome ledger audits it.
SKILLS_SHOWN = 1

# Awareness gets a hard ceiling. Three is the editorial standard the curation
# design already sets, and the cap is enforced here rather than trusted to the
# artifact: a served file that ever grows to twelve items must not be able to
# turn the page into a feed. Owner decision 2026-07-25 — news stays, at three.
NEWS_MAX_ITEMS = 3

_SHELL_CSS = """
.wrap { max-width:960px; margin:0 auto; padding:28px 28px 72px; }
header.top { display:flex; align-items:center; gap:14px; margin-bottom:18px; }
.mark { width:38px; height:38px; border-radius:10px; background:var(--teal-600);
        display:flex; align-items:center; justify-content:center; flex:none; }
.brand { display:flex; flex-direction:column; gap:1px; }
.brand .name { font-weight:700; font-size:16px; color:var(--ink-900);
               letter-spacing:-.01em; }
.brand .name b { color:var(--teal-700); font-weight:700; }
.brand .sub { font-size:12.5px; color:var(--ink-500); }
header.top .trust { margin-left:auto; }
nav.tabs { display:flex; gap:2px; border-bottom:1px solid var(--border-hair);
           margin-bottom:24px; flex-wrap:wrap; }
nav.tabs a { padding:10px 15px; font-size:13.5px; font-weight:600;
             color:var(--ink-500); text-decoration:none;
             border-bottom:2px solid transparent; margin-bottom:-1px; }
nav.tabs a:hover { color:var(--ink-700); }
nav.tabs a.privacy { margin-left:auto; display:inline-flex; align-items:center; gap:6px; }
nav.tabs a.privacy svg { width:14px; height:14px; }
main .panel { display:none; scroll-margin-top:16px; }
main .panel:target { display:flex; }
body:not(:has(main .panel:target)) main #today { display:flex; }
body:not(:has(main .panel:target)) nav.tabs a[href="#today"],
body:has(#today:target) nav.tabs a[href="#today"],
body:has(#range-week:target) nav.tabs a[href="#today"],
body:has(#range-month:target) nav.tabs a[href="#today"],
body:has(#range-all:target) nav.tabs a[href="#today"],
body:has(#insights:target) nav.tabs a[href="#insights"],
body:has(#sources:target) nav.tabs a[href="#sources"],
body:has(#skills:target) nav.tabs a[href="#skills"],
body:has(#privacy:target) nav.tabs a[href="#privacy"]
  { color:var(--teal-800); border-bottom-color:var(--teal-600); }
.stack { flex-direction:column; gap:18px; }
.hero { display:grid; grid-template-columns:1.4fr 1fr; gap:28px; align-items:center; }
.hero .figure { font-family:var(--mono); font-variant-numeric:tabular-nums;
                font-size:54px; font-weight:600; color:var(--ink-900);
                letter-spacing:-.02em; line-height:1; }
.hero .ratecard { font-family:var(--mono); font-size:11.5px; color:var(--ink-500);
                  margin-top:10px; }
.herokpis { display:flex; gap:26px; flex-wrap:wrap; }
.kpi .k { font-family:var(--mono); font-variant-numeric:tabular-nums; font-size:21px;
          font-weight:600; color:var(--ink-900); }
.kpi .l { font-size:12px; color:var(--ink-500); margin-top:2px; }
.kpi .k.good { color:var(--positive-600); }
.mixbar { display:flex; height:14px; border-radius:var(--r-pill); overflow:hidden;
          margin:4px 0 16px; background:var(--paper-100); }
.mixbar span { display:block; height:100%; }
.mixlegend { display:grid; grid-template-columns:1fr 1fr; gap:10px 22px; }
.mixrow { display:flex; align-items:center; gap:10px; }
.mixrow .dot { width:10px; height:10px; border-radius:3px; flex:none; }
.mixrow .lab { font-size:13px; color:var(--ink-700); flex:1; }
.mixrow .val { font-family:var(--mono); font-variant-numeric:tabular-nums;
               font-size:13.5px; font-weight:600; color:var(--ink-900); }
.mixrow .pct { font-family:var(--mono); font-size:11.5px; color:var(--ink-500);
               width:38px; text-align:right; }
table.usage { border-collapse:collapse; width:100%; font-size:13.5px; }
table.usage th { text-align:left; font-size:11.5px; font-weight:600;
                 letter-spacing:.08em; text-transform:uppercase;
                 color:var(--ink-500); padding:0 10px 10px 0; }
table.usage td { padding:9px 10px 9px 0; border-top:1px solid var(--border-hair);
                 color:var(--ink-800); }
table.usage th.n, table.usage td.n { text-align:right; font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
table.usage td.model { font-family:var(--mono); font-size:12.5px;
                       color:var(--ink-600); }
details.quiet { background:var(--paper-50); border:1px solid var(--border-hair);
                border-radius:var(--r-lg); }
details.quiet > summary { list-style:none; cursor:pointer; padding:16px 22px;
                          display:flex; align-items:center; gap:12px; }
details.quiet > summary::-webkit-details-marker { display:none; }
details.quiet > summary .chev { color:var(--ink-400); display:inline-flex; }
details.quiet[open] > summary .chev { transform:rotate(90deg); }
details.quiet .body { padding:0 22px 18px; }
.srcrow { display:grid; grid-template-columns:1fr auto auto; gap:14px;
          align-items:center; padding:12px 0;
          border-top:1px solid var(--border-hair); }
.srcrow:first-child { border-top:none; }
.srcrow .nm { font-size:14px; font-weight:600; color:var(--ink-800); }
.srcrow .meta { font-family:var(--mono); font-size:12px; color:var(--ink-500); }
.privacy-hero { display:flex; gap:16px; align-items:flex-start; }
.privacy-hero .shield { flex:none; width:46px; height:46px; border-radius:12px;
  background:var(--teal-50); border:1px solid var(--teal-200); display:flex;
  align-items:center; justify-content:center; color:var(--teal-700); }
.privacy-hero .shield svg { width:24px; height:24px; }
.pill-row { display:flex; flex-wrap:wrap; gap:10px; }
.factpill { display:inline-flex; align-items:center; gap:8px; padding:8px 13px;
  border-radius:var(--r-pill); background:var(--teal-50);
  border:1px solid var(--teal-200); font-size:12.5px; font-weight:600;
  color:var(--teal-800); font-family:var(--mono); }
.payload { background:var(--ink-900); border-radius:var(--r-lg); padding:20px 22px;
           overflow:auto; }
.payload pre { margin:0; font-family:var(--mono); font-size:12.5px; line-height:1.65;
               color:#cfe6df; white-space:pre; }
.payload .k { color:#9fd0c4; }
.payload .s { color:#e6d9a8; }
.payload .b { color:#e89f86; font-weight:600; }
.consent { display:flex; align-items:center; gap:12px; padding:14px 18px;
           border-radius:var(--r-md); }
.consent.on { background:var(--positive-100); border:1px solid var(--positive-600); }
.consent.on .state { font-weight:700; color:var(--positive-600); }
.consent.off { background:var(--paper-100); border:1px solid var(--border-soft); }
.consent.off .state { font-weight:700; color:var(--ink-700); }
.qstats { display:flex; gap:26px; flex-wrap:wrap; }
code { font-family:var(--mono); font-size:12.5px; background:var(--paper-100);
       border-radius:4px; padding:2px 7px; color:var(--ink-800); }
ul.cmds { margin:0; padding-left:18px; display:flex; flex-direction:column; gap:8px;
          font-size:13.5px; color:var(--ink-600); }
.wow { display:grid; grid-template-columns:auto auto 1fr; gap:26px;
       align-items:center; }
.delta .d { font-family:var(--mono); font-variant-numeric:tabular-nums;
            font-size:24px; font-weight:600; }
.delta .d.down { color:var(--positive-600); }
.delta .d.up { color:var(--caution-600); }
.delta .l { font-size:12px; color:var(--ink-500); margin-top:2px; }
.chips { display:flex; flex-wrap:wrap; gap:10px; }
.chip { display:inline-flex; align-items:center; gap:8px; padding:8px 13px;
        border-radius:var(--r-md); border:1px solid var(--border-hair);
        background:#fff; }
.chip .dot { width:8px; height:8px; border-radius:999px; flex:none; }
.chip .t { font-size:13px; font-weight:600; color:var(--ink-800); }
.sugg { display:flex; gap:15px; padding:18px 0;
        border-top:1px solid var(--border-hair); }
.sugg:first-of-type { border-top:none; padding-top:0; }
.sugg .n { font-family:var(--mono); font-size:13px; font-weight:600;
           color:var(--teal-600); flex:none; width:24px; }
.sugg .h { font-size:15px; font-weight:600; color:var(--ink-900);
           margin-bottom:4px; }
.sugg .r { font-size:13.5px; color:var(--ink-600); line-height:1.5; }
.sugg .impact { font-family:var(--mono); font-size:12px; font-weight:600;
                color:var(--caution-600); margin-top:6px; }
.bars { display:flex; flex-direction:column; gap:11px; }
.bar { display:grid; grid-template-columns:110px 1fr auto; gap:12px;
       align-items:center; }
.bar .lab { font-size:13.5px; font-weight:600; color:var(--ink-800); }
.bar .track { height:9px; background:var(--paper-100);
              border-radius:var(--r-pill); overflow:hidden; }
.bar .fill { display:block; height:100%; background:var(--teal-500);
             border-radius:var(--r-pill); }
.bar .fill.q { background:var(--suppressed-line); }
.bar .pct { font-family:var(--mono); font-variant-numeric:tabular-nums;
            font-size:13px; font-weight:600; color:var(--ink-900); width:42px;
            text-align:right; }
.focusmetric { font-family:var(--mono); font-variant-numeric:tabular-nums;
               font-size:34px; font-weight:600; color:var(--positive-600);
               line-height:1; }
.perfhead { display:flex; align-items:center; gap:16px; margin-bottom:14px; }
.perf { border-top:1px solid var(--border-hair); padding:11px 0; display:flex;
        flex-direction:column; gap:6px; }
.perf:first-of-type { border-top:none; }
.perf .row { display:grid; grid-template-columns:172px 1fr 40px 76px; gap:12px;
             align-items:center; }
.perf .lab { font-size:13.5px; font-weight:600; color:var(--ink-800); }
.perf .track { height:9px; background:var(--paper-100);
               border-radius:var(--r-pill); overflow:hidden; }
.perf .fill { display:block; height:100%; background:var(--teal-500);
              border-radius:var(--r-pill); }
.perf .num { font-family:var(--mono); font-variant-numeric:tabular-nums;
             font-size:13px; font-weight:600; color:var(--ink-900);
             text-align:right; }
.perf .ev { font-family:var(--mono); font-size:11.5px; color:var(--ink-500); }
.verdict { font-family:Georgia,'Times New Roman',serif; font-size:21px;
           line-height:1.5; color:var(--ink-900); margin:12px 0 2px; }
.weekline { font-family:var(--mono); font-size:12px; color:var(--teal-800);
            margin-top:8px; }
.acts { display:flex; gap:10px; margin-top:16px; flex-wrap:wrap; }
.act { display:inline-flex; align-items:center; gap:8px; padding:9px 15px;
       border-radius:var(--r-pill); font-size:13px; font-weight:600;
       text-decoration:none; }
.act.primary { background:var(--teal-700); color:#fff; }
.act.quiet { border:1px solid var(--border-hair); color:var(--ink-700);
             background:#fff; }
.assembled { font-family:var(--mono); font-size:10.5px; color:var(--ink-400);
             margin-top:14px; }
.dim { border-top:1px solid var(--border-hair); padding:11px 0; }
.dim:first-of-type { border-top:none; padding-top:2px; }
.dim .row { display:grid; grid-template-columns:150px 132px 66px 1fr; gap:14px;
            align-items:center; }
.dim .lab { font-size:13.5px; font-weight:600; color:var(--ink-800); }
.dim .score { font-family:var(--mono); font-variant-numeric:tabular-nums;
              font-size:16px; font-weight:600; }
.dim .score small { font-size:10.5px; margin-left:2px; }
.dim .score.up { color:var(--positive-600); }
.dim .score.down { color:var(--caution-600); }
.dim .score.flat { color:var(--ink-400); }
.dim .why { font-size:12.5px; color:var(--ink-600); }
.dim .why .rx { display:inline-block; margin-left:8px; padding:2px 9px;
                border-radius:var(--r-pill); background:var(--teal-50);
                color:var(--teal-800); font-size:11px; font-weight:600;
                text-decoration:none; white-space:nowrap; }
.qrow { display:flex; gap:15px; padding:16px 0;
        border-top:1px solid var(--border-hair); }
.qrow:first-of-type { border-top:none; padding-top:0; }
.qrow .n { font-family:var(--mono); font-size:13px; font-weight:600;
           color:var(--teal-600); flex:none; width:24px; }
.qrow .h { font-size:15px; font-weight:600; color:var(--ink-900);
           margin-bottom:4px; }
.qrow .r { font-size:13.5px; color:var(--ink-600); line-height:1.5; }
.qrow .ev { font-family:var(--mono); font-size:11.5px; color:var(--ink-400);
            margin-top:6px; }
.ranges { display:flex; gap:8px; flex-wrap:wrap; }
.ranges a { padding:7px 14px; border-radius:var(--r-pill); font-size:12.5px;
            font-weight:600; text-decoration:none; color:var(--ink-600);
            border:1px solid var(--border-hair); background:#fff; }
.ranges a.on { background:var(--teal-700); color:#fff;
               border-color:var(--teal-700); }
.toolmeta { font-family:var(--mono); font-size:11.5px; color:var(--ink-500);
            margin:2px 0 4px 122px; }
.rate { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
        margin-top:16px; padding-top:14px;
        border-top:1px solid var(--border-hair); }
.rate .q { font-size:13px; font-weight:600; color:var(--ink-700); }
.rate a { width:30px; height:30px; display:inline-flex; align-items:center;
          justify-content:center; border-radius:999px; text-decoration:none;
          border:1px solid var(--border-hair); background:#fff;
          font-family:var(--mono); font-size:13px; font-weight:600;
          color:var(--teal-800); }
.rate a:hover { background:var(--teal-50); border-color:var(--teal-500); }
.rate .hint { font-family:var(--mono); font-size:11px; color:var(--ink-400); }
a.dismiss { float:right; margin-left:12px; padding:3px 12px;
            border-radius:var(--r-pill); border:1px solid var(--border-hair);
            background:#fff; font-size:11.5px; font-weight:600;
            color:var(--ink-500); text-decoration:none; }
a.dismiss:hover { color:var(--ink-700); border-color:var(--ink-400); }
.qrow .why { font-size:12.5px; color:var(--ink-500); margin-top:6px;
             font-style:italic; }
svg.heatmap { display:block; margin-top:6px; }
.heatlegend { display:flex; align-items:center; gap:4px; margin-top:8px;
              font-size:11px; color:var(--ink-500); justify-content:flex-end; }
.heatlegend i { width:10px; height:10px; border-radius:2px; display:inline-block; }
.heatlegend span { margin:0 4px; }
@media (max-width:720px){ .hero,.g-2,.g-3 { grid-template-columns:1fr; }
  .wow { grid-template-columns:1fr 1fr; }
  .perf .row { grid-template-columns:96px 1fr 34px 70px; }
  .dim .row { grid-template-columns:96px 90px 56px 1fr; } }
"""


@dataclass(frozen=True, slots=True)
class PrivacyStatus:
    """Inputs for the Privacy Center view (FR-RPT-5). Contains no secrets."""

    consent_enabled: bool
    consent_decided_at: str | None
    endpoint_configured: bool
    org_configured: bool
    queue_counts: dict[str, int]
    queue_error_counts: dict[str, int]
    next_payload_pretty: str | None
    example_payload_pretty: str


def gather_privacy_status(config: Config, store: Store | None) -> PrivacyStatus:
    consent = read_consent(config.data_dir)
    queue_counts = dict.fromkeys(QUEUE_STATUSES, 0)
    queue_error_counts: dict[str, int] = {}
    next_payload_pretty: str | None = None
    if store is not None and store.exists():
        queue_counts = store.queue_counts()
        queue_error_counts = store.queue_error_counts()
        raw = store.next_unsent_payload()
        if raw is not None:
            next_payload_pretty = json.dumps(json.loads(raw), indent=2, sort_keys=True)
    return PrivacyStatus(
        consent_enabled=consent.emission_enabled,
        consent_decided_at=consent.decided_at,
        endpoint_configured=config.api_base_url is not None,
        org_configured=config.org_id is not None,
        queue_counts=queue_counts,
        queue_error_counts=queue_error_counts,
        next_payload_pretty=next_payload_pretty,
        example_payload_pretty=json.dumps(
            synthetic_example_payload(), indent=2, sort_keys=True
        ),
    )


@dataclass(frozen=True, slots=True)
class ShellExtras:
    """History-derived view data for the shell (M1/M3/M4). All computed
    locally; when absent the renderer shows honest not-yet states."""

    schedule: ScheduleProfile = field(default_factory=compatibility_utc_schedule)
    local_day: date | None = None
    accounting_day_utc: date | None = None
    practice_observation: PracticeObservation = field(
        default_factory=lambda: compose_observation(
            None, None, compatibility_utc_schedule()
        )
    )

    trend: list[DayPoint] = field(default_factory=list)
    wow: WeekOverWeek | None = None
    focus_enabled: bool = True
    focus: FocusMetrics | None = None
    tips: list[str] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    work_mix: dict[str, int] = field(default_factory=dict)
    maturity: list[tuple[str, MaturityLevel]] = field(default_factory=list)
    pace: SpendPace | None = None
    heatmap: list[DayPoint] = field(default_factory=list)
    profile: ProfileStats | None = None
    rhythm: RhythmStats | None = None
    blocks: dict[str, int] = field(default_factory=dict)
    practices: list[tuple[Practice, bool]] = field(default_factory=list)
    # Skill registry matches (report/viewmodel serves them): skills whose tags
    # fit the person's own recent work-type mix + tools. Matched LOCALLY - the
    # weight is the matched work-type's session count, for the "why" line.
    skills: list[SkillMatch] = field(default_factory=list)
    skills_source: str = "bundled"
    # A newer release, IF the signed manifest verified against the key built
    # into this binary. None covers every other case - no key, no manifest,
    # bad signature, or simply already current - because an update prompt that
    # can be forged is worse than no update prompt at all.
    update: UpdateOffer | None = None
    # What is wrong with the INSTALL rather than the practice: a leftover
    # machine-wide agent writing a second store, or a scheduler that has not
    # run. Both became possible on 2026-07-26 and neither is visible anywhere
    # else - a page that cannot say "I am out of date" lies by omission.
    install_notices: list[InstallNotice] = field(default_factory=list)
    # The closed half of the skill loop: what the last taken skill's own target
    # measure has done since. Reports movement the other way on the same
    # schedule as movement toward it — a loop that only reports wins is not a
    # measurement. Local-only forever.
    skill_outcome: SkillOutcome | None = None
    # News feed (report/viewmodel serves it): the active briefings, closed copy.
    # Display-only; the endpoint never fetches a briefing url, and which items
    # were seen/dismissed stays local (NFR-PRV-6).
    briefings: list[Briefing] = field(default_factory=list)
    # Curated public news is independent of the bundled briefing fallback.
    # It is display-only and validated before reaching this structure.
    news: list[NewsItem] = field(default_factory=list)
    # The dated shelves behind the editorial feeds: which previous days are
    # readable locally. Days only — the items are fetched on demand.
    news_days: list[str] = field(default_factory=list)
    community: list[CommunityItem] = field(default_factory=list)
    feed_status: dict[str, dict[str, str | None]] = field(default_factory=dict)
    build_ideas: list[object] = field(default_factory=list)
    build_repos: list[object] = field(default_factory=list)
    build_ideas_days: list[str] = field(default_factory=list)
    # Project folders the one-click pin may target (leafs only on the wire;
    # the server re-derives the real paths at action time).
    pin_projects: list[str] = field(default_factory=list)
    # Human-curated external benchmark guidance interpreted against only local
    # tool/model turn counts. Benchmark token units are never mixed with local
    # token counters, and the local model name is not serialized to the UI.
    model_recommendations: list[ModelRecommendation] = field(default_factory=list)
    # The documentation shelf: served artifact or the bundled default —
    # never None, reference material always exists.
    docs: DocsArtifact = EMPTY_DOCS
    # The model catalog: available models + effort ladders, same never-None
    # served-or-bundled rule.
    model_catalog: ModelCatalog = EMPTY_CATALOG
    # Which audience the page speaks to (PROFILES value; named `audience`
    # here because `profile` already means the legacy performance stats).
    # Chooses copy and served-content lanes only — never what is collected.
    audience: str = "coding"
    # The pinned session defaults, one per harness, read from each tool's
    # own local config (None model = not pinned).
    tool_defaults: tuple[ToolDefault, ...] = ()
    # The harness inventory (tools page): versions from the newest logs,
    # connectors from the configs, skills from disk, and the latest
    # published releases from the daily pull.
    harness_versions: tuple[ToolVersion, ...] = ()
    harness_connectors: tuple[HarnessConnectors, ...] = ()
    harness_skills: tuple[HarnessSkills, ...] = ()
    # tool -> (stable version, stable url, newest-any version, newest-any
    # url); the viewmodel compares an install against its own channel.
    harness_releases: dict[str, tuple[str, str, str, str]] = field(
        default_factory=dict
    )
    harness_features: tuple[HarnessFeatures, ...] = ()
    # The Advisor board (analysis/advisor.py): at most three verdict-shaped
    # takes fusing the person's own observed economics (receipts first) with
    # the curated market artifact (corroboration, attributed). Market claims
    # carry expiry; sub-floor evidence renders the calm default. Local-only
    # forever (NFR-PRV-6).
    advisor: AdvisorBoard | None = None
    # The practice check-in: one CoachPillar per pillar (pattern/focus/
    # discipline/craft), composed locally from the dimensions + rhythm.
    # `coaching` is what the view shows (styled if a daily restyle is cached);
    # `coaching_raw` is the deterministic source the restyle stage reads.
    coaching: list[CoachPillar] = field(default_factory=list)
    coaching_raw: list[CoachPillar] = field(default_factory=list)
    # The conditioning readout (analysis/conditioning.py): up to three
    # composite fitness-level readings — capacity, load balance, composure —
    # computed indirectly from the behavioral record. An unavailable reading
    # is absent from the list, never zero. Local-only forever (NFR-PRV-6).
    conditioning: list[ConditioningIndicator] = field(default_factory=list)
    # Coaching acknowledgment (A3): last week's cue-pillar acknowledged if it
    # improved (>= ACK_MIN_DELTA), else None — silence, never a negative line.
    # Composed purely on read (the ledger is written only in the tick).
    coach_ack: AckLine | None = None
    # The weekly reading (A4): last week vs the week before, a theme, the ack,
    # and next week's focus — or None when either week is too thin. Composed
    # on read; persists nothing.
    weekly_reading: WeeklyReading | None = None
    training_load: str = "unknown"
    # Close the day (A2): whether the passed-in day has been closed. Local-only
    # meta flag; today only, never a chain or a count.
    day_closed: bool = False
    # Quota runway (A1): today's per-window rate-limit runway, or an unavailable
    # snapshot the view hides. Composed locally; never an emit.
    runway: RunwaySnapshot | None = None
    # The economy reading (W1.1): capability per dollar/window over the
    # receipts window — one lever, calm-first, dual-denominated (AM-1), with
    # the first-open retrospective (AM-3) until dismissed. Local-only forever.
    economy: EconomyReading | None = None
    billing_mode: str = "unknown"
    billing_by_tool: dict[str, str] = field(default_factory=dict)
    quiet_hours: dict[str, object] | None = None
    # Session grain (W2.0): the latest session's receipt and the context-tax
    # cohorts, from the session_totals table. Local-only; session identity
    # never leaves this structure.
    sessions: SessionReading | None = None
    # Units of work (W4): the window's distribution over deterministic
    # work units — median/p90 model spend, concentration, landed share, the
    # largest units. Window grain only: per-day unit counts stay off every
    # surface while the day-close probe collects felt-vs-measured pairs.
    # Local-only forever.
    work_units: WorkUnitsReading | None = None
    # Which world the playbook prompts speak to: "repo" when the window shows
    # repository evidence (a branch hash, a commit or test attempt), "plain"
    # when it does not — a document-producing user must never be told to edit
    # AGENTS.md (red-team 2026-08-13). Default keeps old callers on the
    # original register.
    playbook_register: str = "repo"
    # How you worked with it (Phase 1): the collaboration-shape reading —
    # Anthropic's RCT construct read as structure, never as a score and never
    # as an over/under-reliance label. Local-only forever.
    reliance: RelianceReading | None = None
    # Where the day ends (analysis/sessiontail.py): the last-prompt tail
    # across twelve weeks plus the concurrency cross — the behavioral half of
    # the stopping-point construct the self-report surveys can only ask
    # about. Late-evening activity, never a sleep claim. Local-only forever.
    session_tail: SessionTailReading | None = None
    # The Calibration Mirror: a pending estimate probe, the last answered
    # result, and the aggregate once earned. The probe is interactive, so the
    # static report renders only the results half. Local-only.
    calibration: CalibrationReading | None = None
    # The strain reading's two halves (STRAIN_READING_PLAN S1/S2): the
    # verification-load components and the felt-drain probe that must be
    # answered before they show for the day.
    verification: VerificationReading | None = None
    drain: DrainSurface | None = None
    # The teaching layer: the six words this page is built on, shown until
    # dismissed. App-only — the static report is a fallback surface and a
    # card you cannot dismiss there would never go away.
    vocabulary: VocabularyCard | None = None
    performance: PerformanceProfile | None = None
    weekly: list[WindowInputs] = field(default_factory=list)
    day_marks: list[MarkRow] = field(default_factory=list)
    brief: Brief | None = None
    ranges: list[RangeSummary] = field(default_factory=list)
    # P3-gate transparency for the app's recognition band: the rhythm
    # window's full mark count next to the interactive subset the gate keeps
    # (behavior is scored on the subset; the difference is agents working).
    rhythm_marks_total: int = 0
    rhythm_marks_interactive: int = 0
    # The reflection band (report/reflections.py): render-ready sentences
    # from 28-day cohort math. Local-only forever — view model, never wire.
    # `reflections` is styled-if-cached (what the view shows); `reflections_raw`
    # is the deterministic source the restyle stage fingerprints on.
    reflections: list[dict[str, str]] = field(default_factory=list)
    reflections_raw: list[dict[str, str]] = field(default_factory=list)
    capability: CapabilityReading = field(
        default_factory=lambda: CapabilityReading(
            paths=("knowledge", "software"), paths_confirmed=False,
            pending_outcome=False, opportunity=None, practice_result=None,
        )
    )


def gather_shell_extras(
    store: Store,
    snapshot: DailySnapshot,
    prefs: Prefs,
    day: date,
    schedule: ScheduleProfile | None = None,
    local_today: date | None = None,
    env: dict[str, str] | None = None,
    generated_at: datetime | None = None,
    include_static_details: bool = True,
) -> ShellExtras:
    """Assemble every history-backed view input for one day.

    P3 behavioral gate: focus metrics, tips, the work-type mix, the day
    ribbon, rhythm windows, and the refire/marathon groupings read only
    interactive (human-driven) marks — sidechain activity still counts in
    every cost and token figure (snapshot/ranges/trend are untouched)."""
    schedule = schedule or compatibility_utc_schedule()
    local_today = local_today or day
    effective_generated_at = generated_at or datetime.combine(day, time.max, tzinfo=UTC)
    marks = interactive_marks(marks_for_local_day(store, local_today, schedule))
    focus_metrics = compute_metrics(marks) if marks else None
    tips: list[str] = []
    if focus_metrics is not None and prefs.focus_coaching:
        tips = visible_tips(triggered_tips(focus_metrics), store.dismissed_tips())
    active_7 = len(
        store.active_days((day - timedelta(days=6)).isoformat(), day.isoformat())
    )
    pace = None
    if prefs.monthly_budget_micro_usd:
        pace = spend_pace(store, day.isoformat(), prefs.monthly_budget_micro_usd)
    rhythm_start = local_today - timedelta(days=RHYTHM_WINDOW_DAYS - 1)
    marks_by_day: dict[str, list[MarkRow]] = {}
    rhythm_marks_total = 0
    rhythm_marks_interactive = 0
    for row in store.marks_between(
        (rhythm_start - timedelta(days=1)).isoformat(),
        (local_today + timedelta(days=1)).isoformat(),
    ):
        mark: MarkRow = row[1:]
        context = classify_time(datetime.fromisoformat(mark[1]), schedule)
        if not rhythm_start <= context.local_day <= local_today:
            continue
        rhythm_marks_total += 1
        if not mark[MARK_INTERACTIVE]:
            continue
        rhythm_marks_interactive += 1
        marks_by_day.setdefault(context.local_day.isoformat(), []).append(mark)
    from practicegraph.catalog import (
        active_skills_source,
        feed_status,
        list_artifact_days,
        load_advisor,
        load_briefings,
        load_build_ideas,
        load_community,
        load_docs,
        load_harness_releases,
        load_model_catalog,
        load_models,
        load_news,
        load_practices,
        load_skills,
        load_update_offer,
    )

    findings = detect(snapshot)
    # P0 late-night drift: this week's night share vs your own prior-4-week
    # baseline. Descriptive-only by decision — INFO severity, never escalates.
    week_from = (local_today - timedelta(days=6)).isoformat()
    current_by_day = {
        mark_day: day_marks
        for mark_day, day_marks in marks_by_day.items()
        if mark_day >= week_from
    }
    baseline_by_day: dict[str, list[MarkRow]] = {}
    for row in store.marks_between(
        (local_today - timedelta(days=35)).isoformat(),
        (local_today - timedelta(days=6)).isoformat(),
    ):
        baseline_mark: MarkRow = row[1:]
        context = classify_time(datetime.fromisoformat(baseline_mark[1]), schedule)
        if not (
            local_today - timedelta(days=34)
            <= context.local_day
            <= local_today - timedelta(days=7)
        ):
            continue
        if not baseline_mark[MARK_INTERACTIVE]:
            continue  # both drift windows gate identically (P3)
        baseline_by_day.setdefault(context.local_day.isoformat(), []).append(
            baseline_mark
        )
    drift = quiet_hours_drift(current_by_day, baseline_by_day, schedule)
    if drift is not None:
        findings.append(Finding("late_night_drift", Severity.INFO, drift))
    # P2 refire: human replies chasing failed runs (calibrated gate;
    # INFO forever by decision — it informs, never alarms).
    if (
        focus_metrics is not None
        and focus_metrics.refire_replies >= REFIRE_MIN_COUNT
    ):
        findings.append(
            Finding(
                "refire_after_failure", Severity.INFO,
                focus_metrics.refire_replies,
            )
        )
    # P2 marathon: today's most-compacted session (grouped per session in
    # the gate). INFO forever by decision — it informs, never alarms.
    marathon = marathon_finding(marks)
    if marathon is not None:
        findings.append(marathon)
    # Waved-through approvals: several long-run approvals answered fast and
    # short, AND waving was at least half of the day's approval moments
    # (calibrated gates in focus.py). INFO forever by the descriptive-only
    # decision — it informs, never alarms.
    if (
        focus_metrics is not None
        and focus_metrics.waved_through >= APPROVAL_WAVED_MIN_COUNT
        and focus_metrics.waved_through * 100
        >= focus_metrics.approval_moments * APPROVAL_WAVED_MIN_SHARE_PCT
    ):
        findings.append(
            Finding(
                "approvals_waved_through", Severity.INFO,
                focus_metrics.waved_through,
            )
        )
    # P4 approaching-quota: the day's latest provider rate-limit reading at
    # or above the named gate. Informational forever (INFO by decision) and
    # local-only — the reading never feeds an emit.
    reading = store.latest_rate_limit(day.isoformat())
    if reading is not None and reading[0] >= APPROACHING_QUOTA_PCT * 10:
        findings.append(
            Finding("approaching_quota", Severity.INFO, reading[0] // 10)
        )
    # W2.0 carried context: the session-cohort reading, raised as a finding so
    # the skill shelf can answer it. Only the "carried" register fires — when
    # the cache is absorbing the weight there is nothing to act on. Suppressed
    # when today's own context_bloat already fired: same fact, one voice.
    sessions_reading = compose_sessions(store, day, RECEIPTS_WINDOW_DAYS)
    if (
        sessions_reading.tax is not None
        and sessions_reading.tax.register == "carried"
        and not any(f.finding_id == "context_bloat" for f in findings)
    ):
        findings.append(
            Finding(
                "context_carried",
                Severity.OPPORTUNITY,
                sessions_reading.tax.cost_multiple_tenths,
            )
        )
    tools_observed = {row.tool for row in snapshot.rows if row.assistant_turns > 0}
    practices = relevant_practices(
        tuple(load_practices(store.path.parent)),  # type: ignore[arg-type]
        tools_observed,
        {finding.finding_id for finding in findings},
    )
    performance = gather_performance(store, local_today, schedule)
    # Skill registry, matched locally to this person's own recent work: the
    # work-type mix + tools they used, PLUS the findings that fired today and
    # the maturity dimensions that are measured-weak. All locally derived —
    # nothing about the match leaves the machine (INV-2, NFR-PRV-6).
    work_mix = work_type_mix(marks)
    active_findings = {finding.finding_id for finding in findings}
    weak_dimensions = {
        reading.dimension_id
        for reading in (performance.readings if performance else ())
        if reading.score <= WEAK_DIM_MAX_SCORE
    }
    data_dir = store.path.parent
    active_source = active_skills_source(data_dir, store)
    # The Advisor board is computed before the shelf so the skills match can
    # subtract the findings its takes already carry — one fact, one voice
    # per page (the advisor-over-findings precedence, extended).
    advisor_receipts = gather_receipts(store, day)
    advisor_board = build_advisor_board(
        advisor_receipts,
        load_advisor(data_dir),
        local_today,
        audit=advisor_audit(store, advisor_receipts, local_today),
        billing_mode=prefs.billing_mode,
    )
    # Skills match on the WEEK's work mix (the today-only mix is empty every
    # morning and reshuffles the shelf hour to hour), skip recently-copied
    # skills (delivered goods retire for a while), and rotate ties weekly.
    week_marks = [
        mark
        for _mark_day, day_marks in sorted(current_by_day.items())
        for mark in day_marks
    ]
    skills = relevant_skills(
        tuple(load_skills(data_dir)),  # type: ignore[arg-type]
        tools_observed,
        work_type_mix(week_marks),
        active_findings - covered_findings(advisor_board),
        weak_dimensions,
        limit=SKILLS_SHOWN,
        day=local_today,
        recently_copied=recently_copied_skills(store, local_today),
    )
    # ...and what the last one actually did. The shelf could say a skill was
    # taken; this is the half that says what changed, in both directions.
    skill_outcome = skill_audit(store, advisor_receipts, local_today)
    weekly = weekly_windows(store, local_today, schedule=schedule) if include_static_details else []
    wow = week_over_week(store, day)
    rhythm = rhythm_stats(marks_by_day, schedule) if marks_by_day else None
    # Conditioning: the current rhythm window against the 28 days before it —
    # both interactive-gated (P3) and keyed by schedule-local day, like every
    # behavioral surface. One extra bounded pass over the prior window.
    prior_start = rhythm_start - timedelta(days=RHYTHM_WINDOW_DAYS)
    prior_end = rhythm_start - timedelta(days=1)
    prior_by_day: dict[str, list[MarkRow]] = {}
    for row in store.marks_between(
        (prior_start - timedelta(days=1)).isoformat(),
        (prior_end + timedelta(days=1)).isoformat(),
    ):
        prior_mark: MarkRow = row[1:]
        context = classify_time(datetime.fromisoformat(prior_mark[1]), schedule)
        if not prior_start <= context.local_day <= prior_end:
            continue
        if not prior_mark[MARK_INTERACTIVE]:
            continue
        prior_by_day.setdefault(context.local_day.isoformat(), []).append(
            prior_mark
        )
    conditioning = compose_conditioning(
        marks_by_day, prior_by_day, local_today, RHYTHM_WINDOW_DAYS
    )
    # Where the day ends: twelve weeks of interactive marks, flat — the
    # composer groups them into 05:00-boundary practice days itself (a
    # past-midnight tail belongs to the evening it grew from, so the
    # schedule-local calendar-day keying above would split it). One bounded
    # pass; a day of slack each side covers the boundary shift.
    tail_start = local_today - timedelta(days=TAIL_WINDOW_WEEKS * 7 - 1)
    tail_marks: list[MarkRow] = []
    for row in store.marks_between(
        (tail_start - timedelta(days=1)).isoformat(),
        (local_today + timedelta(days=1)).isoformat(),
    ):
        tail_mark: MarkRow = row[1:]
        if not tail_mark[MARK_INTERACTIVE]:
            continue
        tail_marks.append(tail_mark)
    session_tail = compose_session_tail(tail_marks, schedule, local_today)
    # The RAW (deterministic, pre-restyle) composed surfaces. The view fields
    # below overlay the daily restyle onto these; the *_raw fields carry the
    # un-styled source so the restyle stage fingerprints on the real composer
    # output, never on already-styled text (which would defeat the once-a-day
    # cache and re-spend every tick).
    raw_reflections = compose_reflections(
        marks_by_day,
        rhythm,
        rhythm_marks_total,
        rhythm_marks_interactive,
        schedule,
    )
    # How you worked with it: the collaboration-shape reading. Rework and tool
    # volume come from the receipts window already gathered above, so the same
    # counters are read once and the page cannot disagree with itself.
    # The prior window is the same 28 days the conditioning readout already
    # walked, so the trend costs no extra pass. It is what makes the facets
    # readable: a level is a factoid, a movement is a finding.
    prior_receipts = gather_receipts(
        store, local_today - timedelta(days=RHYTHM_WINDOW_DAYS),
        RHYTHM_WINDOW_DAYS,
    )
    prompt_tokens = sum(
        f.tokens.input + f.tokens.cached + f.tokens.cache_creation
        for f in advisor_receipts.families
    )
    window_turns = sum(f.assistant_turns for f in advisor_receipts.families)
    reliance = compose_reliance(
        daily=reliance_daily_metrics(marks_by_day),
        marks_total=rhythm_marks_total,
        marks_interactive=rhythm_marks_interactive,
        waiting_minutes=rhythm.waiting_minutes if rhythm else 0,
        rework_edits=sum(f.rework_edits for f in advisor_receipts.families),
        tool_calls=sum(f.tool_calls for f in advisor_receipts.families),
        window_days=RHYTHM_WINDOW_DAYS,
        prior_daily=reliance_daily_metrics(prior_by_day),
        prior_rework_edits=sum(f.rework_edits for f in prior_receipts.families),
        prior_tool_calls=sum(f.tool_calls for f in prior_receipts.families),
        prompt_tokens_per_turn=(
            prompt_tokens // window_turns if window_turns > 0 else 0
        ),
    )
    raw_coaching = compose_coaching(
        performance.readings if performance else (), rhythm
    )
    model_artifact = load_models(store.path.parent)
    model_usage: dict[str, dict[str, int]] = {}
    for offset in range(27, -1, -1):
        usage_day = (day - timedelta(days=offset)).isoformat()
        for tool, model, values in store.day_usage_rows(usage_day):
            turns = ContributionRow.from_values(values).assistant_turns
            if turns > 0:
                tool_usage = model_usage.setdefault(tool, {})
                tool_usage[model] = tool_usage.get(model, 0) + turns
    # The pinned defaults from each tool's own config: what a session
    # STARTS on, which the logs alone cannot see. The artifact judges the
    # pin against its casts inside build_model_recommendations.
    # env is a test seam: fixtures pass their hermetic homes so nothing
    # here ever reads the developer's real ~/.claude or ~/.codex. Runtime
    # callers leave it None (the process environment).
    tool_defaults = read_tool_defaults(env)
    defaults_by_tool = {
        default.tool: (default.model, default.effort)
        for default in tool_defaults
    }
    model_recommendations = (
        list(build_model_recommendations(
            model_artifact, model_usage, local_today, defaults_by_tool
        ))
        if model_artifact is not None
        else []
    )
    build_edition = load_build_ideas(store.path.parent)
    return ShellExtras(
        schedule=schedule,
        local_day=local_today,
        accounting_day_utc=day,
        practice_observation=compose_observation(focus_metrics, rhythm, schedule),
        trend=spend_series(store, day, 7),
        wow=wow,
        focus_enabled=prefs.focus_coaching,
        focus=focus_metrics,
        tips=tips,
        suggestions=visible_suggestions(store, derive_suggestions(snapshot)),
        findings=findings,
        work_mix=work_mix,
        maturity=maturity_signals(snapshot, active_7),
        pace=pace,
        heatmap=spend_series(store, day, HEATMAP_DAYS),
        profile=profile_stats(store, day),
        rhythm=rhythm,
        blocks=block_counters(store, local_today.isoformat()),
        practices=practices,
        skills=skills,
        skills_source=active_source,
        skill_outcome=skill_outcome,
        # Re-verified on every render, never trusted because we cached it: the
        # catalog directory is world-readable (2026-07-19 review), so the file
        # is untrusted input each time it is read.
        update=load_update_offer(data_dir, __version__),
        install_notices=compose_install_health(
            store.meta_get("last_tick_at"),
            datetime.now(UTC),
            legacy_service=legacy_service_present(),
        ),
        briefings=list(load_briefings(store.path.parent)),  # type: ignore[arg-type]
        news=list(load_news(store.path.parent))[:NEWS_MAX_ITEMS],
        news_days=list(list_artifact_days(store.path.parent, "news")),
        community=list(load_community(store.path.parent).items),
        feed_status={
            channel: feed_status(store.path.parent, channel, profile=prefs.profile)
            for channel in ("news", "build-ideas", "community", "models", "model-catalog", "docs")
        },
        build_ideas=list(build_edition.ideas),
        build_repos=list(build_edition.repos),
        build_ideas_days=list(list_artifact_days(store.path.parent, "build-ideas")),
        model_recommendations=model_recommendations,
        docs=load_docs(store.path.parent, prefs.profile),
        model_catalog=load_model_catalog(store.path.parent, prefs.profile),
        audience=prefs.profile,
        tool_defaults=tool_defaults,
        harness_versions=installed_versions(env),
        harness_connectors=read_connectors(env),
        harness_skills=read_skills(env),
        harness_releases=load_harness_releases(store),
        harness_features=read_features(env),
        pin_projects=sorted(
            recent_claude_project_dirs(
                dict(os.environ) if env is None else env
            )
        ),
        advisor=advisor_board,
        coaching=_coaching_for_view(store, raw_coaching),
        coaching_raw=raw_coaching,
        conditioning=conditioning,
        coach_ack=compose_acknowledgment(store, local_today, schedule),
        weekly_reading=(
            compose_weekly(store, local_today, schedule, advice_audit=advisor_board.audit)
            if include_static_details else None
        ),
        training_load=training_load(rhythm),
        day_closed=is_day_closed(store, local_today.isoformat()),
        runway=compose_runway(store, day.isoformat()),
        billing_mode=prefs.billing_mode,
        billing_by_tool=prefs.billing_by_tool,
        quiet_hours=quiet_hours_reading(
            store.marks_between(
                (local_today - timedelta(days=15)).isoformat(),
                (local_today + timedelta(days=1)).isoformat(),
            ), schedule, local_today,
        ),
        economy=compose_economy(
            store, day, advisor_receipts, advisor_board, wow, prefs.billing_mode
        ),
        sessions=sessions_reading,
        work_units=compose_work_units(store, day),
        playbook_register=playbook_register(store, day),
        reliance=reliance,
        session_tail=session_tail,
        calibration=compose_calibration(store, local_today, prefs.focus_coaching),
        verification=compose_verification(store, local_today),
        drain=compose_drain(store, local_today, prefs.focus_coaching),
        vocabulary=build_vocabulary_card(store),
        performance=performance,
        weekly=weekly,
        day_marks=marks,
        brief=build_brief(
            snapshot,
            performance,
            weekly,
            spend_series(store, day, PERFORMANCE_WINDOW_DAYS),
            wow,
            day,
        ) if include_static_details else None,
        ranges=gather_ranges(store, day),
        rhythm_marks_total=rhythm_marks_total,
        rhythm_marks_interactive=rhythm_marks_interactive,
        reflections=_reflections_for_view(store, raw_reflections),
        reflections_raw=raw_reflections,
        capability=compose_capability_reading(
            store, prefs, local_today, effective_generated_at,
            schedule=schedule,
        ),
    )


def _reflections_for_view(
    store: Store, reflections: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Overlay the daily restyle onto the composed band when a valid cache
    exists for exactly these facts; otherwise the deterministic sentences.
    The cache read re-validates every styled line against its source (numbers
    intact, copy clean), so a stale or tampered entry can only ever degrade
    to the template — never surface an unfaithful claim (report/restyle.py)."""
    styled = cached_styled(reflections, store.meta_get(STYLE_META_KEY))
    return styled if styled is not None else reflections


def _coaching_for_view(
    store: Store, pillars: list[CoachPillar]
) -> list[CoachPillar]:
    """Overlay the daily restyle onto the coaching cues when a valid cache
    exists for exactly these cues (same fingerprint + revalidation as the
    reflection band); otherwise the deterministic cues. Fresh-daily wording
    with the numbers and copy rules still enforced per cue."""
    items = coaching_as_style_items(pillars)
    styled = cached_styled(items, store.meta_get(COACH_STYLE_META_KEY))
    return apply_styled_cues(pillars, styled) if styled is not None else pillars


_KEY_LINE = re.compile(r'^(\s*)"([^"]+)"(: )(.*?)(,?)$')


def _colorize_json(pretty: str) -> str:
    """Deterministic syntax tinting for the payload panel. Input is the exact
    pretty-printed JSON; every fragment is escaped before wrapping."""
    out_lines: list[str] = []
    for line in pretty.splitlines():
        match = _KEY_LINE.match(line)
        if not match:
            out_lines.append(esc(line))
            continue
        indent, key, sep, value, comma = match.groups()
        if value.startswith('"'):
            value_html = f'<span class="s">{esc(value)}</span>'
        elif value in ("true", "false"):
            value_html = f'<span class="b">{esc(value)}</span>'
        else:
            value_html = esc(value)
        out_lines.append(
            f'{indent}<span class="k">{esc(f'"{key}"')}</span>{esc(sep)}'
            f"{value_html}{esc(comma)}"
        )
    return "\n".join(out_lines)


def _sparkline(trend: list[DayPoint]) -> str:
    """Seven-day spend sparkline (inline SVG, integer math)."""
    active = [p for p in trend if p.cost_micro_usd > 0]
    if len(trend) < 2 or len(active) < 1:
        return ""
    max_cost = max(p.cost_micro_usd for p in trend)
    if max_cost <= 0:
        return ""
    n = len(trend)
    points = []
    for index, point in enumerate(trend):
        x = 2 + (index * 256) // (n - 1)
        y = 42 - (point.cost_micro_usd * 34) // max_cost
        points.append(f"{x},{y}")
    polyline = " ".join(points)
    area = f"2,46 {polyline} 258,46"
    last_x, last_y = points[-1].split(",")
    return (
        '<div style="margin-top:18px;">'
        '<div class="eyebrow" style="margin-bottom:8px;">Spend &middot; last 7 days'
        "</div>"
        '<svg width="100%" height="46" viewBox="0 0 260 46" '
        'preserveAspectRatio="none" role="img" aria-label="spend sparkline">'
        f'<polygon points="{area}" fill="#dcefe2" opacity="0.5"></polygon>'
        f'<polyline points="{polyline}" fill="none" stroke="#2f7d57" '
        'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></polyline>'
        f'<circle cx="{last_x}" cy="{last_y}" r="3" fill="#2f7d57"></circle>'
        "</svg></div>"
    )


def _hero_card(
    snapshot: DailySnapshot, rate_card_version: str, extras: ShellExtras | None
) -> str:
    tokens_total = sum(
        row.tokens.input + row.tokens.output + row.tokens.cached
        + row.tokens.cache_creation
        for row in snapshot.rows
    )
    cached = sum(row.tokens.cached for row in snapshot.rows)
    prompt_side = cached + sum(row.tokens.input for row in snapshot.rows)
    cached_pct = percent(cached, prompt_side)
    sparkline = _sparkline(extras.trend) if extras is not None else ""
    return (
        '<div class="card"><div class="hero">'
        "<div>"
        f'<div class="eyebrow" style="margin-bottom:12px;">Estimated spend &middot; '
        f"UTC accounting day {esc(snapshot.day.isoformat())}</div>"
        f'<div class="figure">{esc(usd(snapshot.total_cost_micro_usd))}{est_pill()}</div>'
        f'<div class="ratecard">rate card {esc(rate_card_version)} &middot; '
        f"{esc(count(snapshot.session_count))} sessions &middot; "
        f"{esc(compact(tokens_total))} tokens</div>"
        "</div>"
        '<div><div class="herokpis">'
        f'<div class="kpi"><div class="k good">{cached_pct}%</div>'
        '<div class="l">served from cache</div></div>'
        f'<div class="kpi"><div class="k">{esc(count(snapshot.session_count))}</div>'
        '<div class="l">sessions</div></div>'
        f'<div class="kpi"><div class="k">'
        f"{esc(count(snapshot.total_assistant_turns))}</div>"
        '<div class="l">assistant turns</div></div>'
        f"</div>{sparkline}</div>"
        "</div></div>"
    )


_LEVEL_TAG = {
    "leading": "leading",
    "developing": "steady",
    "emerging": "emerging",
    "not_yet": "quiet",
}
_LEVEL_LABEL = {
    "leading": "leading",
    "developing": "developing",
    "emerging": "emerging",
    "not_yet": "not yet",
}


def _wow_and_maturity(extras: ShellExtras) -> str:
    out = ['<div class="grid g-2">']
    out.append('<div class="card">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">Week over week</span>'
        '<span class="meta">vs. prior 7 days</span></div>'
    )
    if extras.wow is not None:
        cost_class = "down" if extras.wow.cost_delta_pct <= 0 else "up"
        token_class = "down" if extras.wow.tokens_delta_pct <= 0 else "up"
        cost_sign = "" if extras.wow.cost_delta_pct < 0 else "+"
        token_sign = "" if extras.wow.tokens_delta_pct < 0 else "+"
        out.append(
            '<div class="wow">'
            f'<div class="delta"><div class="d {cost_class}">'
            f'{cost_sign}{extras.wow.cost_delta_pct}%</div>'
            '<div class="l">est. cost</div></div>'
            f'<div class="delta"><div class="d {token_class}">'
            f'{token_sign}{extras.wow.tokens_delta_pct}%</div>'
            '<div class="l">tokens</div></div>'
            "<div></div></div>"
        )
    else:
        out.append(
            '<p class="muted" style="font-size:13px;">Not enough history yet - the '
            "comparison appears once a prior week of local data exists.</p>"
        )
    out.append("</div>")
    out.append('<div class="card">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">Maturity signals</span>'
        '<span class="meta">behavioral &middot; local only</span></div>'
    )
    if extras.maturity:
        out.append('<div class="chips">')
        for signal_id, level in extras.maturity:
            out.append(
                f'<span class="chip"><span class="t">'
                f"{esc(MATURITY_LABELS.get(signal_id, signal_id))}</span>"
                f'<span class="tag {_LEVEL_TAG[level.value]}">'
                f"{esc(_LEVEL_LABEL[level.value])}</span></span>"
            )
        out.append("</div>")
        out.append(
            '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">Signals '
            "describe behavior patterns on this machine - they are never compared "
            "against other people.</p>"
        )
    else:
        out.append('<p class="muted" style="font-size:13px;">No signals yet.</p>')
    out.append("</div></div>")
    return "".join(out)


def _focus_card(extras: ShellExtras) -> str:
    """FR-FOC-2: the positive metric leads; at most two observation tips follow.
    With the focus switch off only the positive metric renders (FR-FOC-5)."""
    if extras.focus is None:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Focus</span>'
        '<span class="meta">local only &middot; never shared</span></div>'
    )
    out.append(
        f'<div class="focusmetric">{count(extras.focus.longest_block_min)} min</div>'
        '<p class="muted" style="font-size:13px;margin:6px 0 0;">your longest '
        "uninterrupted block today - the positive number worth protecting.</p>"
    )
    if extras.focus_enabled and extras.tips:
        for tip_id in extras.tips:
            title, body = TIP_COPY[tip_id]
            out.append(
                f'<div class="sugg"><span class="n">&middot;</span><div>'
                f'<div class="h">{esc(title)}'
                f'<a class="dismiss" href="practicegraph:dismiss-tip-{esc(tip_id)}">'
                "Dismiss</a></div>"
                f'<div class="r">{esc(body)}</div>'
                "</div></div>"
            )
    if extras.focus.assistant_followups > 0:
        out.append(
            '<p class="muted num" style="font-size:12px;margin:14px 0 0;">'
            f"follow-ups sent within {REFLEX_GAP_MAX_S}s of an answer: "
            f"{count(extras.focus.reflex_replies)} of "
            f"{count(extras.focus.assistant_followups)} - a read-through before "
            "the next ask keeps your own judgment in the loop</p>"
        )
    if extras.focus.refire_replies > 0:
        out.append(
            '<p class="muted num" style="font-size:12px;margin:8px 0 0;">'
            f"replies within {REFIRE_WINDOW_MIN} min of a failed run: "
            f"{count(extras.focus.refire_replies)}</p>"
        )
    if extras.focus.approval_moments > 0:
        out.append(
            '<p class="muted num" style="font-size:12px;margin:8px 0 0;">'
            "long-run approvals waved through: "
            f"{count(extras.focus.waved_through)} of "
            f"{count(extras.focus.approval_moments)}</p>"
        )
    started = extras.blocks.get("block-started", 0)
    completed = extras.blocks.get("block-completed", 0)
    breaks = extras.blocks.get("break-completed", 0)
    if started or completed or breaks:
        out.append(
            f'<p class="muted num" style="font-size:12px;margin:14px 0 0;">focus '
            f"timer today - blocks started {count(started)} &middot; completed "
            f"{count(completed)} &middot; breaks taken {count(breaks)} (tray: "
            "Start a 90-minute focus block)</p>"
        )
    out.append("</div>")
    return "".join(out)


def _economy_card(reading: EconomyReading | None) -> str:
    """The economy reading (W1.1): the capability headline above the cost
    detail — window figures as KPIs, the one lever as a row, the first-open
    retrospective until dismissed, and the estimate note. Subscription mode
    leads with the weekly window share and labels dollars API-equivalent
    (AM-1). Hidden entirely below the evidence floor (withheld, not zero)."""
    if reading is None or not reading.available:
        return ""
    title = ECONOMY_COPY[f"title-{reading.billing_mode}"]
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">'
        f'{esc(ECONOMY_COPY["eyebrow"])}</span>'
        f'<span class="meta">{esc(title)} &middot; '
        f"{reading.window_days}-day window &middot; est.</span></div>"
    )
    stats: list[tuple[str, str]] = []
    if (
        reading.billing_mode == "subscription"
        and reading.week_window_pct is not None
    ):
        stats.append(
            (f"{reading.week_window_pct}%", ECONOMY_COPY["label-week-window"])
        )
    stats.append((usd(reading.cost_micro_usd), ECONOMY_COPY["label-spend"]))
    stats.append(
        (
            usd(reading.cost_per_priced_turn_micro_usd, 4),
            ECONOMY_COPY["label-per-turn"],
        )
    )
    stats.append((f"{reading.cache_hit_pct}%", ECONOMY_COPY["label-cache"]))
    if reading.reasoning is not None:
        stats.append(
            (
                f"{reading.reasoning.share_pct}%",
                ECONOMY_COPY["label-reasoning"],
            )
        )
    if reading.wow_cost_delta_pct is not None:
        delta = reading.wow_cost_delta_pct
        sign = "+" if delta > 0 else ""
        stats.append((f"{sign}{delta}%", ECONOMY_COPY["label-wow"]))
    out.append('<div class="herokpis">')
    for value, label in stats:
        out.append(
            f'<div class="kpi"><div class="k">{esc(value)}</div>'
            f'<div class="l">{esc(label)}</div></div>'
        )
    out.append("</div>")
    if reading.lever is not None:
        out.append(
            '<div class="sugg" style="margin-top:14px;">'
            '<span class="n">&middot;</span><div>'
            f'<div class="h">{esc(reading.lever.title)}</div>'
            f'<div class="r">{esc(reading.lever.body)}</div></div></div>'
        )
    if reading.retro is not None:
        out.append(
            '<div class="sugg"><span class="n">&middot;</span><div>'
            f'<div class="h">{esc(ECONOMY_COPY["retro-title"])}</div>'
            f'<div class="r">{esc(reading.retro.line)}</div></div></div>'
        )
    note_key = (
        "note-api-equivalent"
        if reading.billing_mode == "subscription"
        else "note-estimate"
    )
    out.append(
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">'
        f"{esc(ECONOMY_COPY[note_key])}</p>"
    )
    out.append("</div>")
    return "".join(out)


def _reliance_card(reading: RelianceReading | None) -> str:
    """How you worked with it: the job-shape headline and the facets that
    cleared their gates. No score, no verdict — each facet states a number and
    names both ways of reading it."""
    if reading is None or not reading.available:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">'
        f'{esc(RELIANCE_COPY["eyebrow"])}</span>'
        f'<span class="meta">{reading.window_days} days &middot; '
        "structure only</span></div>"
    )
    out.append(
        f'<p style="font-size:14px;color:var(--ink-700);margin:0 0 4px;">'
        f"{esc(reading.headline)}</p>"
    )
    out.append(
        '<p class="muted" style="font-size:12.5px;margin:0 0 14px;">'
        f'{esc(RELIANCE_COPY["intro"])}</p>'
    )
    for facet in reading.facets:
        if facet.facet_id == "engagement":
            continue
        out.append(
            f'<div class="sugg"><span class="n">&middot;</span><div>'
            f'<div class="h">{esc(facet.label)}</div>'
            f'<div class="r">{esc(facet.line)}</div>'
            f'<div class="r muted" style="font-size:12px;">{esc(facet.why)}</div>'
            "</div></div>"
        )
    out.append("</div>")
    return "".join(out)


def _calibration_card(reading: CalibrationReading | None) -> str:
    """The Calibration Mirror, results only. The static report is script-free,
    so it can never carry the probe — asking for an estimate needs an answer
    path. It shows what previous estimates did against the clock."""
    if reading is None or not reading.available or reading.last is None:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">'
        f'{esc(CALIBRATION_COPY["eyebrow"])}</span>'
        '<span class="meta">your estimate vs the clock</span></div>'
    )
    out.append(
        f'<p style="font-size:13.5px;color:var(--ink-700);margin:0;">'
        f"{esc(reading.last.line)}</p>"
    )
    if reading.summary:
        out.append(
            '<p class="muted" style="font-size:12.5px;margin:8px 0 0;">'
            f"{esc(reading.summary)}</p>"
        )
    out.append("</div>")
    return "".join(out)


def _sessions_card(reading: SessionReading | None) -> str:
    """Session grain (W2.0): the latest session's receipt and what a long
    session carries. Hidden entirely when the window holds no substantial
    session (withheld, not zero)."""
    if reading is None or not reading.available:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">'
        f'{esc(SESSION_COPY["receipt-eyebrow"])}</span>'
        '<span class="meta">your own sessions &middot; est.</span></div>'
    )
    receipt = reading.receipt
    if receipt is not None:
        out.append(
            f'<div class="h" style="font-size:13.5px;font-weight:650;">'
            f'{esc(SESSION_COPY["receipt-title"])}</div>'
            f'<p style="font-size:14px;color:var(--ink-700);margin:4px 0 0;">'
            f"{esc(receipt.line)}</p>"
        )
        for detail in receipt.detail:
            out.append(
                '<p class="muted" style="font-size:12.5px;margin:6px 0 0;">'
                f"{esc(detail)}</p>"
            )
    tax = reading.tax
    if tax is not None:
        out.append(
            '<div class="sugg" style="margin-top:14px;">'
            '<span class="n">&middot;</span><div>'
            f'<div class="h">{esc(SESSION_COPY["tax-title"])}</div>'
            f'<div class="r">{esc(tax.line)}</div>'
            f'<div class="r muted" style="font-size:12px;">{esc(tax.basis)}</div>'
            + (
                f'<div class="r muted" style="font-size:12px;">'
                f"{esc(tax.caveat)}</div>"
                if tax.caveat
                else ""
            )
            + "</div></div>"
        )
    out.append("</div>")
    return "".join(out)


_MIX_SEGMENTS = (
    ("input", "Input", "var(--teal-700)"),
    ("cached", "Cached", "var(--teal-300)"),
    ("output", "Output", "var(--teal-500)"),
    ("cache_creation", "Cache write", "var(--sage-500)"),
)


def _token_mix_card(snapshot: DailySnapshot) -> str:
    totals = {
        "input": sum(row.tokens.input for row in snapshot.rows),
        "cached": sum(row.tokens.cached for row in snapshot.rows),
        "output": sum(row.tokens.output for row in snapshot.rows),
        "cache_creation": sum(row.tokens.cache_creation for row in snapshot.rows),
    }
    grand = sum(totals.values())
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Token mix</span>'
        f'<span class="meta">{esc(compact(grand))} total</span></div>'
    )
    if grand > 0:
        out.append('<div class="mixbar">')
        for key, _label, color in _MIX_SEGMENTS:
            pct = percent(totals[key], grand)
            if pct > 0:
                out.append(f'<span style="width:{pct}%;background:{color}"></span>')
        out.append("</div>")
        out.append('<div class="mixlegend">')
        for key, label, color in _MIX_SEGMENTS:
            out.append(
                f'<div class="mixrow"><span class="dot" style="background:{color}">'
                f'</span><span class="lab">{esc(label)}</span>'
                f'<span class="val">{esc(compact(totals[key]))}</span>'
                f'<span class="pct">{percent(totals[key], grand)}%</span></div>'
            )
        out.append("</div>")
        if totals["cached"] * 2 > grand:
            out.append(
                '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">Cached '
                "reads account for most recorded tokens. Providers may bill cached "
                "input at a lower rate.</p>"
            )
        out.append('<p class="muted">Recorded reasoning is already included in output.</p>')
    else:
        out.append('<p class="muted">No token activity recorded for this day.</p>')
    out.append("</div>")
    return "".join(out)


def _usage_body(snapshot: DailySnapshot) -> str:
    visible_rows = [row for row in snapshot.rows if row.assistant_turns > 0]
    out: list[str] = []
    if visible_rows:
        out.append('<table class="usage"><thead><tr>')
        out.append(
            "<th>Tool</th><th>Model</th>"
            '<th class="n">Turns</th><th class="n">Input</th><th class="n">Cached</th>'
            '<th class="n">Output</th><th class="n">Cost</th></tr></thead><tbody>'
        )
        for row in visible_rows:
            out.append(
                f"<tr><td>{esc(row.tool)}</td>"
                f'<td class="model">{esc(row.model)}</td>'
                f'<td class="n">{esc(count(row.assistant_turns))}</td>'
                f'<td class="n">{esc(count(row.tokens.input))}</td>'
                f'<td class="n">{esc(count(row.tokens.cached))}</td>'
                f'<td class="n">{esc(count(row.tokens.output))}</td>'
                f'<td class="n">{esc(usd(row.cost_micro_usd, 4))}</td></tr>'
            )
        out.append("</tbody></table>")
    else:
        out.append('<p class="muted">No assistant activity recorded for this day.</p>')
    out.append(
        '<p class="muted num" style="font-size:12px;margin:14px 0 0;">'
        f"tool calls {esc(count(snapshot.total_tool_calls))} &middot; "
        f"interruptions {esc(count(snapshot.total_interruptions))} &middot; "
        f"retries {esc(count(snapshot.total_retries))} &middot; "
        f"user turns {esc(count(snapshot.total_user_turns))}</p>"
    )
    return "".join(out)


def _usage_card(snapshot: DailySnapshot) -> str:
    return (
        '<div class="card">'
        '<div class="sec-head"><span class="eyebrow">Usage by tool and model</span>'
        '<span class="meta">assistant turns &middot; est. cost</span></div>'
        f"{_usage_body(snapshot)}</div>"
    )


def _source_rows(snapshot: DailySnapshot, with_counters: bool) -> str:
    out: list[str] = []
    for row in snapshot.source_health:
        health = row.health
        if health.seen == 0:
            tag = '<span class="tag quiet">no logs found</span>'
            meta = "source paused - activity appears automatically once logs exist"
        elif health.drift_detected:
            tag = '<span class="tag emerging">format drift</span>'
            meta = (
                f"{count(health.seen)} records - {count(health.parsed)} parsed - "
                f"{count(health.malformed)} malformed - "
                f"{count(health.unsupported)} unrecognized - "
                f"{count(health.unknown_field)} unfamiliar fields"
                if with_counters
                else f"{count(health.seen)} records - {count(health.parsed)} parsed"
            )
        else:
            tag = '<span class="tag steady">parsed</span>'
            meta = f"{count(health.seen)} records - {count(health.parsed)} parsed"
        out.append(
            f'<div class="srcrow"><span class="nm">{esc(row.source_id)}</span>'
            f'<span class="meta">{esc(meta)} &middot; parser v{row.parser_version}</span>'
            f"{tag}</div>"
        )
    return "".join(out)


def _source_health_quiet(snapshot: DailySnapshot) -> str:
    detected = sum(1 for row in snapshot.source_health if row.health.seen > 0)
    total = len(snapshot.source_health)
    drift = any(row.health.drift_detected for row in snapshot.source_health)
    note = ""
    if drift:
        note = (
            '<p class="muted" style="font-size:12.5px;margin:10px 0 0;">Some records '
            "were not recognized. Parsing continued and skipped them safely; an agent "
            "update may be available. No log content was recorded.</p>"
        )
    return (
        '<details class="quiet"><summary>'
        f'<span class="chev">{CHEVRON_SVG}</span>'
        '<span class="eyebrow">Source health</span>'
        f'<span style="font-family:var(--mono);font-size:11px;'
        f'color:var(--ink-400);">{detected} of {total} local tools parsed</span>'
        "</summary>"
        f'<div class="body">{_source_rows(snapshot, with_counters=False)}{note}</div>'
        "</details>"
    )


HEATMAP_DAYS = 182  # 26 weeks (named constant)

_HEAT_RAMP = ("#dbece7", "#82bcae", "#2f8576", "#1b574b")
_HEAT_EMPTY = "#efe9df"
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _heatmap_svg(series: list[DayPoint]) -> str:
    """GitHub-style token-activity calendar (inline SVG, integer math)."""
    if not series or not any(p.tokens_total > 0 for p in series):
        return ""
    max_tokens = max(p.tokens_total for p in series)
    cell, gap = 11, 2
    step = cell + gap
    pad_left, pad_top = 8, 16
    first_weekday = date.fromisoformat(series[0].day).weekday()
    columns = (first_weekday + len(series) + 6) // 7
    width = pad_left + columns * step
    height = pad_top + 7 * step
    parts = [
        f'<svg class="heatmap" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" role="img" aria-label="Daily token activity, '
        'last 26 weeks">'
    ]
    for index, point in enumerate(series):
        slot = first_weekday + index
        column, row = divmod(slot, 7)
        x = pad_left + column * step
        y = pad_top + row * step
        if point.tokens_total <= 0:
            color = _HEAT_EMPTY
        else:
            bucket = max(1, min(4, point.tokens_total * 4 // max_tokens or 1))
            color = _HEAT_RAMP[bucket - 1]
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" '
            f'fill="{color}"><title>{esc(point.day)}: '
            f"{esc(compact(point.tokens_total))} tokens</title></rect>"
        )
        day_of_month = int(point.day[8:10])
        if day_of_month == 1:
            month = _MONTH_ABBR[int(point.day[5:7]) - 1]
            parts.append(
                f'<text x="{x}" y="{pad_top - 5}" style="font:9px var(--mono);'
                f'fill:var(--ink-400);">{month}</text>'
            )
    parts.append("</svg>")
    legend = "".join(
        f'<i style="background:{color}"></i>' for color in (_HEAT_EMPTY, *_HEAT_RAMP)
    )
    return (
        "".join(parts)
        + '<div class="heatlegend"><span>less</span>'
        + legend
        + "<span>more</span></div>"
    )


def _activity_block(extras: ShellExtras) -> str:
    profile = extras.profile
    if profile is None or profile.active_days_total == 0:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Activity</span>'
        '<span class="meta">all local history &middot; UTC accounting days</span></div>'
    )
    peak_label = (
        f"{compact(profile.peak_day_tokens)} on {profile.peak_day}"
        if profile.peak_day
        else "-"
    )
    # No streak chips by design: displaying unbroken-daily-usage runs turns
    # activity volume into an achievement (a documented dark pattern). Active
    # days is the neutral fact; rest days are never framed as a loss.
    stats = (
        (compact(profile.lifetime_tokens), "lifetime tokens"),
        (usd(profile.lifetime_cost_micro_usd), "lifetime est. spend"),
        (peak_label, "peak day"),
        (f"{count(profile.longest_block_min)} min", "longest block (90d)"),
        (count(profile.active_days_total), "active days"),
    )
    out.append('<div class="herokpis" style="margin-bottom:16px;">')
    for value, label in stats:
        out.append(
            f'<div class="kpi"><div class="k">{esc(value)}</div>'
            f'<div class="l">{esc(label)}</div></div>'
        )
    out.append("</div>")
    heatmap = _heatmap_svg(extras.heatmap)
    if heatmap:
        out.append(heatmap)
    out.append("</div>")
    return "".join(out)


def _rhythm_card(extras: ShellExtras) -> str:
    """Behavioral observations (FR-FOC-8 language rules apply and
    are lexicon-scanned): how the work is paced, never a judgment, never
    shared (NFR-PRV-6)."""
    rhythm = extras.rhythm
    if rhythm is None or rhythm.events_total == 0 or not extras.focus_enabled:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Working rhythm</span>'
        f'<span class="meta">last {rhythm.window_days} days &middot; local only'
        "</span></div>"
    )
    velocity = rhythm.turns_per_active_hour_tenths
    quiet_value = (
        f"{rhythm.quiet_hours_activity_pct}%"
        if rhythm.quiet_hours_activity_pct is not None
        else "preferred schedule not confirmed"
    )
    outside_value = (
        f"{rhythm.outside_preferred_hours_pct}%"
        if rhythm.outside_preferred_hours_pct is not None
        else "preferred schedule not confirmed"
    )
    off_schedule_value = (
        f"{rhythm.off_schedule_day_pct}%"
        if rhythm.off_schedule_day_pct is not None
        else "preferred schedule not confirmed"
    )
    stats = (
        (quiet_value, "activity during confirmed quiet hours"),
        (outside_value, "activity outside preferred hours"),
        (off_schedule_value, "activity on non-preferred working days"),
        (f"{count(rhythm.deep_block_days)}", "days with a 45+ min deep block"),
        (f"{count(rhythm.long_streak_days)}", "days with 2h+ and no 15-min pause"),
        (f"{velocity // 10}.{velocity % 10}", "events per active hour"),
        (f"{count(rhythm.active_days)}", "active days"),
        # P3 environment cardinality: descriptive context, never a finding.
        (f"{count(rhythm.distinct_projects)}", "projects touched"),
        (f"{count(rhythm.distinct_branches)}", "branches touched"),
        # P4 accumulated response wait: informational context, never a score.
        (duration_hm(rhythm.waiting_minutes), "waiting on responses"),
    )
    out.append('<div class="herokpis">')
    for value, label in stats:
        out.append(
            f'<div class="kpi"><div class="k">{esc(value)}</div>'
            f'<div class="l">{esc(label)}</div></div>'
        )
    out.append("</div>")
    out.append(
        '<p class="muted" style="font-size:12.5px;margin:16px 0 0;">Observations, '
        "not judgments: if the pattern is not how you want to work, the break "
        "nudge and focus tips are already on it. This never leaves your machine "
        "and is never compared against anyone.</p>"
    )
    out.append("</div>")
    return "".join(out)


def _range_selector(active: str) -> str:
    """Today / 7 days / 30 days / All time — CSS-only :target switching, the
    same accepted mechanism as the tabs (FR-RPT-2)."""
    items = [("today", "Today")] + [
        (range_id, label) for range_id, label, _days in RANGE_KINDS
    ]
    out = ['<nav class="ranges" aria-label="Report range">']
    for range_id, label in items:
        marker = ' class="on"' if range_id == active else ""
        out.append(f'<a{marker} href="#{range_id}">{esc(label)}</a>')
    out.append("</nav>")
    return "".join(out)


_BAR_FILL = "#2f8576"
_BAR_EMPTY = "#e5ddcd"


def _bar_axis_label(range_id: str, index: int, total: int, label: str) -> str | None:
    if range_id == "range-week":
        return label[8:10]
    if range_id == "range-month":
        return label[8:10] if index % 7 == 0 or index == total - 1 else None
    month = _MONTH_ABBR[int(label[5:7]) - 1]
    if total > 14 and index % 2 == 1:
        return None
    return f"{month} {label[2:4]}" if month == "Jan" or index == 0 else month


def _range_bars(summary: RangeSummary) -> str:
    series = summary.series
    if not series or all(cost == 0 for _label, cost in series):
        return ""
    total = len(series)
    max_cost = max(cost for _label, cost in series)
    width = 620
    gap = 2 if total > 20 else 5
    bar_width = max(2, (width - gap * total) // total)
    step = bar_width + gap
    parts = [
        f'<svg width="100%" height="66" viewBox="0 0 {width} 66" '
        'preserveAspectRatio="none" role="img" aria-label="estimated spend '
        'per period">'
    ]
    for index, (label, cost) in enumerate(series):
        height = max(2, cost * 44 // max_cost) if cost > 0 else 1
        x = index * step
        fill = _BAR_FILL if cost > 0 else _BAR_EMPTY
        parts.append(
            f'<rect x="{x}" y="{48 - height}" width="{bar_width}" '
            f'height="{height}" rx="1" fill="{fill}">'
            f"<title>{esc(label)}: {esc(usd(cost))}</title></rect>"
        )
        axis = _bar_axis_label(summary.range_id, index, total, label)
        if axis:
            parts.append(
                f'<text x="{x}" y="62" style="font:8.5px var(--mono);'
                f'fill:var(--ink-400);">{esc(axis)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _range_panel(summary: RangeSummary) -> str:
    """One range view: header figures, the by-tool split (a quiet lane keeps
    its last-active day instead of vanishing), top models, spend bars."""
    out = [f'<section class="panel stack" id="{summary.range_id}">']
    out.append(_range_selector(summary.range_id))
    if summary.active_days == 0:
        out.append(
            '<div class="card"><p class="muted" style="font-size:13.5px;">No '
            "activity recorded in this range yet.</p></div></section>"
        )
        return "".join(out)

    out.append('<div class="card">')
    out.append(
        f'<div class="sec-head"><span class="eyebrow">API-equivalent usage &middot; '
        f"{esc(summary.label)}</span>"
        f'<span class="meta">{esc(summary.from_day)} &rarr; {esc(summary.to_day)} '
        "(UTC)</span></div>"
    )
    out.append(
        f'<div class="figure" style="font-family:var(--mono);font-size:32px;'
        f'font-weight:600;letter-spacing:-.02em;line-height:1;">'
        f"{esc(usd(summary.cost_micro_usd))}{est_pill()}</div>"
    )
    out.append('<p class="muted">At listed API rates. Subscription amounts are comparisons, '
               'not charges. Your bill may differ.</p>')
    out.append('<details class="quiet"><summary>Usage details for this period</summary>')
    out.append('<div class="herokpis" style="margin-top:18px;">')
    for value, label in (
        (compact(summary.tokens_total), "tokens"),
        (count(summary.assistant_turns), "assistant turns"),
        (count(summary.active_days), "active days"),
    ):
        out.append(
            f'<div class="kpi"><div class="k">{esc(value)}</div>'
            f'<div class="l">{esc(label)}</div></div>'
        )
    out.append("</div>")
    bars = _range_bars(summary)
    if bars:
        out.append('<div style="margin-top:18px;">' + bars + "</div>")
    if summary.unpriced_turns:
        out.append(
            f'<p class="muted num" style="font-size:12px;margin:12px 0 0;">'
            f"{esc(count(summary.unpriced_turns))} turns used models missing from "
            "the rate card - their cost is not included.</p>"
        )
    out.append("</details></div>")

    out.append('<div class="card">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">By tool</span>'
        '<span class="meta">share of est. spend</span></div><div class="bars">'
    )
    for split in summary.tools:
        out.append(
            f'<div class="bar"><span class="lab">{esc(split.tool)}</span>'
            f'<span class="track"><span class="fill" '
            f'style="width:{split.share_pct}%"></span></span>'
            f'<span class="pct">{split.share_pct}%</span></div>'
            f'<div class="toolmeta">{esc(usd(split.cost_micro_usd))} &middot; '
            f"{esc(count(split.assistant_turns))} turns &middot; "
            f"{esc(compact(split.tokens_total))} tokens &middot; top model "
            f"{esc(split.top_model)} &middot; last active "
            f"{esc(split.last_active)}</div>"
        )
    out.append("</div></div>")

    if summary.models:
        out.append('<div class="card">')
        out.append(
            '<div class="sec-head"><span class="eyebrow">Top models</span>'
            '<span class="meta">by est. cost</span></div>'
        )
        out.append('<table class="usage"><thead><tr>')
        out.append(
            "<th>Tool</th><th>Model</th>"
            '<th class="n">Turns</th><th class="n">Tokens</th>'
            '<th class="n">Cost</th></tr></thead><tbody>'
        )
        for row in summary.models:
            out.append(
                f"<tr><td>{esc(row.tool)}</td>"
                f'<td class="model">{esc(row.model)}</td>'
                f'<td class="n">{esc(count(row.assistant_turns))}</td>'
                f'<td class="n">{esc(compact(row.tokens_total))}</td>'
                f'<td class="n">{esc(usd(row.cost_micro_usd))}</td></tr>'
            )
        out.append("</tbody></table></div>")
    out.append("</section>")
    return "".join(out)


def _brief_card(extras: ShellExtras) -> str:
    """The verdict (FR-RPT-1 Today): celebrate first (FR-FOC-2's rule,
    promoted to the page), one flag, one executable next step. Assembled from
    the closed template catalogs in report/brief.py — never free text."""
    brief = extras.brief
    if brief is None:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Daily brief</span>'
        f'<span class="meta">{esc(brief.cost_line)}</span></div>'
    )
    flag = f" {esc(brief.flag)}" if brief.flag else ""
    out.append(f'<p class="verdict">{esc(brief.celebrate)}{flag}</p>')
    if brief.weekly_line:
        out.append(f'<p class="weekline">{esc(brief.weekly_line)}</p>')
    out.append(
        '<div class="acts">'
        f'<a class="act primary" href="{esc(brief.action_href)}">&#9654;&nbsp; '
        f"{esc(brief.action_label)}</a>"
        '<a class="act quiet" href="#insights">See the evidence</a>'
        "</div>"
    )
    out.append(_rate_row())
    out.append(
        '<div class="assembled">assembled from closed local counters &middot; '
        "deterministic &middot; nothing here was sent anywhere</div>"
    )
    out.append("</div>")
    return "".join(out)


def _observation_card(extras: ShellExtras) -> str:
    observation = extras.practice_observation
    return (
        f'<div class="card" data-observation-id="{esc(observation.observation_id)}">'
        '<div class="sec-head"><span class="eyebrow">Work-pattern observation</span>'
        f'<span class="meta">{esc(observation.confidence)} &middot; '
        f'{esc(observation.period)}</span></div>'
        f'<p class="verdict">{esc(observation.title)}</p>'
        f'<p class="weekline">{esc(observation.body)}</p>'
        f'<p class="muted" style="font-size:12px;margin:14px 0 0;">'
        f'{esc(observation.caveat)}</p></div>'
    )


def _rate_row() -> str:
    """One-click daily self-rating (the perception-check input): protocol
    links dispatch to the shell, which records through the CLI (C-4). The
    page itself stays static and script-free (FR-RPT-5)."""
    pills = "".join(
        f'<a href="practicegraph:checkin-{rating}">{rating}</a>'
        for rating in range(1, 6)
    )
    return (
        '<div class="rate"><span class="q">How did today feel?</span>'
        f"{pills}"
        '<span class="hint">1 = rough &middot; 5 = great &middot; one click, '
        "local only</span></div>"
    )


_RX_LABELS = {
    "deep_work": "Rx: protect the first block",
    "working_pattern": "Rx: 90/10 cadence",
    "single_threading": "Rx: parallel agents, one task",
    "context_hygiene": "Rx: trim the working set",
    "model_economy": "Rx: route routine mid-tier",
    "execution_quality": "Rx: read the error before you paste it",
}

_SPARK_STROKE = {"up": "#2f7d57", "down": "#b0722a", "flat": "#97a09a"}


def _trajectory_card(extras: ShellExtras) -> str:
    """The practice spine: 12 weekly scores per dimension as trajectories —
    direction against your own history, not a fill-to-100 gauge."""
    profile = extras.performance
    if profile is None or not extras.weekly:
        return ""
    weekly_scores = [
        dimension_scores(window) if window.active_days > 0 else None
        for window in extras.weekly
    ]
    weeks_with_data = sum(1 for scores in weekly_scores if scores is not None)
    if weeks_with_data < 2:
        return ""
    slots = len(weekly_scores)
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Your practice &middot; '
        f"{slots} weeks</span>"
        '<span class="meta">scored against your own history only</span></div>'
    )
    for reading in profile.readings:
        points = []
        for index, scores in enumerate(weekly_scores):
            if scores is None:
                continue
            x = 2 + index * 128 // max(1, slots - 1)
            y = 25 - scores[reading.dimension_id] * 22 // 100
            points.append(f"{x},{y}")
        if reading.delta is None or reading.delta == 0:
            direction, arrow, delta_label = "flat", "&#8594;", "0"
        elif reading.delta > 0:
            direction, arrow, delta_label = "up", "&#9650;", f"+{reading.delta}"
        else:
            direction, arrow, delta_label = "down", "&#9660;", str(reading.delta)
        spark = (
            f'<svg width="132" height="28" viewBox="0 0 132 28" role="img" '
            f'aria-label="{esc(DIMENSION_LABELS[reading.dimension_id])} trend">'
            f'<polyline fill="none" stroke="{_SPARK_STROKE[direction]}" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round" '
            f'points="{" ".join(points)}"></polyline></svg>'
            if len(points) >= 2
            else ""
        )
        rx = ""
        if reading.score < STRONG_MIN_SCORE and not reading.neutral:
            rx = (
                f' <a class="rx" href="#insights">'
                f"{esc(_RX_LABELS[reading.dimension_id])}</a>"
            )
        out.append(
            '<div class="dim"><div class="row">'
            f'<span class="lab">{esc(DIMENSION_LABELS[reading.dimension_id])}</span>'
            f"<span>{spark}</span>"
            f'<span class="score {direction}">{reading.score}'
            f"<small>{arrow} {esc(delta_label)}</small></span>"
            f'<span class="why">{esc(reading.evidence[0])}{rx}</span>'
            "</div></div>"
        )
    out.append("</div>")
    return "".join(out)


_RIBBON_TRACK = "#efe9df"
_RIBBON_BLOCK = "#2f8576"
_RIBBON_DEEP = "#1b574b"
_RIBBON_TICK = "#b0722a"


def _day_ribbon(
    day: date,
    marks: list[MarkRow],
    schedule: ScheduleProfile | None = None,
) -> str:
    """The day's local wall-clock shape: activity segments (dark = a 45+ min deep
    block), amber ticks where sessions alternated within 15 minutes. The
    geometry comes from the shared `day_ribbon_shape` helper — the same one
    the view-model serves to the interactive app (one implementation)."""
    if not marks:
        return ""
    schedule = schedule or compatibility_utc_schedule()
    shape = day_ribbon_shape(day, marks, schedule)
    parts = [
        f'<svg width="100%" height="44" viewBox="0 0 {RIBBON_SCALE} 44" '
        'preserveAspectRatio="none" role="img" '
        f'aria-label="today&#39;s activity timeline ({esc(schedule.timezone_name)} '
        'local time)">',
        f'<rect x="0" y="4" width="{RIBBON_SCALE}" height="20" rx="4" '
        f'fill="{_RIBBON_TRACK}"></rect>',
    ]
    for x, w in shape.segments:
        parts.append(
            f'<rect x="{x}" y="8" width="{w}" height="12" rx="2" '
            f'fill="{_RIBBON_BLOCK}"></rect>'
        )
    for x, w in shape.deep:
        parts.append(
            f'<rect x="{x}" y="8" width="{w}" height="12" rx="2" '
            f'fill="{_RIBBON_DEEP}"></rect>'
        )
    for tick in shape.ticks:
        parts.append(
            f'<rect x="{tick}" y="4" width="2" height="20" '
            f'fill="{_RIBBON_TICK}" opacity="0.55"></rect>'
        )
    for x, label in ((0, "00"), (247, "06"), (497, "12"), (747, "18"), (978, "24")):
        parts.append(
            f'<text x="{x}" y="40" style="font:9px var(--mono);'
            f'fill:var(--ink-400);">{label}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _shape_card(snapshot: DailySnapshot, extras: ShellExtras | None) -> str:
    """Daily accounting with activity counts confined to optional details."""
    missing = (
        f'<p>Incomplete estimate: {count(snapshot.total_unpriced_turns)} turns '
        'have no listed price.</p>'
        if snapshot.total_unpriced_turns else ""
    )
    return (
        '<div class="card"><div class="sec-head">'
        '<span class="eyebrow">Usage at listed rates</span>'
        f'<span class="meta">UTC accounting day {esc(snapshot.day.isoformat())}</span></div>'
        f'<p class="verdict">{esc(usd(snapshot.total_cost_micro_usd))} API-equivalent usage</p>'
        '<p class="muted">Subscription amounts are comparisons, not charges. '
        'Your bill may differ.</p>'
        f'{missing}<details class="quiet"><summary>Recorded usage details</summary>'
        '<div class="body"><p>Includes recorded agent and subagent usage. '
        'Activity counts do not establish completed work.</p>'
        f'<p>{count(snapshot.session_count)} sessions; '
        f'{count(snapshot.total_assistant_turns)} assistant turns.</p>'
        f'{_usage_body(snapshot)}</div></details></div>'
    )


def _change_queue_card(extras: ShellExtras) -> str:
    """The ranked change queue from the shared view-model: evidence as
    sentences, dismissal as a button (protocol link) — never CLI strings on
    a human surface (INTERACTIVE_UI_PLAN.md item 3)."""
    entries = queue_entries(extras)
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">If you change one thing</span>'
        '<span class="meta">ranked by estimated impact</span></div>'
    )
    if not entries:
        out.append(
            '<p class="muted" style="font-size:13.5px;">Nothing queued - usage '
            "looks healthy for this day.</p>"
        )
    for index, entry in enumerate(entries, start=1):
        dismiss = ""
        if entry["dismiss_id"]:
            dismiss = (
                f'<a class="dismiss" href="practicegraph:dismiss-suggestion-'
                f'{esc(str(entry["dismiss_id"]))}">Dismiss</a>'
            )
        out.append(
            f'<div class="qrow"><span class="n">{index:02d}</span><div>'
            f'<div class="h">{esc(str(entry["title"]))}{dismiss}</div>'
            f'<div class="r">{esc(str(entry["body"]))}</div>'
            f'<div class="why">{esc(str(entry["evidence"]))}</div>'
            "</div></div>"
        )
    out.append("</div>")
    return "".join(out)


_PERF_TAG = {"strong": "leading", "steady": "steady", "building": "emerging"}


def _performance_card(extras: ShellExtras) -> str:
    """Human-performance profile: six behavioral dimensions from local
    heuristics. Same privacy stance as focus — local only, never shared,
    scored against your own history (NFR-PRV-6, FR-FOC-8 copy rules)."""
    profile = extras.performance
    if profile is None:
        return ""
    out = ['<div class="card">']
    out.append(
        '<div class="sec-head"><span class="eyebrow">Performance profile</span>'
        f'<span class="meta">last {profile.window_days} days &middot; '
        f"{count(profile.active_days)} active &middot; local only</span></div>"
    )
    if profile.delta_vs_prior is None:
        delta_html = '<span class="tag quiet">first scored window</span>'
    else:
        sign = "+" if profile.delta_vs_prior > 0 else ""
        tag = "steady" if profile.delta_vs_prior > 0 else "quiet"
        delta_html = (
            f'<span class="tag {tag}">{sign}{profile.delta_vs_prior} vs prior '
            f"{profile.window_days} days</span>"
        )
    out.append(
        '<div class="perfhead">'
        f'<div class="focusmetric" style="color:var(--teal-700);">'
        f"{profile.overall}</div>"
        f'<div><div style="font-size:13px;font-weight:600;color:var(--ink-700);">'
        f"overall &middot; {esc(level_for(profile.overall))}</div>"
        f'<div style="margin-top:5px;">{delta_html}</div></div>'
        "</div>"
    )
    for reading in profile.readings:
        evidence = " &middot; ".join(esc(part) for part in reading.evidence)
        out.append(
            '<div class="perf"><div class="row">'
            f'<span class="lab">{esc(DIMENSION_LABELS[reading.dimension_id])}</span>'
            f'<span class="track"><span class="fill" '
            f'style="width:{reading.score}%"></span></span>'
            f'<span class="num">{reading.score}</span>'
            f'<span class="tag {_PERF_TAG[reading.level]}">{esc(reading.level)}'
            "</span>"
            f'</div><div class="ev">{evidence}</div></div>'
        )
    out.append(
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">Scored by '
        "closed formulas over local counters - deep blocks, pauses, verified "
        "human workstream movement, cache share, model mix, command outcomes. "
        "Measured against "
        "your own history only, never other people, and never sent anywhere.</p>"
    )
    out.append("</div>")
    return "".join(out)


_SEVERITY_TAG = {"info": "quiet", "opportunity": "emerging", "attention": "emerging"}


def _insights_panel(
    snapshot: DailySnapshot, rate_card_version: str, extras: ShellExtras | None
) -> str:
    """Accounting diagnostics and curated practices, without personal scores."""
    out = ['<section class="panel stack" id="insights">']
    out.append(
        '<p class="lede">The evidence behind the brief. Every number is computed '
        "locally from your own counters &mdash; nothing here was sent anywhere.</p>"
    )
    if extras is None:
        out.append(
            '<div class="card"><p class="muted" style="font-size:13.5px;">Initialize '
            "the agent (<code>practicegraph init</code>) to unlock local insights."
            "</p></div></section>"
        )
        return "".join(out)

    out.append('<details class="card quiet"><summary>Accounting details</summary>'
               '<div class="body">')
    out.append('<p>All amounts use listed API rates; subscription amounts are comparisons, '
               'not charges. Counts include recorded agent activity.</p>')
    out.append(_token_mix_card(snapshot))
    out.append(_usage_card(snapshot))
    out.append('</div></details>')
    if extras.economy is not None and extras.economy.lever is not None:
        lever = extras.economy.lever
        out.append(
            '<div class="card"><div class="sec-head"><span class="eyebrow">Usage driver</span>'
            f'<span class="meta">{extras.economy.window_days} days ending '
            f'{esc(snapshot.day.isoformat())} (UTC)</span></div>'
            f'<p>{esc(lever.title)}</p><p>{esc(lever.body)}</p></div>'
        )
    reliance = _reliance_card(extras.reliance)
    if reliance:
        out.append('<details class="quiet"><summary>Interaction details</summary>'
                   f'<div class="body">{reliance}</div></details>')
    calibration = _calibration_card(extras.calibration)
    if calibration:
        out.append('<details class="quiet"><summary>Reflect on a recorded session</summary>'
                   f'<div class="body">{calibration}</div></details>')

    if extras.practices:
        out.append('<div class="card">')
        out.append(
            '<div class="sec-head"><span class="eyebrow">Field guide</span>'
            '<span class="meta">curated practices &middot; catalog artifact</span>'
            "</div>"
        )
        for practice, relevant in extras.practices:
            badge = (
                ' <span class="tag emerging">relevant now</span>' if relevant else ""
            )
            out.append(
                f'<div class="sugg"><span class="n">&middot;</span><div>'
                f'<div class="h">{esc(practice.title)}{badge}</div>'
                f'<div class="r">{esc(practice.body)}</div></div></div>'
            )
        out.append("</div>")

    total_sessions = sum(extras.work_mix.values())
    if total_sessions > 0:
        out.append('<div class="card">')
        out.append(
            '<div class="sec-head"><span class="eyebrow">Where your sessions went'
            '</span><span class="meta">by work type &middot; structural</span></div>'
            '<div class="bars">'
        )
        for work_type, sessions in extras.work_mix.items():
            if sessions == 0:
                continue
            pct = percent(sessions, total_sessions)
            fill_class = "fill q" if work_type == "unknown" else "fill"
            out.append(
                f'<div class="bar"><span class="lab">'
                f"{esc(WORK_TYPE_LABELS[work_type])}</span>"
                f'<span class="track"><span class="{fill_class}" '
                f'style="width:{pct}%"></span></span>'
                f'<span class="pct">{pct}%</span></div>'
            )
        out.append("</div>")
        out.append(
            '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">Classified '
            "from event structure only (tool calls, retries, interruptions) - never "
            "from content.</p></div>"
        )
    out.append("</section>")
    return "".join(out)


# Tier -> existing design-tag class (design.py closed tag set; the tier word
# itself carries the meaning, the class only sets the visual register).
_TIER_TAG: dict[str, str] = {
    "use": "leading",
    "try": "emerging",
    "watch": "steady",
    "wait": "quiet",
    "avoid": "steady",
}


def _advisor_measurement(item: CitedMeasurement) -> str:
    labels = {
        "released_on": "Released", "coding_index_tenths": "Coding Index",
        "agentic_index_tenths": "Agentic Index", "price_in_micro": "Input price",
        "price_out_micro": "Output price", "price_cache_read_micro": "Cached-input price",
        "median_tps_tenths": "Output speed", "ttft_ms": "Time to first token",
        "context_window": "Context window",
    }
    value = str(item.value)
    if isinstance(item.value, int):
        if item.metric.startswith("price_"):
            amount = f"{item.value // 1_000_000}.{item.value % 1_000_000:06d}".rstrip("0")
            value = f"${amount.rstrip('.')} / 1M tokens"
        elif item.metric.endswith("_tenths"):
            value = f"{item.value // 10}.{item.value % 10}"
            if item.metric == "median_tps_tenths":
                value += " tokens/s"
        elif item.metric == "ttft_ms":
            value += " ms"
        elif item.metric == "context_window":
            value = f"{item.value:,} tokens"
    source = esc(item.source_name)
    if safe_evidence_url(item.source_url):
        source = (f'<a href="{esc(item.source_url)}" target="_blank" '
                  f'rel="noopener noreferrer">{source}</a>')
    observation = (f"observed {esc(item.observed_on)}" if item.observed_on
                   else "observation date not recorded")
    return (
        f"{esc(item.model_id)} · {esc(labels.get(item.metric, item.metric))}: {esc(value)}"
        f" · {source} · {observation} · {esc(item.basis)}"
    )


def _advisor_card(board: AdvisorBoard) -> str:
    """The call: at most three verdict takes, receipts before market lines.
    Reuses the .sugg row primitives — no new CSS surface."""
    if board.market_state == "fresh":
        market_meta = f"market data {esc(board.market_as_of)}"
    elif board.market_state == "partial":
        market_meta = f"partial market data · reviewed {esc(board.market_as_of)}"
    elif board.market_state == "stale":
        market_meta = f"market unverified since {esc(board.market_as_of)}"
    else:
        market_meta = "market data absent"
    out = [
        '<div class="card">'
        '<div class="sec-head"><span class="eyebrow">If you change one thing</span>'
        f'<span class="meta">ranked by estimated impact &middot; {market_meta}'
        "</span></div>"
    ]
    if board.audit:
        out.append(
            f'<p style="font-size:12px;color:var(--teal-800);margin:0 0 10px;">'
            f"{esc(board.audit)}</p>"
        )
    for take in board.takes:
        tag = _TIER_TAG.get(take.tier, "quiet")
        lines: list[str] = [f"<em>{esc(take.boundary)}</em>"]
        lines.extend(esc(line) for line in take.receipts)
        if take.market:
            lines.append(esc(take.market))
        if take.steelman:
            lines.append(esc(take.steelman))
        lines.append(f"<b>Do:</b> {esc(take.action)}")
        if take.experiment:
            lines.append(f"<b>Try:</b> {esc(take.experiment)}")
        if take.expires:
            lines.append(f"re-verdict by {esc(take.expires)}")
        # Last line, always present: what had to be assumed to get here.
        lines.append(
            f"<b>How sure:</b> {esc(take.confidence)} &mdash; "
            f"{esc(take.confidence_note)}"
        )
        if take.attribution:
            lines.append(esc(take.attribution))
        if take.evidence:
            lines.append(
                '<details><summary>Sources and measurements</summary><ul>'
                + "".join(f"<li>{_advisor_measurement(item)}</li>" for item in take.evidence)
                + "</ul></details>"
            )
        body = "<br>".join(lines)
        out.append(
            f'<div class="sugg"><span class="n">&middot;</span><div>'
            f'<div class="h">{esc(take.verdict)} '
            f'<span class="tag {tag}">{esc(take.tier)}</span></div>'
            f'<div class="r">{body}</div></div></div>'
        )
    out.append("</div>")
    return "".join(out)


def _sources_panel(snapshot: DailySnapshot) -> str:
    return (
        '<section class="panel stack" id="sources">'
        '<p class="lede">Which local tools this report was built from. Logs are read '
        "from your machine and parsed in place &mdash; raw contents never leave it.</p>"
        f'<div class="card">{_source_rows(snapshot, with_counters=True)}</div>'
        '<details class="quiet"><summary>'
        f'<span class="chev">{CHEVRON_SVG}</span>'
        '<span class="eyebrow">What a parse reads</span></summary>'
        '<div class="body"><p class="muted" style="font-size:13px;margin:6px 0 0;">'
        "Only closed counters: token totals, model names, timestamps, tool-call and "
        "retry counts, and boolean presence flags. Prompt text, code, file paths, and "
        "identity are read transiently to count and are never stored or emitted.</p>"
        "</div></details>"
        "</section>"
    )


def _skills_panel(extras: ShellExtras | None) -> str:
    out = ['<section class="panel stack" id="skills">']
    out.append(
        '<p class="lede">Behavioral maturity signals, computed locally from closed '
        "counters &mdash; never compared against other people.</p>"
    )
    if extras is not None and extras.maturity:
        out.append('<div class="card"><div class="chips">')
        for signal_id, level in extras.maturity:
            out.append(
                f'<span class="chip"><span class="t">'
                f"{esc(MATURITY_LABELS.get(signal_id, signal_id))}</span>"
                f'<span class="tag {_LEVEL_TAG[level.value]}">'
                f"{esc(_LEVEL_LABEL[level.value])}</span></span>"
            )
        out.append("</div></div>")
    else:
        out.append(
            '<div class="card"><p class="muted" style="font-size:13.5px;">Nothing to '
            "show yet in this version.</p></div>"
        )
    out.append("</section>")
    return "".join(out)


def _privacy_panel(privacy: PrivacyStatus) -> str:
    out = ['<section class="panel stack" id="privacy">']

    out.append(
        '<div class="card"><div class="privacy-hero">'
        f'<span class="shield">{SHIELD_SVG}</span>'
        '<div><h2 style="margin:0 0 6px;font-size:19px;color:var(--ink-900);">'
        "Nothing leaves this machine without your say-so</h2>"
        '<p class="muted" style="font-size:14px;margin:0;max-width:560px;">Raw '
        "prompts, code, file paths, and identity are never emitted. If you opt in, "
        "the only thing sent is the closed aggregate counter payload below &mdash; "
        "no content, no identity.</p></div></div>"
        '<div class="pill-row" style="margin-top:18px;">'
        '<span class="factpill">content_present = false</span>'
        '<span class="factpill">identity_present = false</span>'
        '<span class="factpill">aggregates only</span>'
        "</div></div>"
    )

    if privacy.consent_enabled:
        decided = (
            f" &middot; decided {esc(privacy.consent_decided_at)}"
            if privacy.consent_decided_at
            else ""
        )
        out.append(
            '<div class="consent on"><span style="font-size:13.5px;">Sharing is '
            f'<span class="state">opted in</span>{decided} &middot; revoke any time '
            "with <code>practicegraph consent off</code>; nothing more is sent from "
            "that moment.</span></div>"
        )
    else:
        out.append(
            '<div class="consent off"><span style="font-size:13.5px;">Sharing is '
            '<span class="state">OFF (default)</span> &middot; nothing leaves this '
            "machine. Opt in with <code>practicegraph consent on</code>.</span></div>"
        )

    if privacy.next_payload_pretty is not None:
        payload_eyebrow = "Exact next queued payload"
        payload_meta = "closed aggregate &middot; schema v1"
        payload_html = _colorize_json(privacy.next_payload_pretty)
    else:
        payload_eyebrow = "Exact payload that would be sent"
        payload_meta = esc(SYNTHETIC_EXAMPLE_LABEL)
        payload_html = _colorize_json(privacy.example_payload_pretty)
    out.append(
        '<div class="card">'
        f'<div class="sec-head"><span class="eyebrow">{payload_eyebrow}</span>'
        f'<span class="meta">{payload_meta}</span></div>'
        f'<div class="payload"><pre>{payload_html}</pre></div>'
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">This is the '
        "literal JSON &mdash; there is no hidden field. The schema is closed: "
        "anything else is rejected by the server.</p></div>"
    )

    out.append('<div class="card">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">Emit queue</span>'
        '<span class="meta">local until sent</span></div>'
    )
    out.append('<div class="qstats">')
    for status in QUEUE_STATUSES:
        out.append(
            f'<div class="kpi"><div class="k">'
            f"{esc(count(privacy.queue_counts.get(status, 0)))}</div>"
            f'<div class="l">{esc(status)}</div></div>'
        )
    out.append("</div>")
    if privacy.queue_error_counts:
        items = " &middot; ".join(
            f"{esc(code)}: {esc(count(n))}"
            for code, n in sorted(privacy.queue_error_counts.items())
        )
        out.append(
            f'<p class="muted num" style="font-size:12px;margin:14px 0 0;">recent '
            f"transport results &mdash; {items}</p>"
        )
    destination = "configured" if privacy.endpoint_configured else "not configured"
    org = "configured" if privacy.org_configured else "not configured"
    out.append(
        f'<p class="muted" style="font-size:12.5px;margin:12px 0 0;">Destination '
        f"endpoint: {destination} &middot; organization id: {org}</p>"
    )
    out.append("</div>")

    out.append(
        '<div class="card">'
        '<div class="sec-head"><span class="eyebrow">Change this from the command '
        'line</span></div><ul class="cmds">'
        "<li><code>practicegraph consent status</code> &mdash; current state</li>"
        "<li><code>practicegraph consent show</code> &mdash; the exact sharing "
        "boundary</li>"
        "<li><code>practicegraph consent on</code> / <code>practicegraph consent "
        "off</code> &mdash; change it any time</li></ul>"
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">This page is a '
        "static report: it never changes settings.</p></div>"
    )
    out.append("</section>")
    return "".join(out)


def render_shell(
    snapshot: DailySnapshot,
    privacy: PrivacyStatus,
    generated_at: datetime,
    app_version: str,
    rate_card_version: str,
    extras: ShellExtras | None = None,
) -> str:
    generated_label = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    trust_text = (
        "anonymous aggregates only - opted in"
        if privacy.consent_enabled
        else "this machine only - nothing sent"
    )
    out: list[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('<meta charset="utf-8">')
    out.append(f"<title>PracticeGraph {esc(snapshot.day.isoformat())}</title>")
    out.append(f"<style>{TOKENS_CSS}{_SHELL_CSS}</style>")
    out.append("</head>")
    out.append("<body>")
    out.append('<div class="wrap">')
    report_day = (
        f"Endpoint report - {extras.local_day.isoformat()} "
        f"({extras.schedule.timezone_name} local) - UTC accounting day "
        f"{snapshot.day.isoformat()}"
        if extras is not None and extras.local_day is not None
        else f"Endpoint report - {snapshot.day.isoformat()} (UTC)"
    )
    out.append(brand_header("", report_day, trust_text))
    out.append(
        '<nav class="tabs" aria-label="Report views">'
        '<a href="#today">Today</a>'
        '<a href="#insights">Insights</a>'
        '<a href="#sources">Sources</a>'
        '<a href="#skills">Skills</a>'
        f'<a class="privacy" href="#privacy">{LOCK_SVG} Privacy Center</a>'
        "</nav>"
    )
    out.append("<main>")
    out.append('<section class="panel stack" id="today">')
    if extras is not None and extras.ranges:
        out.append(_range_selector("today"))
    if extras is not None and extras.quiet_hours is not None:
        q = extras.quiet_hours
        out.append(
            '<div class="card"><div class="eyebrow">Your quiet hours</div>'
            f'<p>Recorded human AI interactions reached your quiet hours on '
            f'{q["quiet_days"]} of {q["observed_days"]} observed days.</p>'
            f'<p>{esc(str(q["from_day"]))} to {esc(str(q["to_day"]))}; '
            f'{esc(extras.schedule.timezone_name)}.</p>'
            '<p class="muted">Background agent activity is excluded. '
            'This is not your complete working day.</p></div>'
        )
    out.append('<details class="card quiet"><summary>Your own reflection</summary>'
               f'<div class="body">{_rate_row()}</div></details>')
    out.append(_shape_card(snapshot, extras))
    if extras is not None and extras.runway is not None and extras.runway.available:
        out.append('<div class="card"><div class="eyebrow">Recorded allowance readings</div>')
        for bucket in extras.runway.buckets:
            source = {"codex_cli": "Codex", "claude_code_cli": "Claude Code"}.get(
                bucket.source_id, "Source not recorded"
            )
            out.append(
                f'<p>{esc(source)}; {bucket.window_minutes}-minute window: '
                f'{bucket.latest_pct}% used. '
                f'Observed {esc(bucket.observed_at or "time unknown")}.</p>'
            )
            if bucket.resets_at:
                out.append(f'<p>Recorded reset: {esc(bucket.resets_at)}.</p>')
        out.append('<p class="muted">Historical log snapshots; usage may have changed. '
                   'Reset times appear only when recorded in the source log.</p></div>')
    # Curated guidance follows the local reading when a published board is available.
    if extras is not None and extras.advisor is not None and extras.advisor.takes:
        out.append(_advisor_card(extras.advisor))
    out.append(_source_health_quiet(snapshot))
    out.append("</section>")
    out.append(_insights_panel(snapshot, rate_card_version, extras))
    out.append(_sources_panel(snapshot))
    out.append(_skills_panel(extras))
    out.append(_privacy_panel(privacy))
    if extras is not None:
        for summary in extras.ranges:
            out.append(_range_panel(summary))
    out.append("</main>")
    out.append(
        '<div class="foot">'
        f"<span>practicegraph &middot; endpoint report &middot; generated "
        f"{esc(generated_label)}</span>"
        f"<span>estimates &middot; rate card {esc(rate_card_version)} &middot; "
        f"v{esc(app_version)}</span>"
        "</div>"
    )
    out.append("</div>")
    out.append("</body>")
    out.append("</html>")
    return "\n".join(out) + "\n"
