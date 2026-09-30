//! PracticeGraph native Windows shell (spec §9 C-2).
//!
//! One small binary, a handful of jobs:
//!   `service run [--console] [--once]`  SCM service host for the agent loop
//!   `tray`                              per-user tray application
//!   `app`                               dashboard in a native WebView2 window
//!   `toast --title T --body B`          WinRT toast (AUMID-registered)
//!   `open-report [--privacy]`           open the dashboard app (--privacy:
//!                                       the static report's Privacy Center)
//!   `status [--ui]`                     install health (service/tray/core/data dir)
//!   `url <practicegraph:verb>`          protocol dispatch (toasts, report links)
//!
//! The shell never analyzes anything and never writes state: it launches the
//! core CLI (C-4, C-6) and reads only world-readable artifacts (reports).
//!
//! GUI subsystem: no console window may ever flash (tray, app window,
//! protocol handlers). CLI output (usage, --version, `service run
//! --console`) is therefore invisible unless stdout is redirected — an
//! accepted trade-off for a desktop binary.

#![windows_subsystem = "windows"]

mod app;
mod corerun;
mod eventlog;
mod inputwatch;
mod service;
mod status;
mod timer;
mod toast;
mod tray;
mod upgrade;

pub(crate) const VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = dispatch(&args);
    std::process::exit(code);
}

fn dispatch(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("service") if args.get(1).map(String::as_str) == Some("run") => {
            let console = args.iter().any(|a| a == "--console");
            let once = args.iter().any(|a| a == "--once");
            if console {
                service::run_console(once)
            } else {
                service::run_scm()
            }
        }
        // `tray` opens the dashboard window on launch (regular app behavior);
        // `tray --silent` (used by the logon autostart) starts the icon only,
        // so signing in never forces a window open.
        Some("tray") => tray::run(args.iter().any(|a| a == "--silent")),
        Some("app") => app::run(flag_value(args, "--start").as_deref()),
        // Installer use: end whatever of ours still runs from the install
        // directory, so its files can be replaced (upgrade.rs).
        Some("stop-running") => upgrade::stop_running(args.get(1).map_or("", String::as_str)),
        Some("news-popup") => corerun::open_news(true),
        Some("toast") => {
            let title = flag_value(args, "--title").unwrap_or_else(|| "PracticeGraph".into());
            let body = flag_value(args, "--body").unwrap_or_default();
            // Optional deep-link on tap; closed verbs only (toast.rs
            // validates), so a caller can never smuggle free-form data
            // into the activation.
            let launch = flag_value(args, "--launch");
            match toast::show_launch(&title, &body, launch.as_deref()) {
                Ok(()) => 0,
                Err(_) => {
                    eprintln!("toast delivery failed");
                    1
                }
            }
        }
        Some("open-report") => {
            // The dashboard app IS the product surface; the static report
            // remains only behind the explicit --privacy deep-link (the
            // Privacy Center has no app equivalent yet).
            if args.iter().any(|a| a == "--privacy") {
                corerun::open_latest_report(Some("privacy"))
            } else {
                corerun::open_dashboard()
            }
        }
        Some("status") => {
            let ui = args.iter().any(|a| a == "--ui");
            status::run(ui)
        }
        Some("url") => {
            let raw = args.get(1).map(String::as_str).unwrap_or("");
            handle_url(raw)
        }
        Some("--version") => {
            println!("practicegraph-shell {VERSION}");
            0
        }
        _ => {
            eprintln!(
                "usage: practicegraph-shell <service run [--console] [--once] | tray | app | \
                 toast --title T --body B | open-report [--privacy] | status [--ui] | \
                 --version>"
            );
            2
        }
    }
}

/// Closed protocol verbs (C-5). Anything unrecognized — including the bare
/// `practicegraph:` and legacy `practicegraph:open-report` toasts — opens
/// the dashboard app window; the scheme never carries free-form data.
fn handle_url(raw: &str) -> i32 {
    let verb = raw
        .trim()
        .strip_prefix("practicegraph:")
        .unwrap_or(raw)
        .trim_matches('/');
    for (prefix, cli, done) in [
        (
            "dismiss-suggestion-",
            &["suggest", "dismiss"][..],
            "Dismissed - it will not be suggested again.",
        ),
        (
            "dismiss-tip-",
            &["focus", "dismiss"][..],
            "Dismissed permanently - that tip will not come back.",
        ),
    ] {
        if let Some(id) = verb.strip_prefix(prefix) {
            let safe = !id.is_empty()
                && id.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
            if safe && corerun::dismiss(cli, id) {
                let _ = toast::show("PracticeGraph", done);
                return 0;
            }
            return 1;
        }
    }
    if let Some(rating) = verb.strip_prefix("checkin-") {
        if matches!(rating, "1" | "2" | "3" | "4" | "5") {
            if corerun::record_checkin(rating) {
                let _ = toast::show(
                    "Checked in",
                    &format!(
                        "{rating}/5 for today. The dashboard picks it up on \
                         the next refresh."
                    ),
                );
                return 0;
            }
            return 1;
        }
    }
    match verb {
        "timer-focus" => timer::run_blocking(timer::Kind::Focus),
        "timer-break" => timer::run_blocking(timer::Kind::Break),
        // The break-toast door: open the app straight into the guided
        // break (the dashboard dims, the panel runs the clock).
        "break" => corerun::open_dashboard_start_break(),
        "news" => corerun::open_news(false),
        // The update toast: the verified update card leads the dashboard.
        "update" => corerun::open_dashboard(),
        // The one static-report deep-link left: the Privacy Center has no
        // app equivalent yet. Everything else — including the legacy bare
        // `practicegraph:` and `open-report` toasts — opens the app window.
        "open-privacy" => corerun::open_latest_report(Some("privacy")),
        _ => corerun::open_dashboard(),
    }
}

fn flag_value(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .cloned()
}
