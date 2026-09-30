//! Per-user tray application: a plain win32 notification-area icon with a
//! small menu, and — since the per-user install dropped the LocalSystem
//! service — the HOST OF THE AGENT TICK. Read-only by design: every menu
//! action either opens an already-rendered artifact or launches the core CLI
//! (C-4, C-6). Survives explorer restarts (TaskbarCreated re-add) and enforces
//! a single instance.
//!
//! **Why a tray thread and not a scheduled task.** Registering a task needs a
//! custom action, and C-3 (the MSI has none) is worth keeping, because the
//! agent is a DERIVER, not a recorder: everything it knows comes from log
//! files Claude Code and Codex write, and `history.ingest` walks them with
//! cursors. A tray that was closed for a week costs a delay, never data — the
//! next tick reads the same files and catches up. That is a different risk
//! profile from a telemetry agent that must be running to capture events, and
//! it is what makes the simple design safe.
//!
//! The single-instance mutex below is what stops two ticks racing: a second
//! tray exits before it ever starts a thread.

#![allow(clippy::upper_case_acronyms)]

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS, HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY,
    NOTIFYICONDATAW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DispatchMessageW,
    GetCursorPos, GetMessageW, LoadIconW, LoadImageW, MessageBoxW, PostMessageW, PostQuitMessage,
    RegisterClassW, RegisterWindowMessageW, SetForegroundWindow, SetTimer, TrackPopupMenu,
    TranslateMessage, HICON, IDI_APPLICATION, IMAGE_ICON, LR_DEFAULTSIZE, LR_LOADFROMFILE,
    MB_ICONINFORMATION, MB_OK, MF_GRAYED, MF_SEPARATOR, MF_STRING, MSG, TPM_BOTTOMALIGN,
    TPM_RIGHTBUTTON, WM_COMMAND, WM_CONTEXTMENU, WM_DESTROY, WM_LBUTTONDBLCLK, WM_LBUTTONUP,
    WM_NULL, WM_RBUTTONUP, WM_TIMER, WNDCLASSW, WS_OVERLAPPED,
};

use crate::corerun::{self, wide};

const WM_TRAY_CALLBACK: u32 = 0x8000 + 1; // WM_APP + 1
const CMD_DASHBOARD: usize = 1;
const CMD_ABOUT: usize = 2;
const CMD_EXIT: usize = 3;
const CMD_NEWS: usize = 4;
const CMD_REOPEN: usize = 5;

// Presence: the icon reflects whether AI-tool logs changed recently
// ("working" vs "quiet") — polled every five minutes, shown in the tooltip
// and as the first (informational) menu line.
const PRESENCE_TIMER_ID: usize = 1;
const PRESENCE_POLL_MS: u32 = 5 * 60 * 1000;

static TASKBAR_CREATED_MSG: AtomicU32 = AtomicU32::new(0);
static WORKING: AtomicBool = AtomicBool::new(false);

/// The scheduler, hosted by the tray in a per-user install. Runs the SAME tick
/// the machine-wide service runs — one implementation, two hosts — so the two
/// flavours can never drift into doing different work.
///
/// A failing tick is recorded and the loop continues (NFR-REL-1/2). The first
/// tick is immediate so a fresh install shows real numbers rather than an
/// empty page while a 15-minute timer runs down.
fn agent_loop() {
    let interval = crate::service::tick_interval();
    loop {
        let _outcome = crate::service::run_one_tick();
        std::thread::sleep(interval);
    }
}

