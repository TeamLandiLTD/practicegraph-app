"""Can the install tell the truth about itself?

Both notices exist because of the 2026-07-26 move to a per-user install, and
both cover a failure that is otherwise SILENT — which is the worst kind for a
measurement tool, because silence reads as "nothing to report" when it may mean
"nobody is reporting".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from practicegraph.analysis.install_health import (
    HEALTH_COPY,
    STALE_AFTER_MIN,
    TICK_INTERVAL_MIN,
    compose_install_health,
    legacy_service_present,
    minutes_since,
)
from practicegraph.privacy import leak_findings, lexicon_violations

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _at(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def test_an_ordinary_gap_says_nothing() -> None:
    """A laptop that slept, a tick that ran long, a machine that was off. The
    page must not cry stale over normal life or the notice stops being read."""
    for gap in (0, TICK_INTERVAL_MIN, STALE_AFTER_MIN - 1):
        assert compose_install_health(_at(gap), NOW) == []


def test_a_stalled_scheduler_is_stated_rather_than_hidden() -> None:
    """The failure mode the per-user install created: the tray hosts the tick
    now, and nothing restarts a tray. A service is restarted by the SCM; this
    is not, so the page has to admit its own age."""
    notices = compose_install_health(_at(STALE_AFTER_MIN), NOW)
    assert [n.notice_id for n in notices] == ["stale"]
    assert "60 minutes ago" in notices[0].line
    # ...and it says nothing is lost, because nothing is: the agent re-reads
    # the same logs whenever it next runs.
    assert "nothing is lost" in notices[0].why


def test_the_ago_phrasing_scales_with_the_gap() -> None:
    assert "90 minutes" in compose_install_health(_at(90), NOW)[0].line
    assert "5 hours" in compose_install_health(_at(300), NOW)[0].line
    assert "3 days" in compose_install_health(_at(60 * 24 * 3), NOW)[0].line


def test_a_leftover_machine_agent_leads_because_it_makes_everything_ambiguous(
) -> None:
    """A per-user MSI cannot remove a per-machine one, so an upgrade leaves two
    agents writing two stores. Every other number on the page becomes 'one of
    the two', which is worse than a stale page and is ranked accordingly."""
    notices = compose_install_health(
        _at(STALE_AFTER_MIN * 10), NOW, legacy_service=True
    )
    assert [n.notice_id for n in notices] == ["legacy_service", "stale"]
    assert "administrator" in notices[0].why
    assert "sc.exe stop PracticeGraphAgent" in notices[0].action


def test_an_unreadable_or_absent_timestamp_never_invents_a_notice() -> None:
    """Withheld, not guessed. 'We could not tell' must not render as 'you have
    a problem'."""
    assert compose_install_health(None, NOW) == []
    for junk in ("", "yesterday", "2026-13-45T00:00:00+00:00", "2026-07-26"):
        assert compose_install_health(junk, NOW) == []
        assert minutes_since(junk, NOW) is None


def test_a_clock_that_moved_backwards_is_not_evidence_of_anything() -> None:
    """A timestamp in the future means the clock changed, not that the agent
    is ahead of itself. It reads as zero rather than a negative age."""
    future = (NOW + timedelta(hours=5)).isoformat()
    assert minutes_since(future, NOW) == 0
    assert compose_install_health(future, NOW) == []


def test_a_naive_timestamp_is_refused_rather_than_assumed_utc() -> None:
    assert minutes_since("2026-07-26T10:00:00", NOW) is None


def test_the_service_probe_is_read_only_and_fails_closed() -> None:
    """It opens the SCM with connect rights and the service with query rights -
    never start, stop or change. On any failure it returns False, because a
    notice telling someone to uninstall something must never come from 'we
    could not tell'."""
    import inspect

    source = inspect.getsource(legacy_service_present)
    for mutating in (
        "StartService", "ControlService", "DeleteService",
        "ChangeServiceConfig", "SERVICE_ALL_ACCESS",
    ):
        assert mutating not in source, mutating
    assert "return False" in source
    # It answers without raising, whatever platform runs the suite.
    assert isinstance(legacy_service_present(), bool)


def test_copy_stays_observational() -> None:
    for text in HEALTH_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
        # No alarm vocabulary: both situations are ordinary and have one step.
        for alarming in ("error", "failed", "critical", "warning", "urgent"):
            assert alarming not in text.lower(), text


def test_the_refresh_hint_names_the_launcher_this_platform_actually_has() -> None:
    """Small, but it is the kind of detail that tells a Mac user the app was
    ported rather than built for them. The stale notice is one of the few
    strings a new user is likely to meet early."""
    from practicegraph.analysis.install_health import _stale_action

    assert "Start menu" in _stale_action("win32")
    assert "Applications folder" in _stale_action("darwin")
    assert "Start menu" not in _stale_action("darwin")
    # An unknown platform still gets a sentence, not a KeyError or a lie.
    assert "app launcher" in _stale_action("freebsd13")
