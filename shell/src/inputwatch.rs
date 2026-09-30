//! The dashboard window can go deaf: WebView2 stops delivering mouse and
//! keyboard input to the page while everything else keeps working — scripts
//! run, timers fire, the view refreshes, the window repaints, focus and blur
//! still reach the document. Only the user's clicks are dropped, so the page
//! looks frozen on the last frame it drew (field diagnosis 2026-09-21; the
//! second occurrence started at the exact second the machine resumed from a
//! 29-hour sleep).
//!
//! Reloading the page does not fix it — the fresh document is equally deaf —
//! and neither does resizing, minimizing, or hiding and showing the webview.
//! Only a new WebView2 instance does.
//!
//! The probe is end-to-end and needs no cooperation from the page beyond an
//! injected counter: the watchdog posts synthetic `WM_MOUSEMOVE` messages to
//! the WebView2 child window (the same door the user's mouse comes through),
//! then asks the page how many moves it has seen. A count that stops growing
//! across probes means the door is shut. Real mouse movement counts too, so
//! an active user keeps proving the window healthy for free.

use std::time::Duration;

/// How often the window checks it can still hear the mouse. Cheap: three
/// posted messages and one script evaluation.
pub const PROBE_INTERVAL: Duration = Duration::from_secs(60);

/// The probe thread sleeps in short steps; a step that takes far longer than
/// it asked for means the machine was suspended in between. Resume is the one
/// trigger seen in the field, so it earns an immediate probe instead of
/// waiting out the interval.
pub const RESUME_GAP: Duration = Duration::from_secs(45);

/// One silent probe can be a page still loading, a document swapped by the
/// watchdog's re-navigation, or a lost IPC reply. Two in a row, with the
/// counter frozen at the same value, is the fault.
const STRIKES_BEFORE_REBUILD: u32 = 2;

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Verdict {
    /// Input reached the page (or this is the first reading).
    Healthy,
    /// Not enough evidence yet — say nothing, change nothing.
    Waiting,
    /// The window is deaf: rebuild the webview.
    Deaf,
}

/// Tracks the injected move counter across probes.
#[derive(Default)]
pub struct Deafness {
    last: Option<u64>,
    strikes: u32,
}

impl Deafness {
    pub fn new() -> Self {
        Self::default()
    }

    /// `reply` is the page's move counter, or `None` when the page did not
    /// answer at all. A missing answer is *not* deafness — that is a script
    /// or engine problem, which the endpoint watchdog already owns — so it
    /// clears the strikes rather than counting toward a rebuild.
    pub fn observe(&mut self, reply: Option<u64>) -> Verdict {
        let Some(count) = reply else {
            self.strikes = 0;
            self.last = None;
            return Verdict::Waiting;
        };
        let verdict = match self.last {
            // First reading after a start or a rebuild: only a baseline.
            None => Verdict::Waiting,
            Some(previous) if count > previous => {
                self.strikes = 0;
                Verdict::Healthy
            }
            Some(_) => {
                self.strikes += 1;
                if self.strikes >= STRIKES_BEFORE_REBUILD {
                    Verdict::Deaf
                } else {
                    Verdict::Waiting
                }
            }
        };
        self.last = Some(count);
        if verdict == Verdict::Deaf {
            self.reset();
        }
        verdict
    }

    /// After a rebuild the injected counter starts from zero again, so the
    /// old reading must not be compared against the new one.
    pub fn reset(&mut self) {
        self.last = None;
        self.strikes = 0;
    }
}

/// A rebuild that does not cure the window must not become a loop that
/// reloads the page every couple of minutes forever. Three attempts is enough
/// to cover a transient fault; past that the window stays as it is and the
/// person can use the tray's reopen item (or the failure is something else
/// entirely, which a silent retry loop would only hide).
pub struct RebuildBudget {
    used: u32,
}

impl RebuildBudget {
    pub const MAX: u32 = 3;

    pub fn new() -> Self {
        Self { used: 0 }
    }

    /// True while a rebuild is still worth trying.
    pub fn take(&mut self) -> bool {
        if self.used >= Self::MAX {
            return false;
        }
        self.used += 1;
        true
    }

