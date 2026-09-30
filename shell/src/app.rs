//! Native dashboard window (`app` mode): the local UI served by the core
//! engine, hosted in an evergreen WebView2 inside a plain tao window — the
//! dashboard never opens in a browser tab. The shell stays read-only: the
//! engine (`ui serve`) is launched through the core CLI and owns all state
//! (C-4); this process only ensures it is up and points a webview at it.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use tao::dpi::{LogicalSize, PhysicalPosition};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::platform::run_return::EventLoopExtRunReturn;
use tao::platform::windows::{WindowBuilderExtWindows, WindowExtWindows};
use tao::window::{Window, WindowBuilder};
use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS, HWND};
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    FindWindowExW, IsIconic, IsWindowVisible, LoadImageW, MessageBoxW, PostMessageW, SendMessageW,
    SetForegroundWindow, ShowWindow, ICON_BIG, ICON_SMALL, IMAGE_ICON, LR_DEFAULTSIZE,
    LR_LOADFROMFILE, MB_ICONWARNING, MB_OK, SW_RESTORE, WM_MOUSEMOVE, WM_SETICON,
};

use crate::corerun::{self, wide};
use crate::inputwatch::{self, Verdict};
use crate::eventlog;

const DEFAULT_SIZE: (f64, f64) = (1280.0, 900.0);
const MIN_SIZE: (f64, f64) = (900.0, 600.0);
// How often the window checks its server is still alive. Short enough that a
// restart recovers in a couple of seconds, long enough to be free.
const WATCHDOG_INTERVAL: Duration = Duration::from_secs(3);
/// The input probe sleeps in short steps so a suspend/resume is noticed
/// promptly rather than at the end of a long wait.
const PROBE_STEP: Duration = Duration::from_secs(5);
/// How long the page gets to answer the probe before it counts as silence.
const PROBE_REPLY_GRACE: Duration = Duration::from_millis(700);

/// Injected into every document (it survives the watchdog's re-navigations and
/// the page's own reloads): counts the mouse moves that actually reach the
/// document, which is what the window loses when WebView2 goes deaf.
const INPUT_COUNTER_SCRIPT: &str = "(function () { if (window.__pgInputHook) { return; } window.__pgInputHook = 1; window.__pgInput = 0; window.addEventListener('mousemove', function () { window.__pgInput++; }, true); })();";

/// Asked on each probe; the reply carries the counter and the page's current
/// address, so a rebuilt webview reopens on the page the user was reading.
const INPUT_REPORT_SCRIPT: &str = "window.ipc.postMessage('pg-input:' + (window.__pgInput | 0) + ':' + location.href);";

/// What the event loop is asked to do from the background threads.
enum AppEvent {
    /// The endpoint watchdog found a live server at a new address.
    Navigate(String),
    /// Ask the page how much input it has seen.
    ReportInput,
    /// The window went deaf: build a new webview.
    RebuildWebView,
}

/// The page's answer to the last probe. Written by the IPC handler on the UI
/// thread, read by the probe thread.
#[derive(Default)]
struct ProbeReply {
    count: AtomicU64,
    answered: AtomicBool,
    href: Mutex<String>,
}

impl ProbeReply {
    fn clear(&self) {
        self.answered.store(false, Ordering::Release);
    }

    fn record(&self, message: &str) {
        let Some(body) = message.strip_prefix("pg-input:") else {
            return;
        };
        let (count, href) = body.split_once(':').unwrap_or((body, ""));
        let Ok(count) = count.parse::<u64>() else {
            return;
        };
        if let Ok(mut current) = self.href.lock() {
            if !href.is_empty() {
                *current = href.to_string();
            }
        }
        self.count.store(count, Ordering::Release);
        self.answered.store(true, Ordering::Release);
    }

    fn take(&self) -> Option<u64> {
        self.answered
            .load(Ordering::Acquire)
            .then(|| self.count.load(Ordering::Acquire))
    }

    fn last_href(&self) -> Option<String> {
        self.href.lock().ok().map(|href| href.clone()).filter(|href| !href.is_empty())
    }
}