pub fn run(silent: bool) -> i32 {
    unsafe {
        // Single instance per session. If the tray is already running, this
        // launch (e.g. clicking the Start-menu shortcut again) should behave
        // like reopening a normal app: surface the dashboard window rather than
        // do nothing, then exit and leave the existing tray in place. The
        // silent logon autostart is the exception — it must not pop a window.
        let mutex_name = wide("Local\\PracticeGraphTray");
        let mutex = CreateMutexW(std::ptr::null(), 0, mutex_name.as_ptr());
        if mutex.is_null() || GetLastError() == ERROR_ALREADY_EXISTS {
            if !silent {
                corerun::open_dashboard();
            }
            return 0;
        }

        // Past the mutex: this process is THE tray for this session, so it
        // owns the scheduler. Detached because the tick shells out to the core
        // and must never block the message pump — a stalled tick would freeze
        // the icon, and an unresponsive tray is worse than a late number.
        std::thread::spawn(agent_loop);

        let instance = GetModuleHandleW(std::ptr::null());
        let class_name = wide("PracticeGraphTrayWindow");
        let class = WNDCLASSW {
            style: 0,
            lpfnWndProc: Some(window_proc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: instance,
            hIcon: std::ptr::null_mut(),
            hCursor: std::ptr::null_mut(),
            hbrBackground: std::ptr::null_mut(),
            lpszMenuName: std::ptr::null(),
            lpszClassName: class_name.as_ptr(),
        };
        if RegisterClassW(&class) == 0 {
            return 1;
        }

        TASKBAR_CREATED_MSG.store(
            RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()),
            Ordering::Relaxed,
        );

        let window_name = wide("PracticeGraph");
        let hwnd = CreateWindowExW(
            0,
            class_name.as_ptr(),
            window_name.as_ptr(),
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            instance,
            std::ptr::null(),
        );
        if hwnd.is_null() {
            return 1;
        }

        add_tray_icon(hwnd);
        update_presence(hwnd);
        SetTimer(hwnd, PRESENCE_TIMER_ID, PRESENCE_POLL_MS, None);

        // Regular app behavior: a user-initiated launch (Start-menu shortcut)
        // also opens the dashboard window once. The tray then keeps running in
        // the background even when the window is closed (left-click or the
        // shortcut reopens it). The silent logon autostart skips this.
        if !silent {
            corerun::open_dashboard();
        }

        let mut message = std::mem::zeroed::<MSG>();
        while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        0
    }
}

unsafe fn load_icon(active: bool) -> HICON {
    // The install ships two icons: the base mark and a green-dot "working"
    // variant. Fall back to the base, then to the stock application icon,
    // so the tray never silently disappears.
    if active {
        let working = corerun::install_dir().join("practicegraph-working.ico");
        if working.is_file() {
            let path_w = wide(&working.display().to_string());
            let handle = LoadImageW(
                std::ptr::null_mut(),
                path_w.as_ptr(),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            );
            if !handle.is_null() {
                return handle as HICON;
            }
        }
    }
    let icon_path = corerun::install_dir().join("practicegraph.ico");
    if icon_path.is_file() {
        let path_w = wide(&icon_path.display().to_string());
        let handle = LoadImageW(
            std::ptr::null_mut(),
            path_w.as_ptr(),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        );
        if !handle.is_null() {
            return handle as HICON;
        }
    }
    LoadIconW(std::ptr::null_mut(), IDI_APPLICATION)
}

unsafe fn tray_data(hwnd: HWND) -> NOTIFYICONDATAW {
    let mut data = std::mem::zeroed::<NOTIFYICONDATAW>();
    data.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
    data.hWnd = hwnd;
    data.uID = 1;
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    data.uCallbackMessage = WM_TRAY_CALLBACK;
    data.hIcon = load_icon(false);
    set_tip(&mut data, "PracticeGraph");
    data
}

unsafe fn set_tip(data: &mut NOTIFYICONDATAW, tip: &str) {
    data.szTip = [0; 128];
    let tip_w: Vec<u16> = tip.encode_utf16().collect();
    let count = tip_w.len().min(data.szTip.len() - 1);
    data.szTip[..count].copy_from_slice(&tip_w[..count]);
}

unsafe fn update_presence(hwnd: HWND) {
    let active = corerun::presence_active();
    WORKING.store(active, Ordering::Relaxed);
    let mut data = tray_data(hwnd);
    data.hIcon = load_icon(active);
    set_tip(
        &mut data,
        if active {
            "PracticeGraph - working now (local only)"
        } else {
            "PracticeGraph - quiet (local only)"
        },
    );
    let unread = corerun::news_unread();
    if unread > 0 {
        set_tip(&mut data, &format!("PracticeGraph - {unread} unread news stories"));
    }
    Shell_NotifyIconW(NIM_MODIFY, &data);
}

