//
//  main.swift
//  PracticeGraph macOS shell (spec §9 C-2, macOS edition of shell/ Rust).
//
//  One small app, the same handful of jobs the Windows shell does:
//    - a menu-bar presence icon (working / quiet), left-click opens the app
//    - the dashboard in a native WKWebView window (never a browser tab)
//    - the practicegraph: URL scheme for protocol links (closed verbs)
//    - launch-at-login (SMAppService), the analogue of the HKMU Run key
//
//  The shell never analyzes anything and never writes state: it launches the
//  core CLI (C-4, C-6) and reads only world-readable artifacts (ui.json,
//  rendered reports). There is no service host here: while the app runs it
//  starts the periodic agent tick itself through the core CLI when one is due
//  (BackgroundRefresh.swift); the optional LaunchAgent in packaging/macos
//  keeps ticking while the app is closed.
//
//  Entry point: a plain AppKit app driven by an NSApplicationDelegate. It is
//  an accessory (LSUIElement) — menu-bar-resident, no Dock icon — but it does
//  open real windows, so activation policy is nudged to .regular while a
//  window is visible.
//

import AppKit
import UserNotifications

/// Static app metadata read from the bundle Info.plist, with dev fallbacks so
/// the bare SwiftPM binary still runs.
enum AppInfo {
    static var version: String {
        (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0.1.0"
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate,
                         UNUserNotificationCenterDelegate {

    private let dashboard = DashboardWindowController()
    private var menuBar: MenuBarController?
    private let refresher = BackgroundRefresher()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Accessory app: live in the menu bar, no Dock tile. We temporarily
        // become a regular app when showing a window (see below) so the
        // dashboard behaves like a normal window (Cmd-Tab, Dock while open).
        NSApp.setActivationPolicy(.accessory)

        // Before any window: macOS routes key equivalents through the main
        // menu, so without one there is no Cmd-C/V/A/W/Q anywhere in the app.
        MainMenu.install()

        menuBar = MenuBarController(dashboard: dashboard)

        // The agent tick pulls the editions `ui serve` does not (models,
        // benchmarks, advisor, docs, skills, rate card). Runs whether or not
        // this launch opens a window; skipped when a tick ran recently.
        refresher.start()

        // Register the Apple Event handler for the practicegraph: URL scheme.
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleURLEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )

        // Notification click-through (the macOS half of the Windows toast
        // door): BreakNotifier posts from a short-lived second instance; the
        // click lands HERE, in the resident app, and carries a closed verb.
        // Delegate + authorization only apply inside a real bundle — the bare
        // SwiftPM dev binary has no notification identity.
        if Bundle.main.bundleIdentifier != nil {
            let center = UNUserNotificationCenter.current()
            center.delegate = self
            center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }

        // Regular-app launch behavior (matching the Windows tray opening the
        // dashboard once on a user-initiated start): open the window unless we
        // were launched as a login item, in which case start quiet — signing
        // in must never force a window open.
        if !LaunchContext.launchedAsLoginItem {
            dashboard.show()
        }
    }

    /// A tapped notification. The verb set is closed exactly like the URL
    /// scheme's: "break" opens the guided break; anything else is a plain
    /// open. (kept in sync with BreakNotifier / the Windows toast verbs).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let verb = response.notification.request.content
            .userInfo["verb"] as? String
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if verb == "break" {
                self.dashboard.show(startBreak: true)
            } else {
                self.dashboard.show()
            }
        }
        completionHandler()
    }

    // macOS 10.15+ delivers URL opens here as well; keep both paths so the
    // scheme works whether the OS routes via Apple Events or this selector.
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls where url.scheme == "practicegraph" {
            ProtocolRouter.handle(url, dashboard: dashboard)
        }
    }

    @objc private func handleURLEvent(
        _ event: NSAppleEventDescriptor,
        withReplyEvent replyEvent: NSAppleEventDescriptor
    ) {
        guard
            let raw = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
            let url = URL(string: raw)
        else { return }
        ProtocolRouter.handle(url, dashboard: dashboard)
    }

    // Keep the app alive when the dashboard window closes — the menu-bar item
    // is the app's home, exactly like the tray keeps the Windows process up.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}