/// Open the dashboard in a native window. Stateless by design: size and
/// position are not persisted. Single instance per session — a second
/// invocation surfaces the existing window and exits 0 (a break intent is
/// dropped in that case: the person is looking at the page and its own
/// break button; relaying intents across instances is not worth an IPC
/// channel yet).
///
/// `start` carries the toast's intent: Some("break") appends `#break` to
/// the initial navigation, which the page consumes once to start the
/// guided break. Watchdog re-navigations rebuild the URL without the
/// fragment, so an engine restart never replays the intent.
pub fn run(start: Option<&str>) -> i32 {
    let news = matches!(start, Some("news") | Some("news-auto"));
    let automatic = start == Some("news-auto");
    let title = if news { "PracticeGraph - Latest news" } else { "PracticeGraph" };
    unsafe {
        let mutex_name = wide(if news { "Local\\PracticeGraphNews" } else { "Local\\PracticeGraphApp" });
        // Handle intentionally never closed: it pins the mutex for the
        // process lifetime.
        let mutex = CreateMutexW(std::ptr::null(), 0, mutex_name.as_ptr());
        if mutex.is_null() || GetLastError() == ERROR_ALREADY_EXISTS {
            if !automatic { focus_existing_window(title); }
            return 0;
        }
    }

    // Probe the WebView2 runtime before creating anything visible. It is
    // evergreen on Windows 11, but a managed image may have removed it.
    if wry::webview_version().is_err() {
        if news { return 1; }
        return fallback_no_webview2();
    }

    // A slow engine start no longer downgrades the app to a browser tab.
    //
    // This used to bail to open_latest_report when the endpoint didn't appear
    // within the 15s spawn-poll — and a fresh install reliably missed that
    // deadline (first run of the just-installed onefile engine: cold unpack,
    // AV scan of an unsigned binary, and a schema/parser upgrade rescanning
    // months of history). The user's first impression of every upgrade was
    // "it just opens the browser". The window is the product; it opens
    // regardless, on a local warming page, and the watchdog below navigates
    // the moment the engine answers. The rendered-report fallback remains
    // only where a window is impossible (no WebView2, window build failure).
    let endpoint = corerun::ensure_ui_server();
    let url = match &endpoint {
        Some(endpoint) => {
            let fragment = match start {
                Some("break") => "#break",
                Some("news") | Some("news-auto") => "&panel=news",
                _ => "",
            };
            format!(
                "http://127.0.0.1:{}/?token={}{}",
                endpoint.port, endpoint.token, fragment
            )
        }
        None => warming_page_url(),
    };

    // A user-event loop so the watchdog thread can hand a fresh URL back to the
    // UI thread (String = the new endpoint to reload).
    let mut event_loop = EventLoopBuilder::<AppEvent>::with_user_event().build();
    let size = if news { (420.0, 560.0) } else { DEFAULT_SIZE };
    let minimum = if news { (340.0, 360.0) } else { MIN_SIZE };
    let window = match WindowBuilder::new()
        .with_title(title)
        .with_inner_size(LogicalSize::new(size.0, size.1))
        .with_min_inner_size(LogicalSize::new(minimum.0, minimum.1))
        .with_focused(!automatic)
        .with_skip_taskbar(news)
        .build(&event_loop)
    {
        Ok(window) => window,
        Err(_) if news => return 1,
        Err(_) => return corerun::open_latest_report(None),
    };
    if news {
        if let Some(monitor) = window.current_monitor() {
            let origin = monitor.position();
            let screen = monitor.size();
            let outer = window.outer_size();
            let margin = (64.0 * monitor.scale_factor()) as i32;
            window.set_outer_position(PhysicalPosition::new(
                origin.x + (screen.width as i32 - outer.width as i32 - margin).max(0),
                origin.y + (screen.height as i32 - outer.height as i32 - margin).max(0),
            ));
        }
    }
    apply_window_icon(&window);

    // Give WebView2 a writable data directory. Without this it defaults to a
    // folder next to the exe (<exe>.WebView2\EBWebView) — which lives under
    // C:\Program Files and is not writable, so the runtime aborts with "can't
    // read and write to its data directory". Put it under the app data dir
    // (%LOCALAPPDATA%\PracticeGraph), the same writable root the engine uses.
    let mut web_context = wry::WebContext::new(Some(corerun::data_dir().join(
        if news { "webview2-news" } else { "webview2" }
    )));

    // Must outlive the event loop; wry subclasses the window and follows
    // resizes on its own. Held in an Option because a deaf window is cured
    // only by dropping this webview and building another one.
    let probe = Arc::new(ProbeReply::default());
    let mut webview = match build_webview(&mut web_context, &window, &url, probe.clone()) {
        Ok(webview) => Some(webview),
        Err(_) if news => return 1,
        Err(_) => return fallback_no_webview2(),
    };

    // Self-heal: a background watchdog checks whether the server this window is
    // pointed at is still answering. If it stops (the engine was restarted,
    // crashed, or a newer instance superseded it), the window would otherwise
    // sit forever on a dead origin showing "could not reach the local agent".
    // Instead we re-resolve the live endpoint and reload the webview to it —
    // so restarting the agent, or the machine waking, quietly recovers the
    // window rather than leaving it broken. The check runs off the UI thread
    // and wakes the event loop through a proxy carrying the fresh URL.
    let proxy = event_loop.create_proxy();
    let mut current_url = url;
    std::thread::spawn(move || {
        let mut endpoint = endpoint;
        loop {
            // While warming (no endpoint yet) poll eagerly — the engine may
            // land any second and the window is sitting on the warming page.
            // Once connected, the relaxed interval is plenty.
            match &endpoint {
                Some(live) => {
                    std::thread::sleep(WATCHDOG_INTERVAL);
                    if corerun::ui_listening(live.port) {
                        continue; // still alive — nothing to do
                    }
                }
                None => std::thread::sleep(Duration::from_millis(700)),
            }
            // No live server. Re-resolve (this reuses a live one or starts a
            // fresh server, single-flighted), then ask the UI thread to load
            // it — the same path serves cold warm-up and mid-life recovery.
            if let Some(fresh) = corerun::ensure_ui_server() {
                let fresh_url = format!("http://127.0.0.1:{}/?token={}{}", fresh.port,
                    fresh.token, if news { "&panel=news" } else { "" });
                endpoint = Some(fresh);
                // A failed send just means the window is gone; the thread ends.
                if proxy.send_event(AppEvent::Navigate(fresh_url)).is_err() {
                    return;
                }
            }
        }
    });

    // Second watchdog, for a different failure: the window keeps running but
    // WebView2 stops handing it mouse and keyboard input, so every click is
    // swallowed and the page sits on its last frame (field diagnosis
    // 2026-09-21). Nothing the page can see is wrong, so the check has to come
    // from outside: post a mouse move through the same child window the user's
    // mouse uses, then ask the page whether it arrived.
    let probe_proxy = event_loop.create_proxy();
    let probe_state = probe.clone();
    let window_handle = window.hwnd();
    std::thread::spawn(move || {
        let mut deafness = inputwatch::Deafness::new();
        let mut budget = inputwatch::RebuildBudget::new();
        let mut waited = Duration::ZERO;
        loop {
            // Wall clock, not Instant: the monotonic clock does not advance
            // while the machine is suspended, and a resume is exactly what we
            // want to notice here.
            let before = SystemTime::now();
            std::thread::sleep(PROBE_STEP);
            let slept = before.elapsed().unwrap_or(PROBE_STEP);
            waited += PROBE_STEP;
            let resumed = inputwatch::resumed_from_sleep(PROBE_STEP, slept);
            if !resumed && waited < inputwatch::PROBE_INTERVAL {
                continue;
            }
            waited = Duration::ZERO;
            if resumed {
                // The counter and the fault both predate the suspend; start
                // the comparison over so the first probe after a resume is a
                // baseline rather than an instant verdict.
                deafness.reset();
            }
            unsafe {
                let target = webview_input_window(window_handle as HWND);
                if !target.is_null() {
                    post_probe_moves(target);
                }
            }
            probe_state.clear();
            if probe_proxy.send_event(AppEvent::ReportInput).is_err() {
                return; // window gone
            }
            std::thread::sleep(PROBE_REPLY_GRACE);
            match deafness.observe(probe_state.take()) {
                // Input is flowing again: any past rebuild is forgiven.
                Verdict::Healthy => budget.restore(),
                Verdict::Waiting => {}
                Verdict::Deaf => {
                    if !budget.take() {
                        eventlog::error(
                            "dashboard window keeps losing input; giving up on rebuilding it",
                        );
                        return;
                    }
                    if probe_proxy.send_event(AppEvent::RebuildWebView).is_err() {
                        return;
                    }
                }
            }
        }
    });

    event_loop.run_return(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                *control_flow = ControlFlow::Exit;
            }
            // The watchdog found a new endpoint; point the webview at it. The
            // guard skips a reload when the URL is unchanged, so a flap can't
            // loop the page.
            Event::UserEvent(AppEvent::Navigate(fresh_url)) if fresh_url != current_url => {
                if let Some(view) = &webview {
                    let _ = view.load_url(&fresh_url);
                }
                current_url = fresh_url;
            }
            Event::UserEvent(AppEvent::ReportInput) => {
                if let Some(view) = &webview {
                    let _ = view.evaluate_script(INPUT_REPORT_SCRIPT);
                }
            }
            // The window went deaf. A reload would not help — the fresh
            // document is just as deaf — so drop the webview and build a new
            // one, reopening the page the user was reading.
            Event::UserEvent(AppEvent::RebuildWebView) => {
                let target = probe.last_href().unwrap_or_else(|| current_url.clone());
                webview = None;
                match build_webview(&mut web_context, &window, &target, probe.clone()) {
                    Ok(fresh) => {
                        webview = Some(fresh);
                        eventlog::warn(
                            "dashboard window stopped receiving input; rebuilt its webview",
                        );
                    }
                    Err(_) => {
                        eventlog::error(
                            "dashboard window stopped receiving input and could not be rebuilt",
                        );
                        *control_flow = ControlFlow::Exit;
                    }
                }
            }
            _ => {}
        }
    })
}