    /// The window proved it can hear the mouse again, so the past attempts
    /// are forgiven: a fresh fault next week gets its own three tries.
    pub fn restore(&mut self) {
        self.used = 0;
    }
}

impl Default for RebuildBudget {
    fn default() -> Self {
        Self::new()
    }
}

/// Did the machine sleep through this wait? `asked` is what the thread asked
/// to sleep for, `actual` is how long it really took.
pub fn resumed_from_sleep(asked: Duration, actual: Duration) -> bool {
    actual.saturating_sub(asked) >= RESUME_GAP
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_first_reading_is_only_a_baseline() {
        let mut watch = Deafness::new();
        assert_eq!(watch.observe(Some(7)), Verdict::Waiting);
    }

    #[test]
    fn a_growing_counter_is_a_window_that_hears_the_mouse() {
        let mut watch = Deafness::new();
        watch.observe(Some(7));
        assert_eq!(watch.observe(Some(10)), Verdict::Healthy);
        assert_eq!(watch.observe(Some(13)), Verdict::Healthy);
    }

    #[test]
    fn two_frozen_readings_in_a_row_condemn_the_webview() {
        let mut watch = Deafness::new();
        watch.observe(Some(4));
        assert_eq!(watch.observe(Some(4)), Verdict::Waiting);
        assert_eq!(watch.observe(Some(4)), Verdict::Deaf);
    }

    #[test]
    fn one_frozen_reading_alone_never_rebuilds() {
        let mut watch = Deafness::new();
        watch.observe(Some(4));
        assert_eq!(watch.observe(Some(4)), Verdict::Waiting);
        assert_eq!(watch.observe(Some(9)), Verdict::Healthy);
        assert_eq!(watch.observe(Some(9)), Verdict::Waiting);
    }

    #[test]
    fn a_silent_page_is_not_deafness_and_clears_the_strikes() {
        let mut watch = Deafness::new();
        watch.observe(Some(4));
        assert_eq!(watch.observe(Some(4)), Verdict::Waiting);
        assert_eq!(watch.observe(None), Verdict::Waiting);
        // The silent probe reset the baseline, so the next equal pair starts over.
        assert_eq!(watch.observe(Some(4)), Verdict::Waiting);
        assert_eq!(watch.observe(Some(4)), Verdict::Waiting);
        assert_eq!(watch.observe(Some(4)), Verdict::Deaf);
    }

    #[test]
    fn after_a_rebuild_the_counter_starts_over_without_a_verdict() {
        let mut watch = Deafness::new();
        watch.observe(Some(120));
        watch.observe(Some(120));
        assert_eq!(watch.observe(Some(120)), Verdict::Deaf);
        // The new webview's counter is lower; that is a fresh baseline, not a fault.
        assert_eq!(watch.observe(Some(2)), Verdict::Waiting);
        assert_eq!(watch.observe(Some(5)), Verdict::Healthy);
    }

    #[test]
    fn the_rebuild_budget_stops_a_reload_loop() {
        let mut budget = RebuildBudget::new();
        for _ in 0..RebuildBudget::MAX {
            assert!(budget.take());
        }
        assert!(!budget.take());
    }

    #[test]
    fn a_window_that_recovers_earns_its_attempts_back() {
        let mut budget = RebuildBudget::new();
        assert!(budget.take());
        budget.restore();
        for _ in 0..RebuildBudget::MAX {
            assert!(budget.take());
        }
        assert!(!budget.take());
    }

    #[test]
    fn a_long_gap_in_a_short_sleep_means_the_machine_suspended() {
        assert!(resumed_from_sleep(
            Duration::from_secs(5),
            Duration::from_secs(29 * 3600)
        ));
        assert!(resumed_from_sleep(
            Duration::from_secs(5),
            Duration::from_secs(60)
        ));
    }

    #[test]
    fn ordinary_scheduling_jitter_is_not_a_resume() {
        assert!(!resumed_from_sleep(
            Duration::from_secs(5),
            Duration::from_secs(5)
        ));
        assert!(!resumed_from_sleep(
            Duration::from_secs(5),
            Duration::from_millis(5_900)
        ));
    }
}
