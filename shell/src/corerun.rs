//! Launching the analysis core and opening its artifacts. The shell writes
//! nothing itself: state changes go through the core CLI only (C-4).

use std::net::{SocketAddr, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant, SystemTime};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Directory containing this shell executable (the install dir).
pub fn install_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Data directory. Per-user by default (%LOCALAPPDATA%\PracticeGraph), which
/// is also the core's own default in `config.default_data_dir` - so the CLI,
/// the tray and the app window all land on ONE store instead of the two that
/// existed while this defaulted to %ProgramData%.
///
/// The machine-wide service overrides it by setting PRACTICEGRAPH_DATA_DIR to
/// the shared location in older releases. That
/// is the only caller that should: a LocalSystem process resolving
/// %LOCALAPPDATA% would land in SYSTEM's own profile, which is nobody's data.
pub fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("PRACTICEGRAPH_DATA_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    local_app_data().join("PracticeGraph")
}

/// %LOCALAPPDATA%, falling back to the profile-relative path.
pub fn local_app_data() -> PathBuf {
    let local = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| {
        let home = std::env::var("USERPROFILE").unwrap_or_default();
        format!("{home}\\AppData\\Local")
    });
    PathBuf::from(local)
}

fn python_exe() -> PathBuf {
    install_dir().join("runtime").join("python.exe")
}

/// Compiled analysis core shipped next to the shell (engine bundles).
/// Dev bundles carry the embedded Python runtime instead.
fn engine_exe() -> Option<PathBuf> {
    let exe = install_dir().join("practicegraph-engine.exe");
    exe.is_file().then_some(exe)
}

/// Which analysis core this install carries (`status` reporting).
pub enum CoreKind {
    /// Compiled engine exe next to the shell (engine bundle).
    Engine,
    /// Embedded Python runtime (dev/runtime bundle).
    Runtime,
    /// Neither found — the install is broken.
    Missing,
}

pub fn core_kind() -> CoreKind {
    if engine_exe().is_some() {
        CoreKind::Engine
    } else if python_exe().is_file() {
        CoreKind::Runtime
    } else {
        CoreKind::Missing
    }
}

fn core_command(args: &[&str]) -> Command {
    let mut cmd = match engine_exe() {
        Some(engine) => Command::new(engine),
        None => {
            let mut cmd = Command::new(python_exe());
            cmd.arg("-m").arg("practicegraph");
            cmd
        }
    };
    cmd.args(args);
    cmd.current_dir(install_dir());
    cmd.env("PRACTICEGRAPH_DATA_DIR", data_dir());
    // Each process reads only its user's profile, including on upgrades.
    cmd.env_remove("PRACTICEGRAPH_SCAN_PROFILES");
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

/// Record a daily self-rating through the core CLI (C-4) — invoked by the
/// report's one-click rating links via the practicegraph: protocol.
pub fn record_checkin(rating: &str) -> bool {
    core_command(&["checkin", rating])
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

/// Open the dashboard. Kept for protocol back-compat, but it no longer
/// touches a browser: it relaunches this shell detached in `app` mode,
/// which ensures the UI server and hosts it in a native WebView2 window.
pub fn open_dashboard() -> i32 {
    spawn_app(&["app"])
}

pub fn open_news(automatic: bool) -> i32 {
    if automatic {
        spawn_app(&["app", "--start", "news-auto"])
    } else {
        spawn_app(&["app", "--start", "news"])
    }
}

/// Counts only, from the engine-owned status artifact; no story content in tray state.
pub fn news_unread() -> u16 {
    std::fs::read_to_string(data_dir().join("status.json"))
        .ok()
        .and_then(|text| json_u16(&text, "news_unread"))
        .unwrap_or(0)
        .min(12)
}

/// The break-toast door: the app window opens with the guided break
/// already running (app mode passes the intent to the page as a fragment).
pub fn open_dashboard_start_break() -> i32 {
    spawn_app(&["app", "--start", "break"])
}

fn spawn_app(args: &[&str]) -> i32 {
    let Ok(exe) = std::env::current_exe() else {
        return 1;
    };
    let mut cmd = Command::new(exe);
    cmd.args(args);
    cmd.current_dir(install_dir());
    cmd.creation_flags(CREATE_NO_WINDOW);
    match cmd.spawn() {
        Ok(_) => 0,
        Err(_) => 1,
    }
}

/// Where the core's `ui serve` publishes its endpoint (ui.json with
/// {port, token}). ONE path now: the shell pins PRACTICEGRAPH_DATA_DIR on
/// every core command it spawns, so a server we started always publishes
/// exactly where `data_dir()` says. This used to probe two directories
/// because the shell defaulted to %ProgramData% while the core defaulted to
/// %LOCALAPPDATA%, and "whichever answers first" is not a design.
fn ui_state_paths() -> Vec<PathBuf> {
    vec![data_dir().join("ui.json")]
}

/// Live UI server endpoint as published in ui.json.
pub struct UiEndpoint {
    pub port: u16,
    pub token: String,
}

// ui.json is written by our own core ({"port": N, "token": "..."}); two
// field lookups do not warrant a JSON dependency. The token is URL-safe by
// construction, so no escape handling is needed.
fn json_value_start<'t>(text: &'t str, key: &str) -> Option<&'t str> {
    let needle = format!("\"{key}\"");
    let at = text.find(&needle)?;
    let rest = text[at + needle.len()..].trim_start();
    Some(rest.strip_prefix(':')?.trim_start())
}