/// The local page shown while the engine warms. Written fresh on every use
/// (it is tiny) so an old install can never pin an old look. Deliberately
/// static — no animation may gate visibility (the macOS blank-dashboard
/// lesson), and the copy says why a wait can be long the first time.
fn warming_page_url() -> String {
    let html = "<!doctype html><meta charset=\"utf-8\">\
<title>PracticeGraph</title>\
<style>\
html,body{height:100%;margin:0}\
body{display:flex;align-items:center;justify-content:center;\
background:#fcfbf8;color:#59655f;\
font:14px/1.6 \"Segoe UI\",system-ui,sans-serif}\
main{text-align:center;max-width:34em;padding:0 24px}\
h1{font-size:15px;font-weight:600;color:#1f2a27;margin:0 0 8px}\
p{margin:0}\
</style>\
<main><h1>Reading this machine&hellip;</h1>\
<p>The local engine is starting. After an update this can take a little \
longer while your history is read back from your tools&rsquo; own logs \
&mdash; nothing is lost, and this page becomes the dashboard the moment \
it is ready.</p></main>";
    let path = corerun::data_dir().join("warming.html");
    // Best-effort: if the write fails the webview shows a blank paper page
    // and the watchdog still navigates when the engine lands.
    let _ = std::fs::create_dir_all(corerun::data_dir());
    let _ = std::fs::write(&path, html);
    format!("file:///{}", path.display().to_string().replace('\\', "/"))
}

