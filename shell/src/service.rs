//! Per-user scheduler helpers. Legacy SCM hosting is explicitly disabled.
use std::time::Duration;
use crate::{corerun, eventlog};
pub const SERVICE_NAME: &str = "PracticeGraphAgent";

const DEFAULT_INTERVAL_S: u64 = 900; // FR-ALR-1 default poll interval
const MIN_INTERVAL_S: u64 = 60;

pub(crate) fn tick_interval() -> Duration {
    let seconds = std::env::var("PRACTICEGRAPH_TICK_INTERVAL_S")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_S)
        .max(MIN_INTERVAL_S);
    Duration::from_secs(seconds)
}

pub(crate) fn run_one_tick() -> String {
    match corerun::run_core_tick() {
        Ok(0) => "tick_ok".to_string(),
        Ok(code) => {
            let outcome = format!("tick_nonzero_exit_{code}");
            eventlog::warn(&outcome);
            outcome
        }
        Err(outcome) => {
            eventlog::error(&outcome);
            outcome
        }
    }
}

pub fn run_console(once: bool) -> i32 {
    let interval = tick_interval();
    loop {
        let outcome = run_one_tick();
        println!("practicegraph-shell {outcome}");
        if once {
            return i32::from(outcome != "tick_ok");
        }
        std::thread::sleep(interval);
    }
}

pub fn run_scm() -> i32 {
    eprintln!("Machine-wide personal collection is retired; launch the per-user tray app.");
    2
}
