//! Windows Event Log reporting for service lifecycle (closed strings only —
//! never content, never paths beyond the install itself; NFR-PRV-1 applies to
//! logs too). The event source is registered by the MSI.

use windows_sys::Win32::System::EventLog::{
    DeregisterEventSource, RegisterEventSourceW, ReportEventW, EVENTLOG_ERROR_TYPE,
    EVENTLOG_WARNING_TYPE,
};

use crate::corerun::wide;

const SOURCE: &str = "PracticeGraph";
const GENERIC_EVENT_ID: u32 = 1;

pub fn warn(message: &str) {
    report(EVENTLOG_WARNING_TYPE, message);
}

pub fn error(message: &str) {
    report(EVENTLOG_ERROR_TYPE, message);
}

fn report(level: u16, message: &str) {
    unsafe {
        let source = wide(SOURCE);
        let handle = RegisterEventSourceW(std::ptr::null(), source.as_ptr());
        if handle.is_null() {
            return; // logging must never take the service down (NFR-REL-1)
        }
        let text = wide(message);
        let mut strings = [text.as_ptr()];
        ReportEventW(
            handle,
            level,
            0,
            GENERIC_EVENT_ID,
            std::ptr::null_mut(),
            1,
            0,
            strings.as_mut_ptr(),
            std::ptr::null(),
        );
        DeregisterEventSource(handle);
    }
}