/// Window + taskbar icon from practicegraph.ico next to the exe (same
/// artifact the tray uses). Best-effort: the stock icon is fine as fallback.
fn apply_window_icon(window: &Window) {
    let icon_path = corerun::install_dir().join("practicegraph.ico");
    if !icon_path.is_file() {
        return;
    }
    let path_w = wide(&icon_path.display().to_string());
    unsafe {
        let icon = LoadImageW(
            std::ptr::null_mut(),
            path_w.as_ptr(),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        );
        if icon.is_null() {
            return;
        }
        let hwnd = window.hwnd() as HWND;
        SendMessageW(hwnd, WM_SETICON, ICON_SMALL as usize, icon as isize);
        SendMessageW(hwnd, WM_SETICON, ICON_BIG as usize, icon as isize);
    }
}

/// Nice-to-have on second launch: bring the open dashboard forward. The
/// tray's hidden message window shares the "PracticeGraph" title, so only
/// visible windows count.
unsafe fn focus_existing_window(title: &str) {
    let title = wide(title);
    let mut hwnd: HWND = std::ptr::null_mut();
    loop {
        hwnd = FindWindowExW(
            std::ptr::null_mut(),
            hwnd,
            std::ptr::null(),
            title.as_ptr(),
        );
        if hwnd.is_null() {
            return;
        }
        if IsWindowVisible(hwnd) != 0 {
            if IsIconic(hwnd) != 0 {
                ShowWindow(hwnd, SW_RESTORE);
            }
            SetForegroundWindow(hwnd);
            return;
        }
    }
}

