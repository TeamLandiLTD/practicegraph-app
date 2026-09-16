//! Focus-block timer (FR-FOC-9): the 90-minute focus block and 10-minute
//! break, activated by the report's `practicegraph:` links (C-5). The
//! dashboard's own in-page timer is the primary surface now; this protocol
//! path remains for the report links. started/completed counters are recorded
//! through the core CLI (C-4) and surface in the daily report; completion
//! notifies via toast.

use std::time::Duration;

use crate::corerun;

pub const FOCUS_BLOCK_MIN: u64 = 90; // FR-FOC-9 default
pub const BREAK_MIN: u64 = 10;

#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    Focus,
    Break,
}

fn plan(kind: Kind) -> (u64, &'static str, &'static str, &'static str, &'static str) {
    match kind {
        Kind::Focus => (
            FOCUS_BLOCK_MIN,
            "block-started",
            "block-completed",
            "Focus block complete",
            "90 minutes on one thing - done. A 10-minute pause keeps the next \
             block sharp.",
        ),
        Kind::Break => (
            BREAK_MIN,
            "break-started",
            "break-completed",
            "Break over",
            "Ready when you are - the next focus block is one click away.",
        ),
    }
}

/// Protocol-activated timer (report links, C-5): this process *is* the
/// countdown — it records the start, waits out the block, records
/// completion, and toasts. No console window (GUI subsystem).
pub fn run_blocking(kind: Kind) -> i32 {
    let (minutes, start_event, done_event, title, body) = plan(kind);
    corerun::record_focus_event(start_event);
    std::thread::sleep(Duration::from_secs(minutes * 60));
    corerun::record_focus_event(done_event);
    corerun::show_toast(title, body);
    0
}
