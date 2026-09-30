//! `status` subcommand: one-glance install health for pilots and support.
//! Read-only apart from a create-and-delete probe file that answers "can
//! this user write the data dir?".
//!
//! Plain lines go to stdout (invisible under the GUI subsystem unless
//! redirected — the accepted `--version` trade-off); `--ui` additionally
//! shows the same summary in a message box so the Start-menu shortcut works
//! without a console. Exit 0 when everything is good, 1 otherwise.

use std::path::Path;

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ACCESS_DENIED};
use windows_sys::Win32::System::Services::{
    CloseServiceHandle, OpenSCManagerW, OpenServiceW, QueryServiceStatus, SC_MANAGER_CONNECT,
    SERVICE_QUERY_STATUS, SERVICE_RUNNING, SERVICE_STATUS,
};
use windows_sys::Win32::System::Threading::OpenMutexW;

use crate::corerun::{self, wide, CoreKind};
use crate::service::SERVICE_NAME;

/// Single-instance mutex the tray holds for its lifetime (tray.rs).
const TRAY_MUTEX: &str = "Local\\PracticeGraphTray";
/// Generic SYNCHRONIZE access right (winnt.h) — enough to probe existence.
const SYNCHRONIZE: u32 = 0x0010_0000;

#[derive(Clone, Copy, PartialEq)]
enum ServiceState {
    Absent,
    Stopped,
    Running,
}

impl ServiceState {
    fn label(self) -> &'static str {
        match self {
            ServiceState::Absent => "absent",
            ServiceState::Stopped => "stopped",
            ServiceState::Running => "running",
        }
    }
}

/// PracticeGraphAgent state via the SCM (read-only access rights). Anything
/// transitional (start/stop pending) reports as the closed label "stopped".
fn service_state() -> ServiceState {
    unsafe {
        let scm = OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_CONNECT);
        if scm.is_null() {
            return ServiceState::Absent;
        }
        let name = wide(SERVICE_NAME);
        let service = OpenServiceW(scm, name.as_ptr(), SERVICE_QUERY_STATUS);
        let state = if service.is_null() {
            ServiceState::Absent
        } else {
            let mut status = std::mem::zeroed::<SERVICE_STATUS>();
            let queried = QueryServiceStatus(service, &mut status) != 0;
            CloseServiceHandle(service);
            if queried && status.dwCurrentState == SERVICE_RUNNING {
                ServiceState::Running
            } else {
                ServiceState::Stopped
            }
        };
        CloseServiceHandle(scm);
        state
    }
}

/// A live tray in this session holds the single-instance mutex: opening it
/// succeeds, or fails with access-denied (still proof it exists).
fn tray_running() -> bool {
    unsafe {
        let name = wide(TRAY_MUTEX);
        let handle = OpenMutexW(SYNCHRONIZE, 0, name.as_ptr());
        if handle.is_null() {
            return GetLastError() == ERROR_ACCESS_DENIED;
        }
        CloseHandle(handle);
        true
    }
}

fn data_dir_writable(dir: &Path) -> bool {
    if !dir.is_dir() {
        return false;
    }
    let probe = dir.join(format!(".pg-status-probe-{}", std::process::id()));
    match std::fs::write(&probe, b"practicegraph status probe") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

pub fn run(ui: bool) -> i32 {
    let service = service_state();
    let tray = tray_running();
    let core = corerun::core_kind();
    let data_dir = corerun::data_dir();
    let writable = data_dir_writable(&data_dir);

    // "Core present" accepts either flavor: the compiled engine (engine
    // bundle) or the embedded Python runtime (dev/runtime bundle).
    let engine_line = match core {
        CoreKind::Engine => "present",
        CoreKind::Runtime => "absent (embedded Python runtime present)",
        CoreKind::Missing => "absent",
    };
    let all_good = service == ServiceState::Running
        && tray
        && !matches!(core, CoreKind::Missing)
        && writable;

    let lines = [
        format!("service: {}", service.label()),
        format!("tray: {}", if tray { "running" } else { "not running" }),
        format!("engine: {engine_line}"),
        format!(
            "data-dir: {} ({})",
            data_dir.display(),
            if writable { "writable" } else { "not writable" }
        ),
        format!("overall: {}", if all_good { "OK" } else { "attention needed" }),
    ];
    for line in &lines {
        println!("{line}");
    }
    if ui {
        corerun::message_box("PracticeGraph status", &lines.join("\n"), !all_good);
    }
    i32::from(!all_good)
}