fn json_u16(text: &str, key: &str) -> Option<u16> {
    let rest = json_value_start(text, key)?;
    let end = rest
        .char_indices()
        .find(|(_, c)| !c.is_ascii_digit())
        .map_or(rest.len(), |(i, _)| i);
    rest[..end].parse().ok()
}

fn json_str<'t>(text: &'t str, key: &str) -> Option<&'t str> {
    let rest = json_value_start(text, key)?.strip_prefix('"')?;
    Some(&rest[..rest.find('"')?])
}

fn read_endpoint_file(path: &Path) -> Option<UiEndpoint> {
    let text = std::fs::read_to_string(path).ok()?;
    let port = json_u16(&text, "port")?;
    let token = json_str(&text, "token")?.to_string();
    (port != 0 && !token.is_empty()).then_some(UiEndpoint { port, token })
}

pub fn ui_listening(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// Ping `port` with `token` and return the server's reported app version.
///
/// A bare socket probe is NOT enough to trust a published endpoint. The
/// preferred port is derived from the data dir, so a REINSTALL rebinds the
/// same port while minting a new token — a surviving ui.json then points at
/// a live server that rejects its token, and the window loads straight into
/// a 403 (field report 2026-07-20, after an uninstall/reinstall). The same
/// probe also catches a server left over from an older install: /api/ping
/// answers 200 only for the right token, and carries the version so a
/// mismatched engine can be replaced instead of reused.
fn ui_ping_version(port: u16, token: &str) -> Option<String> {
    use std::io::{Read, Write};

    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(400)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    stream.set_write_timeout(Some(Duration::from_secs(2))).ok()?;
    let request = format!(
        "GET /api/ping?token={token} HTTP/1.1\r\nHost: 127.0.0.1\r\n\
         Connection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    // The ping body is a few dozen bytes; cap the read so a wedged or
    // unrelated process on this port can never stream at us indefinitely.
    stream.take(4096).read_to_string(&mut response).ok()?;
    let (status, body) = response.split_once("\r\n")?;
    if !status.contains(" 200") {
        return None; // wrong token (403) or not our API
    }
    // {"ok": true, "app_version": "0.1.4"} — one field, no JSON dependency.
    Some(json_str(body, "app_version").unwrap_or_default().to_string())
}

/// First published endpoint whose token authenticates: that ping is the
/// whole identity check. Stale files (server gone) and rebound ports
/// carrying an old token are skipped, so the caller starts a fresh server —
/// whose publish REPLACES the file atomically. The shell deletes nothing:
/// ui.json is the engine's state (C-4 — the engine is the only writer), and
/// the one time this function did delete it, the install broke wholesale.
///
/// Field report 2026-07-28: this used to additionally require the engine's
/// version to EQUAL the shell's compiled crate version. The crate version
/// was still 0.1.13 while the tree shipped 0.1.15, so every freshly
/// installed engine published a perfectly good endpoint and the shell
/// deleted it within milliseconds — respawn, republish, delete again, a
/// convoy of orphaned servers, and after 15 s the old browser fallback. A
/// version-mismatched engine (a mid-upgrade moment: an old server still
/// draining) is a TRANSIENT the engine's own supersession watchdog resolves;
/// treating it as a gate turns a transient into a permanent outage. The
/// version still rides the ping for status surfaces — it is information,
/// never an identity test.
fn live_ui_endpoint() -> Option<UiEndpoint> {
    for path in ui_state_paths() {
        let Some(endpoint) = read_endpoint_file(&path) else {
            continue;
        };
        if ui_ping_version(endpoint.port, &endpoint.token).is_some() {
            return Some(endpoint);
        }
    }
    None
}

/// Ensure the local UI server is running and return its endpoint — never
/// opens a browser. A live published endpoint is reused; otherwise the
/// engine's `ui serve` is spawned detached (CREATE_NO_WINDOW via
/// core_command, spawn-and-forget) and ui.json plus the socket are polled
/// for up to ~15 s.
pub fn ensure_ui_server() -> Option<UiEndpoint> {
    if let Some(endpoint) = live_ui_endpoint() {
        return Some(endpoint);
    }
    // Single-flight the start: without this, two windows (or a window and the
    // service tick) can both see no server and each spawn `ui serve`, and the
    // two racing starts can leave a stale/absent ui.json — the app then can't
    // find the port and shows "could not reach". A cross-process mutex means
    // only one caller spawns; the others wait on it and pick up the endpoint it
    // publishes. Best-effort: if the mutex can't be taken we still fall through
    // to the spawn-and-poll below.
    let _guard = UiSpawnGuard::acquire();
    // Recheck under the guard — whoever held it first may have just started it.
    if let Some(endpoint) = live_ui_endpoint() {
        return Some(endpoint);
    }
    let _ = core_command(&["ui", "serve"]).spawn();
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(300));
        if let Some(endpoint) = live_ui_endpoint() {
            return Some(endpoint);
        }
    }
    None
}