/// Detecting whether we were auto-started as a login item, so the autostart
/// path can stay quiet (no window) — the analogue of the Windows `tray
/// --silent` logon autostart, which starts the icon without popping a window.
enum LaunchContext {
    // Apple Event OSType codes, spelled as literals so this does not depend on
    // the Carbon constants being bridged into Swift (they are frozen ABI
    // four-char codes: 'aevt', 'oapp', 'prdt', 'lgit'). See AppleEvents.h /
    // AERegistry.h.
    private static let coreEventClass = fourCharCode("aevt")     // kCoreEventClass
    private static let openApplication = fourCharCode("oapp")    // kAEOpenApplication
    private static let propData = fourCharCode("prdt")           // keyAEPropData
    private static let launchedAsLogInItem = fourCharCode("lgit") // keyAELaunchedAsLogInItem

    /// True when this launch came from the SMAppService login item rather than
    /// a user action (double-click, `open`, Dock).
    ///
    /// The signal is the launch Apple Event: a login-item / system-reopened
    /// launch is a `kAEOpenApplication` event carrying the `keyAEPropData`
    /// reopen descriptor with `keyAELaunchedAsLogInItem` set. A normal
    /// double-click sends `kAEOpenApplication` WITHOUT that flag; opening a
    /// document/URL sends a different event class. This is Apple's documented
    /// way to tell the two apart at launch. A false negative merely opens the
    /// window when we could have stayed quiet — harmless.
    static var launchedAsLoginItem: Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent,
              event.eventClass == coreEventClass,
              event.eventID == openApplication,
              let reopen = event.paramDescriptor(forKeyword: propData)
        else { return false }
        return reopen.paramDescriptor(forKeyword: launchedAsLogInItem)?
            .booleanValue ?? false
    }
}

/// Pack a 4-character ASCII string into a big-endian OSType/FourCharCode, the
/// encoding Apple Event class/ID/keyword codes use. Returns the raw UInt32 so
/// it unifies with AEEventClass / AEEventID / AEKeyword (all UInt32 typealiases).
private func fourCharCode(_ string: StaticString) -> UInt32 {
    precondition(string.utf8CodeUnitCount == 4, "four-char code must be 4 bytes")
    var result: UInt32 = 0
    string.withUTF8Buffer { buffer in
        for byte in buffer {
            result = (result << 8) | UInt32(byte)
        }
    }
    return result
}

/// The notification poster: the macOS mirror of the Windows `toast` mode,
/// where the ENGINE (the LaunchAgent tick) invokes this same binary as a
/// short-lived second process to fire one user notification, then exits.
/// The tap is delivered by the OS to the resident app instance (relaunching
/// it if needed), which reads the closed verb in AppDelegate above.
///
///     PracticeGraphShell --notify <verb> <title> <body>
///
/// Closed verbs only: "break" deep-links into the guided break; anything
/// else degrades to a plain open. Posting requires a real bundle identity —
/// the bare SwiftPM dev binary reports unavailable instead of trapping.
enum BreakNotifier {
    static func runIfRequested() {
        let arguments = CommandLine.arguments
        guard let flag = arguments.firstIndex(of: "--notify") else { return }
        guard arguments.count >= flag + 4 else { exit(2) }
        guard Bundle.main.bundleIdentifier != nil else {
            FileHandle.standardError.write(
                Data("notify requires the app bundle\n".utf8))
            exit(1)
        }
        let verb = arguments[flag + 1] == "break" ? "break" : "open-report"
        let content = UNMutableNotificationContent()
        content.title = arguments[flag + 2]
        content.body = arguments[flag + 3]
        content.userInfo = ["verb": verb]

        let center = UNUserNotificationCenter.current()
        var outcome: Int32 = 1
        let done = DispatchSemaphore(value: 0)
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else { done.signal(); return }
            let request = UNNotificationRequest(
                identifier: "practicegraph-\(verb)",
                content: content,
                trigger: nil
            )
            center.add(request) { error in
                outcome = error == nil ? 0 : 1
                done.signal()
            }
        }
        // Bounded wait for the async post; the engine side times out at 20s.
        _ = done.wait(timeout: .now() + 10)
        exit(outcome)
    }
}

// Boot. Notify mode short-circuits before any AppKit UI exists.
BreakNotifier.runIfRequested()
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
