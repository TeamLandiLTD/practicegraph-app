//! WinRT toast notifications (FR-ALR-4, C-5). Baseline interaction is the
//! whole-toast click, which protocol-activates `practicegraph:` (registered by
//! the MSI) and opens the requested report or news window. Private-observation
//! text is closed by callers; editorial news may use validated public copy.

use windows::core::HSTRING;
use windows::Data::Xml::Dom::XmlDocument;
use windows::UI::Notifications::{ToastNotification, ToastNotificationManager};
use windows::Win32::System::WinRT::{RoInitialize, RO_INIT_MULTITHREADED};

/// Must match the AppUserModelID on the MSI's Start-menu shortcut (C-2).
pub const AUMID: &str = "PracticeGraph.Agent";

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

pub fn show(title: &str, body: &str) -> windows::core::Result<()> {
    show_launch(title, body, None)
}

/// Tap-through with a CLOSED verb set: the toast may deep-link only to
/// places the protocol dispatch already knows. Anything unrecognized falls
/// back to the report open, so a caller can never smuggle an arbitrary
/// activation into the notification.
pub fn show_launch(
    title: &str,
    body: &str,
    launch: Option<&str>,
) -> windows::core::Result<()> {
    let target = match launch {
        Some("break") => "practicegraph:break",
        Some("news") => "practicegraph:news",
        Some("update") => "practicegraph:update",
        _ => "practicegraph:open-report",
    };
    // Explicit apartment init; RPC_E_CHANGED_MODE means one already exists.
    unsafe {
        let _ = RoInitialize(RO_INIT_MULTITHREADED);
    }
    let payload = format!(
        "<toast activationType=\"protocol\" launch=\"{}\">\
         <visual><binding template=\"ToastGeneric\">\
         <text>{}</text><text>{}</text>\
         </binding></visual></toast>",
        target,
        xml_escape(title),
        xml_escape(body)
    );
    let xml = XmlDocument::new()?;
    xml.LoadXml(&HSTRING::from(payload))?;
    let toast = ToastNotification::CreateToastNotification(&xml)?;
    let notifier = ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from(AUMID))?;
    notifier.Show(&toast)?;
    // Give the shell notification pipeline a moment before the process exits.
    std::thread::sleep(std::time::Duration::from_millis(1500));
    Ok(())
}