/// WebView2 runtime missing or broken: say so once, then fall back to the
/// rendered report so the user still gets their data.
fn fallback_no_webview2() -> i32 {
    let text = wide(
        "PracticeGraph could not start its dashboard window because the \
         Microsoft Edge WebView2 Runtime is not available.\n\nPlease install \
         \"Microsoft Edge WebView2 Runtime\" (preinstalled on Windows 11; \
         available from Microsoft) and try again.\n\nOpening the latest \
         daily report instead.",
    );
    let caption = wide("PracticeGraph");
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            caption.as_ptr(),
            MB_OK | MB_ICONWARNING,
        );
    }
    corerun::open_latest_report(None)
}

/// One place to build the webview, because the input watchdog builds it again
/// when the window goes deaf. Every document gets the input counter injected.
fn build_webview(
    context: &mut wry::WebContext,
    window: &Window,
    url: &str,
    probe: Arc<ProbeReply>,
) -> wry::Result<wry::WebView> {
    wry::WebViewBuilder::new_with_web_context(context)
        .with_url(url)
        .with_initialization_script(INPUT_COUNTER_SCRIPT)
        .with_ipc_handler(move |request| probe.record(request.body()))
        // News and report links deliberately use `target=_blank`. WebView2
        // asks the host to create that window; hand ordinary web links to the
        // user's default browser — plus the one allowlisted codex: composer
        // deep link — instead of silently discarding the request.
        .with_new_window_req_handler(|requested_url, _| {
            corerun::open_external_link(&requested_url);
            wry::NewWindowResponse::Deny
        })
        .build(window)
}

/// The WebView2 child window that receives the mouse. wry nests the browser's
/// own windows under its host window; posting to the innermost one is what
/// actually reaches the page (verified against a healthy window 2026-09-21 —
/// posts to the outer two are ignored).
unsafe fn webview_input_window(parent: HWND) -> HWND {
    let mut hwnd = parent;
    for class in ["WRY_WEBVIEW", "Chrome_WidgetWin_0", "Chrome_WidgetWin_1"] {
        let class_w = wide(class);
        hwnd = FindWindowExW(hwnd, std::ptr::null_mut(), class_w.as_ptr(), std::ptr::null());
        if hwnd.is_null() {
            return std::ptr::null_mut();
        }
    }
    hwnd
}

/// A few pixels in the page's top-left corner, where a hover means nothing.
/// These never move the real cursor: they are messages, not input events.
unsafe fn post_probe_moves(target: HWND) {
    for step in 0..3i32 {
        let position = ((4 + step) << 16) | (4 + step);
        PostMessageW(target, WM_MOUSEMOVE, 0, position as isize);
    }
}
