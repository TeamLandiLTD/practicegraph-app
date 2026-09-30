//! `stop-running <install-dir>`: end every PracticeGraph process whose image
//! lives under the install directory, so an installer can replace the files.
//!
//! Field report 2026-09-17: a user installed 0.2.19 and their window was
//! still served by a 0.1.31 engine from six days earlier. The MSI relied on
//! Windows' Restart Manager, which cannot close a windowless background
//! process; the locked `python.exe`, its DLLs and the running shell therefore
//! stayed OLD on disk until a reboot, and the old shell never asks for a new
//! engine. The installer now runs this verb from its own embedded copy of the
//! shell, before it validates files in use.
//!
//! Targeting is by IMAGE PATH, never by name: the engine's interpreter is
//! called `python.exe`, and ending every Python on the machine would be
//! unforgivable. Only processes running from inside the given directory are
//! touched, the directory must look like a PracticeGraph install, and this
//! process is never its own target.

use std::path::Path;

/// Trailing quote/backslash noise from `"[INSTALLFOLDER]"` (the directory
/// property ends with a backslash, which escapes the closing quote).
pub fn clean_dir_argument(raw: &str) -> String {
    raw.trim()
        .trim_matches('"')
        .trim_end_matches(['\\', '/'])
        .to_string()
}

fn normalised(path: &str) -> String {
    path.replace('/', "\\").trim_end_matches('\\').to_lowercase()
}

/// Whether `image` is a file inside `install_dir` (case-insensitive, either
/// separator). A sibling such as `...\PracticeGraph-old\x.exe` is NOT inside.
pub fn is_inside(image: &str, install_dir: &str) -> bool {
    let dir = normalised(install_dir);
    let image = normalised(image);
    !dir.is_empty() && image.len() > dir.len() + 1 && image.starts_with(&dir)
        && image.as_bytes()[dir.len()] == b'\\'
}

/// A directory this verb may act on: deep enough never to be a drive or a
/// profile root, and recognisably ours.
pub fn looks_like_install_dir(dir: &Path) -> bool {
    dir.components().count() >= 4
        && (dir.join("practicegraph-shell.exe").is_file() || dir.join("runtime").is_dir())
}

#[cfg(windows)]
pub fn stop_running(raw_dir: &str) -> i32 {
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcessId, OpenProcess, QueryFullProcessImageNameW, TerminateProcess,
        WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    };

    const SYNCHRONIZE: u32 = 0x0010_0000;
    let dir = clean_dir_argument(raw_dir);
    if !looks_like_install_dir(Path::new(&dir)) {
        return 0; // a first install, or not ours: nothing to stop, never an error
    }
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return 0;
        }
        let own = GetCurrentProcessId();
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut more = Process32FirstW(snapshot, &mut entry) != 0;
        while more {
            let pid = entry.th32ProcessID;
            if pid != own && pid != 0 {
                let process = OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE,
                    0,
                    pid,
                );
                if !process.is_null() {
                    let mut buffer = [0u16; 1024];
                    let mut length = buffer.len() as u32;
                    if QueryFullProcessImageNameW(process, 0, buffer.as_mut_ptr(), &mut length) != 0
                    {
                        let image = String::from_utf16_lossy(&buffer[..length as usize]);
                        if is_inside(&image, &dir) && TerminateProcess(process, 0) != 0 {
                            // Wait for the image to unlock, bounded.
                            WaitForSingleObject(process, 5000);
                        }
                    }
                    CloseHandle(process);
                }
            }
            more = Process32NextW(snapshot, &mut entry) != 0;
        }
        CloseHandle(snapshot);
    }
    0
}

#[cfg(not(windows))]
pub fn stop_running(_raw_dir: &str) -> i32 {
    0
}

#[cfg(test)]
mod tests {
    use super::{clean_dir_argument, is_inside};

    #[test]
    fn only_images_inside_the_install_directory_are_targets() {
        let dir = r"C:\Users\someone\AppData\Local\Programs\PracticeGraph";
        assert!(is_inside(r"C:\Users\someone\AppData\Local\Programs\PracticeGraph\runtime\python.exe", dir));
        assert!(is_inside(r"c:\users\SOMEONE\appdata\local\programs\practicegraph\practicegraph-shell.exe", dir));
        assert!(is_inside("C:/Users/someone/AppData/Local/Programs/PracticeGraph/runtime/python.exe", dir));
        // Never another Python, a sibling folder, or the directory itself.
        assert!(!is_inside(r"C:\Python314\python.exe", dir));
        assert!(!is_inside(r"C:\Users\someone\AppData\Local\Programs\PracticeGraph-old\runtime\python.exe", dir));
        assert!(!is_inside(dir, dir));
        assert!(!is_inside(r"C:\anything\python.exe", ""));
    }

    #[test]
    fn the_installer_directory_argument_is_cleaned() {
        // "[INSTALLFOLDER]" ends with a backslash, which escapes the closing quote.
        assert_eq!(clean_dir_argument("C:\\Apps\\PracticeGraph\\\""), r"C:\Apps\PracticeGraph");
        assert_eq!(clean_dir_argument("\"C:\\Apps\\PracticeGraph\\\""), r"C:\Apps\PracticeGraph");
        assert_eq!(clean_dir_argument(r"C:\Apps\PracticeGraph"), r"C:\Apps\PracticeGraph");
    }
}
