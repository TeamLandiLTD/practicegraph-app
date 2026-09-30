"""Plain-text daily report renderer (FR-RPT-1).

Pure function of its inputs; byte-reproducible (FR-RPT-3). ASCII-only so the
output is stable across Windows console encodings. Debug-grade telemetry
(per-source counters) lives here, in the text surface, per FR-RPT-4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from practicegraph.analysis.aggregate import DailySnapshot
from practicegraph.report.format import compact, count, usd
from practicegraph.sources import SourceHealthRow

if TYPE_CHECKING:
    from practicegraph.report.shell import ShellExtras

_RULE = "=" * 70


def _health_lines(row: SourceHealthRow) -> list[str]:
    lines = [f"  {row.source_id} ({row.capability.value}, parser v{row.parser_version})"]
    health = row.health
    if health.seen == 0:
        lines.append("    no local logs found for this source.")
        return lines
    lines.append(
        "    records: "
        f"{count(health.seen)} seen, "
        f"{count(health.parsed)} parsed, "
        f"{count(health.skipped)} ignored, "
        f"{count(health.malformed)} malformed, "
        f"{count(health.unsupported)} unrecognized, "
        f"{count(health.unknown_field)} with unfamiliar fields"
    )
    if health.drift_detected:
        lines.append(
            "    note: some records in this source's logs were not recognized."
        )
        lines.append(
            "    Parsing continued and skipped them safely; an agent update may"
        )
        lines.append("    be available. No log content was recorded.")
    return lines


def _extras_lines(extras: ShellExtras) -> list[str]:
    """Debug-grade telemetry lives in the text surface (FR-RPT-4): trend rows,
    focus metrics, and raw finding/suggestion ids."""
    lines: list[str] = []
    active = [p for p in extras.trend if p.cost_micro_usd > 0 or p.tokens_total > 0]
    lines.append("TREND (LAST 7 DAYS)")
    if active:
        for point in extras.trend:
            marker = "*" if point.cost_micro_usd or point.tokens_total else " "
            lines.append(
                f"  {marker} {point.day}  {usd(point.cost_micro_usd):>8}  "
                f"{compact(point.tokens_total):>8} tokens  "
                f"{count(point.assistant_turns):>4} turns"
            )
    else:
        lines.append("  no activity in the window")
    if extras.wow is not None:
        lines.append(
            f"  week over week: cost {extras.wow.cost_delta_pct:+d}% | "
            f"tokens {extras.wow.tokens_delta_pct:+d}%"
        )
    lines.append("")

    if extras.focus is not None:
        lines.append("FOCUS (local only)")
        lines.append(
            f"  Longest uninterrupted block: {count(extras.focus.longest_block_min)} min"
        )
        if extras.focus_enabled:
            lines.append(
                f"  streak {count(extras.focus.longest_streak_min)} min | "
                f"concurrent {count(extras.focus.max_concurrent_sessions)} | "
                f"switches {count(extras.focus.switch_count)} | "
                f"bursts {count(extras.focus.burst_windows)}"
            )
            for tip_id in extras.tips:
                lines.append(f"  tip: {tip_id}")
        lines.append("")

    if extras.profile is not None and extras.profile.active_days_total > 0:
        profile = extras.profile
        lines.append("PROFILE (all local history)")
        lines.append(
            f"  lifetime {compact(profile.lifetime_tokens)} tokens | "
            f"{usd(profile.lifetime_cost_micro_usd)} est. | "
            f"{count(profile.active_days_total)} active days"
        )
        peak = (
            f"{profile.peak_day} ({compact(profile.peak_day_tokens)} tokens)"
            if profile.peak_day
            else "-"
        )
        lines.append(
            f"  peak day {peak} | longest block {count(profile.longest_block_min)} min"
        )
        lines.append("")

    if (
        extras.rhythm is not None
        and extras.rhythm.events_total > 0
        and extras.focus_enabled
    ):
        rhythm = extras.rhythm
        lines.append(f"RHYTHM (last {rhythm.window_days} days, local only)")
        quiet = (
            f"{rhythm.quiet_hours_activity_pct}%"
            if rhythm.quiet_hours_activity_pct is not None
            else "preferred schedule not confirmed"
        )
        outside = (
            f"{rhythm.outside_preferred_hours_pct}%"
            if rhythm.outside_preferred_hours_pct is not None
            else "preferred schedule not confirmed"
        )
        off_schedule = (
            f"{rhythm.off_schedule_day_pct}%"
            if rhythm.off_schedule_day_pct is not None
            else "preferred schedule not confirmed"
        )
        lines.append(
            f"  quiet-hours activity {quiet} | outside preferred hours {outside} |"
            f" off-schedule days {off_schedule} |"
            f" deep-block days {count(rhythm.deep_block_days)} |"
            f" no-pause 2h+ days {count(rhythm.long_streak_days)}"
        )
        lines.append("")

    if extras.findings or extras.suggestions:
        lines.append("INSIGHTS")
        for finding in extras.findings:
            lines.append(
                f"  [{finding.severity.value}] {finding.finding_id} "
                f"({count(finding.metric)})"
            )
        for suggestion in extras.suggestions:
            lines.append(f"  suggest: {suggestion.suggestion_id}")
        lines.append("")
    return lines


def render_text(
    snapshot: DailySnapshot,
    generated_at: datetime,
    app_version: str,
    rate_card_version: str,
    extras: ShellExtras | None = None,
) -> str:
    generated_label = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append("PRACTICEGRAPH DAILY REPORT")
    if extras is not None and extras.local_day is not None:
        lines.append(
            f"Local day: {extras.local_day.isoformat()} ({extras.schedule.timezone_name})"
        )
        lines.append(f"UTC accounting day: {snapshot.day.isoformat()}")
    else:
        lines.append(f"Day (UTC): {snapshot.day.isoformat()}")
    lines.append(_RULE)
    lines.append("")

    if extras is not None:
        observation = extras.practice_observation
        lines.append("WORK-PATTERN OBSERVATION")
        lines.append(f"Observation: {observation.observation_id}")
        lines.append(f"Confidence: {observation.confidence}")
        lines.append(observation.title)
        lines.append(observation.body)
        lines.append(f"Period: {observation.period}")
        lines.append(observation.caveat)
        lines.append("")

    lines.append("SPEND (estimates)")
    lines.append(f"  Estimated spend: {usd(snapshot.total_cost_micro_usd)}")
    lines.append(f"  Basis: local token counts x rate card {rate_card_version}.")
    lines.append("  All figures are estimates, computed on this machine only.")
    if snapshot.total_unpriced_turns:
        lines.append(
            f"  Turns without a rate-card entry: {count(snapshot.total_unpriced_turns)}"
        )
    lines.append("")

    if extras is not None:
        lines.extend(_extras_lines(extras))

    lines.append("USAGE BY TOOL AND MODEL")
    visible_rows = [row for row in snapshot.rows if row.assistant_turns > 0]
    if visible_rows:
        lines.append(
            f"  {'tool':<13} {'model':<28} {'turns':>6} "
            f"{'input':>10} {'cached':>10} {'output':>10} {'cost':>10}"
        )
        for row in visible_rows:
            lines.append(
                f"  {row.tool:<13} {row.model:<28} {count(row.assistant_turns):>6} "
                f"{count(row.tokens.input):>10} {count(row.tokens.cached):>10} "
                f"{count(row.tokens.output):>10} {usd(row.cost_micro_usd, 4):>10}"
            )
    else:
        lines.append("  No assistant activity recorded for this day.")
    lines.append("")
    lines.append(
        f"  Sessions: {count(snapshot.session_count)} | "
        f"Assistant turns: {count(snapshot.total_assistant_turns)} | "
        f"Tool calls: {count(snapshot.total_tool_calls)} | "
        f"Interruptions: {count(snapshot.total_interruptions)} | "
        f"Retries: {count(snapshot.total_retries)}"
    )
    lines.append("")

    lines.append("SOURCE HEALTH (all scanned logs)")
    for health_row in snapshot.source_health:
        lines.extend(_health_lines(health_row))
    lines.append("")

    lines.append("STATUS")
    lines.append("  All analysis ran locally on this machine. Nothing left this machine.")
    lines.append(
        f"  Generated {generated_label} | PracticeGraph {app_version} | "
        f"rate card {rate_card_version}"
    )
    lines.append("")
    return "\n".join(lines)