unsafe fn add_tray_icon(hwnd: HWND) {
    let data = tray_data(hwnd);
    Shell_NotifyIconW(NIM_ADD, &data);
}

unsafe fn remove_tray_icon(hwnd: HWND) {
    let data = tray_data(hwnd);
    Shell_NotifyIconW(NIM_DELETE, &data);
}

unsafe fn show_menu(hwnd: HWND) {
    let menu = CreatePopupMenu();
    if menu.is_null() {
        return;
    }
    let status = if WORKING.load(Ordering::Relaxed) {
        wide("Status: working now")
    } else {
        wide("Status: quiet")
    };
    AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, status.as_ptr());
    AppendMenuW(menu, MF_SEPARATOR, 0, std::ptr::null());
    let news_label = format!("Latest news ({} unread)", corerun::news_unread());
    let items: [(usize, &str); 4] = [
        (CMD_DASHBOARD, "Open PracticeGraph"),
        (CMD_NEWS, &news_label),
        // The window's own watchdog rebuilds a deaf webview within a couple of
        // minutes; this is the door for someone who does not want to wait.
        (CMD_REOPEN, "Reopen window (if clicks do nothing)"),
        (CMD_ABOUT, "About PracticeGraph"),
    ];
    for (id, label) in items {
        let label_w = wide(label);
        AppendMenuW(menu, MF_STRING, id, label_w.as_ptr());
        // A divider sets the dashboard (the daily driver) off from About.
        if id == CMD_DASHBOARD {
            AppendMenuW(menu, MF_SEPARATOR, 0, std::ptr::null());
        }
    }
    let exit_w = wide("Exit");
    AppendMenuW(menu, MF_STRING, CMD_EXIT, exit_w.as_ptr());

    let mut point = POINT { x: 0, y: 0 };
    GetCursorPos(&mut point);
    // Required so the menu closes when the user clicks elsewhere.
    SetForegroundWindow(hwnd);
    TrackPopupMenu(
        menu,
        TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
        point.x,
        point.y,
        0,
        hwnd,
        std::ptr::null(),
    );
    PostMessageW(hwnd, WM_NULL, 0, 0);
    DestroyMenu(menu);
}

unsafe fn about(hwnd: HWND) {
    let text = wide(concat!(
        "PracticeGraph ",
        env!("CARGO_PKG_VERSION"),
        "\n\nAll analysis runs locally on this machine.\n",
        "Sharing anonymous org aggregates is opt-in (see the Privacy view in the app)."
    ));
    let caption = wide("About PracticeGraph");
    MessageBoxW(hwnd, text.as_ptr(), caption.as_ptr(), MB_OK | MB_ICONINFORMATION);
}

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == TASKBAR_CREATED_MSG.load(Ordering::Relaxed) && message != 0 {
        // Explorer restarted: the notification area was rebuilt.
        add_tray_icon(hwnd);
        return 0;
    }
    match message {
        WM_TIMER if wparam == PRESENCE_TIMER_ID => {
            update_presence(hwnd);
            0
        }
        WM_TRAY_CALLBACK => {
            let event = (lparam & 0xFFFF) as u32;
            match event {
                WM_RBUTTONUP | WM_CONTEXTMENU => show_menu(hwnd),
                // Left-click = the daily driver: the dashboard in its own
                // native window (spawns this exe detached in `app` mode —
                // never a browser tab).
                WM_LBUTTONUP | WM_LBUTTONDBLCLK => {
                    corerun::open_dashboard();
                }
                _ => {}
            }
            0
        }
        WM_COMMAND => {
            match wparam & 0xFFFF {
                CMD_DASHBOARD => {
                    corerun::open_dashboard();
                }
                CMD_ABOUT => about(hwnd),
                CMD_NEWS => { corerun::open_news(false); }
                CMD_REOPEN => { corerun::reopen_dashboard(); }
                CMD_EXIT => {
                    remove_tray_icon(hwnd);
                    PostQuitMessage(0);
                }
                _ => {}
            }
            0
        }
        WM_DESTROY => {
            remove_tray_icon(hwnd);
            PostQuitMessage(0);
            0
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}