/// A held cross-process lock that serializes `ui serve` starts. Acquired for
/// the duration of a spawn-and-wait; released on drop so a later cold start can
/// take it again.
struct UiSpawnGuard {
    handle: *mut core::ffi::c_void,
}

impl UiSpawnGuard {
    fn acquire() -> Self {
        use windows_sys::Win32::System::Threading::{CreateMutexW, WaitForSingleObject};
        let name = wide("Local\\PracticeGraphUiSpawn");
        // SAFETY: standard Win32 named-mutex handshake; the handle is released
        // in Drop. A null handle just means we proceed unsynchronized.
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if !handle.is_null() {
            // Wait up to ~16s (a hair over the caller's 15s spawn deadline) so a
            // wedged holder can never block us forever.
            unsafe { WaitForSingleObject(handle, 16_000) };
        }
        Self { handle }
    }
}

impl Drop for UiSpawnGuard {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            use windows_sys::Win32::Foundation::CloseHandle;
            use windows_sys::Win32::System::Threading::ReleaseMutex;
            // SAFETY: handle came from CreateMutexW above; release then close.
            unsafe {
                ReleaseMutex(self.handle);
                CloseHandle(self.handle);
            }
        }
    }
}

// Presence: "working" means an AI-tool log changed in the last 10 minutes.
// A cheap early-exit walk over the known log roots — no parsing, no content.
const PRESENCE_FRESH_S: u64 = 10 * 60;
const PRESENCE_MAX_DEPTH: usize = 6;

