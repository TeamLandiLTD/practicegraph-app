//! Native dashboard window (`app` mode): the local UI served by the core
//! engine, hosted in an evergreen WebView2 inside a plain tao window — the
//! dashboard never opens in a browser tab. The shell stays read-only: the
//! engine (`ui serve`) is launched through the core CLI and owns all state
//! (C-4); this process only ensures it is up and points a webview at it.

use std::time::Duration;

use tao::dpi::{LogicalSize, PhysicalPosition};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::platform::run_return::EventLoopExtRunReturn;
use tao::platform::windows::{WindowBuilderExtWindows, WindowExtWindows};
use tao::window::{Window, WindowBuilder};
use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS, HWND};
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    FindWindowExW, IsIconic, IsWindowVisible, LoadImageW, MessageBoxW, SendMessageW,
    SetForegroundWindow, ShowWindow, ICON_BIG, ICON_SMALL, IMAGE_ICON, LR_DEFAULTSIZE,
    LR_LOADFROMFILE, MB_ICONWARNING, MB_OK, SW_RESTORE, WM_SETICON,
};

use crate::corerun::{self, wide};

const DEFAULT_SIZE: (f64, f64) = (1280.0, 900.0);
const MIN_SIZE: (f64, f64) = (900.0, 600.0);
// How often the window checks its server is still alive. Short enough that a
// restart recovers in a couple of seconds, long enough to be free.
const WATCHDOG_INTERVAL: Duration = Duration::from_secs(3);

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
    let mut event_loop = EventLoopBuilder::<String>::with_user_event().build();
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
    // resizes on its own.
    let webview = match wry::WebViewBuilder::new_with_web_context(&mut web_context)
        .with_url(&url)
        // News and report links deliberately use `target=_blank`. WebView2
        // asks the host to create that window; hand ordinary web links to the
        // user's default browser — plus the one allowlisted codex: composer
        // deep link — instead of silently discarding the request.
        .with_new_window_req_handler(|requested_url, _| {
            corerun::open_external_link(&requested_url);
            wry::NewWindowResponse::Deny
        })
        .build(&window)
    {
        Ok(webview) => webview,
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
                if proxy.send_event(fresh_url).is_err() {
                    return;
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
            Event::UserEvent(fresh_url) if fresh_url != current_url => {
                let _ = webview.load_url(&fresh_url);
                current_url = fresh_url;
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