fn any_fresh(dir: &Path, cutoff: SystemTime, depth: usize) -> bool {
    if depth == 0 {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return false;
    };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else { continue };
        if kind.is_file() {
            if let Ok(meta) = entry.metadata() {
                if let Ok(modified) = meta.modified() {
                    if modified >= cutoff {
                        return true;
                    }
                }
            }
        } else if kind.is_dir() && any_fresh(&entry.path(), cutoff, depth - 1) {
            return true;
        }
    }
    false
}

/// True when any known local AI-tool log was touched in the last 10 minutes.
pub fn presence_active() -> bool {
    let Some(cutoff) = SystemTime::now().checked_sub(Duration::from_secs(PRESENCE_FRESH_S))
    else {
        return false;
    };
    let home = std::env::var("USERPROFILE").unwrap_or_default();
    if home.is_empty() {
        return false;
    }
    let home = PathBuf::from(home);
    let mut roots = vec![
        home.join(".claude").join("projects"),
        home.join(".codex").join("sessions"),
    ];
    if let Ok(codex_home) = std::env::var("CODEX_HOME") {
        if !codex_home.is_empty() {
            roots.push(PathBuf::from(codex_home).join("sessions"));
        }
    }
    roots.iter().any(|root| any_fresh(root, cutoff, PRESENCE_MAX_DEPTH))
}

/// Dismiss a suggestion or tip through the core CLI (C-4) — invoked by the
/// report's Dismiss buttons via the practicegraph: protocol. The id charset
/// is validated by the caller; the CLI rejects unknown ids.
pub fn dismiss(kind_args: &[&str], id: &str) -> bool {
    let mut args: Vec<&str> = kind_args.to_vec();
    args.push(id);
    core_command(&args)
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

/// Run one agent tick via the core CLI. Returns a closed outcome label.
pub fn run_core_tick() -> Result<i32, String> {
    match core_command(&["agent", "run", "--once"]).output() {
        Ok(output) => Ok(output.status.code().unwrap_or(-1)),
        Err(err) => Err(format!("spawn_failed_os_{}", err.raw_os_error().unwrap_or(0))),
    }
}

/// Open the newest rendered daily report in the default browser; fall back to
/// asking the core to render one (FR-RPT-6).
pub fn open_latest_report(fragment: Option<&str>) -> i32 {
    let reports = data_dir().join("reports");
    let mut newest: Option<PathBuf> = None;
    if let Ok(entries) = std::fs::read_dir(&reports) {
        let mut names: Vec<PathBuf> = entries
            .flatten()
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n.starts_with("daily-") && n.ends_with(".html"))
                    .unwrap_or(false)
            })
            .collect();
        names.sort();
        newest = names.pop();
    }
    match newest {
        Some(path) => {
            let mut url = format!("file:///{}", path.display().to_string().replace('\\', "/"));
            if let Some(fragment) = fragment {
                url.push('#');
                url.push_str(fragment);
            }
            shell_open(&url)
        }
        None => match core_command(&["report", "--open"]).status() {
            Ok(status) if status.success() => 0,
            _ => 1,
        },
    }
}

/// Record a focus-timer lifecycle event through the core CLI (C-4: the shell
/// never writes state itself). Fire-and-forget; failures are silent.
pub fn record_focus_event(event: &str) {
    let mut cmd = core_command(&["focus", "record", event]);
    let _ = cmd.spawn();
}

/// Show a toast by re-invoking this shell as a fresh process — keeps WinRT
/// apartment initialization out of worker threads.
pub fn show_toast(title: &str, body: &str) {
    if let Ok(exe) = std::env::current_exe() {
        let mut cmd = Command::new(exe);
        cmd.args(["toast", "--title", title, "--body", body]);
        cmd.creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.spawn();
    }
}

/// Modal message box — the GUI-subsystem replacement for console output
/// (C-2: no console window may ever flash).
pub fn message_box(title: &str, body: &str, warning: bool) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, MB_ICONINFORMATION, MB_ICONWARNING, MB_OK, MB_SETFOREGROUND,
    };
    let icon = if warning { MB_ICONWARNING } else { MB_ICONINFORMATION };
    let title_w = wide(title);
    let body_w = wide(body);
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            body_w.as_ptr(),
            title_w.as_ptr(),
            MB_OK | icon | MB_SETFOREGROUND,
        );
    }
}

/// The dashboard's `target=_blank` requests: ordinary web links, plus exactly
/// one custom scheme — the Codex composer deep link the playbook mints
/// (`codex://new?prompt=`). Anything else is dropped, same as before. The
/// composer link only prefills; the person still presses send over there.
pub fn open_external_link(target: &str) -> bool {
    if !is_external_web_url(target) && !is_codex_deep_link(target) {
        return false;
    }
    shell_open(target) == 0
}

pub(crate) fn is_external_web_url(target: &str) -> bool {
    let target = target.trim();
    target
        .get(..7)
        .is_some_and(|scheme| scheme.eq_ignore_ascii_case("http://"))
        || target
            .get(..8)
            .is_some_and(|scheme| scheme.eq_ignore_ascii_case("https://"))
}

pub(crate) fn is_codex_deep_link(target: &str) -> bool {
    // Exact prefix, not just the scheme: the only codex: URL this shell will
    // ever hand to the OS is the prefilled composer.
    target
        .trim()
        .get(..19)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("codex://new?prompt="))
}

fn shell_open(target: &str) -> i32 {
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
    let target_w = wide(target);
    let verb = wide("open");
    let result = unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            verb.as_ptr(),
            target_w.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            SW_SHOWNORMAL,
        )
    };
    // Per API contract, values > 32 indicate success.
    if result as usize > 32 {
        0
    } else {
        1
    }
}

pub fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(test)]
mod tests {
    use super::{is_external_web_url, json_str};

    #[test]
    fn ping_reply_parsing_rejects_a_wrong_token_and_reads_the_version() {
        // The status line decides trust: 403 — a ui.json that survived a
        // reinstall while the port was rebound, so the token no longer
        // matches — is never a usable endpoint. 200 carries the engine
        // version for the same-version check.
        let denied = "HTTP/1.0 403 Forbidden\r\n\r\n{\"error\": \"forbidden\"}";
        let (status, _body) = denied.split_once("\r\n").unwrap();
        assert!(!status.contains(" 200"));

        let ok = "HTTP/1.0 200 OK\r\n\r\n{\"ok\": true, \"app_version\": \"0.1.4\"}";
        let (status, body) = ok.split_once("\r\n").unwrap();
        assert!(status.contains(" 200"));
        assert_eq!(json_str(body, "app_version"), Some("0.1.4"));
    }

    #[test]
    fn accepts_http_and_https_urls_for_the_external_browser() {
        assert!(is_external_web_url("https://example.com/news"));
        assert!(is_external_web_url("HTTP://example.com/news"));
    }

    #[test]
    fn rejects_non_web_urls_from_the_embedded_dashboard() {
        assert!(!is_external_web_url("file:///C:/ProgramData/PracticeGraph/report.html"));
        assert!(!is_external_web_url("javascript:alert(1)"));
        assert!(!is_external_web_url("mailto:hello@example.com"));
    }

    #[test]
    fn the_codex_composer_is_the_only_custom_scheme_allowed_through() {
        use super::is_codex_deep_link;
        assert!(is_codex_deep_link("codex://new?prompt=Goal%3A%20x"));
        assert!(is_codex_deep_link("CODEX://new?prompt=Goal"));
        // The scheme alone is not enough — only the prefilled composer.
        assert!(!is_codex_deep_link("codex://settings"));
        assert!(!is_codex_deep_link("codex://new?path=%2Fetc"));
        assert!(!is_codex_deep_link("file:///C:/x"));
        assert!(!is_codex_deep_link("javascript:alert(1)"));
    }
}
