//
//  MenuBarController.swift
//  The macOS mirror of shell/src/tray.rs.
//
//  A menu-bar (status-bar) presence icon with a small menu. Read-only by
//  design — every action either opens an already-rendered artifact or
//  launches the core CLI (C-4, C-6). The icon reflects whether AI-tool logs
//  changed recently ("working" vs "quiet"), polled every five minutes and
//  shown as a template image plus a disabled header line in the menu.
//

import AppKit
import ServiceManagement

final class MenuBarController: NSObject {

    // Presence: polled every five minutes, exactly like the Windows tray.
    private static let presencePollInterval: TimeInterval = 5 * 60

    private let statusItem: NSStatusItem
    private let dashboard: DashboardWindowController
    private var presenceTimer: Timer?
    private var working = false

    // Menu items whose state we refresh on open.
    private let statusHeaderItem = NSMenuItem(title: "Status: quiet", action: nil, keyEquivalent: "")
    private let launchAtLoginItem = NSMenuItem(
        title: "Open at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")

    init(dashboard: DashboardWindowController) {
        self.dashboard = dashboard
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        configureButton()
        buildMenu()
        updatePresence()
        startPresencePolling()
    }

    deinit {
        presenceTimer?.invalidate()
    }

    // MARK: - Status button

    private func configureButton() {
        guard let button = statusItem.button else { return }
        button.image = MenuBarIcon.image(working: false)
        button.image?.isTemplate = true // adopts the menu-bar's light/dark tint
        button.toolTip = "PracticeGraph — analysis stays on this machine"
        // We want a real menu on click, but also to distinguish a plain left
        // click (open dashboard) from a menu request. Simplest robust behavior
        // that matches the tray: left-click opens the dashboard; the menu is
        // available via right-click / control-click. AppKit gives us this by
        // NOT assigning statusItem.menu and handling the click ourselves, then
        // popping the menu manually for secondary clicks.
        button.target = self
        button.action = #selector(statusButtonClicked)
        button.sendAction(on: [.leftMouseUp, .rightMouseUp])
    }

    @objc private func statusButtonClicked() {
        guard let event = NSApp.currentEvent else {
            openDashboard()
            return
        }
        let secondary = event.type == .rightMouseUp
            || event.modifierFlags.contains(.control)
        if secondary {
            popMenu()
        } else {
            // Left-click = the daily driver: the dashboard in its own window.
            openDashboard()
        }
    }

    // MARK: - Menu (mirror tray.rs show_menu — the trimmed 3-item version)

    private let menu = NSMenu()

    private func buildMenu() {
        menu.autoenablesItems = false

        // Disabled informational header — the presence line.
        statusHeaderItem.isEnabled = false
        menu.addItem(statusHeaderItem)
        menu.addItem(.separator())

        let open = NSMenuItem(
            title: "Open PracticeGraph", action: #selector(openDashboard), keyEquivalent: "")
        open.target = self
        menu.addItem(open)

        // A divider sets the dashboard (the daily driver) off from the rest.
        menu.addItem(.separator())

        launchAtLoginItem.target = self
        menu.addItem(launchAtLoginItem)

        let about = NSMenuItem(
            title: "About PracticeGraph", action: #selector(showAbout), keyEquivalent: "")
        about.target = self
        menu.addItem(about)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "Quit PracticeGraph", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
    }

    /// Pop the menu under the status item on a secondary click. Refresh the
    /// dynamic lines first (presence header, login-item check state).
    ///
    /// We deliberately do NOT assign `statusItem.menu` permanently — that would
    /// make every click (including left) open the menu and rob us of the
    /// left-click-opens-dashboard behavior. Instead we pop the menu manually,
    /// positioned just under the button, only on a secondary click.
    private func popMenu() {
        statusHeaderItem.title = working ? "Status: working now" : "Status: quiet"
        launchAtLoginItem.state = LoginItem.isEnabled ? .on : .off

        guard let button = statusItem.button else { return }
        let origin = NSPoint(x: 0, y: button.bounds.height + 4)
        menu.popUp(positioning: nil, at: origin, in: button)
    }

    // MARK: - Actions

    @objc private func openDashboard() {
        dashboard.show()
    }

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.messageText = "PracticeGraph \(AppInfo.version)"
        alert.informativeText = """
            All analysis runs locally on this machine.
            Sharing anonymous org aggregates is opt-in \
            (see the Privacy view in the app).
            """
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func toggleLaunchAtLogin() {
        LoginItem.setEnabled(!LoginItem.isEnabled)
        launchAtLoginItem.state = LoginItem.isEnabled ? .on : .off
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    // MARK: - Presence polling (mirror tray.rs update_presence)

    private func startPresencePolling() {
        let timer = Timer.scheduledTimer(
            withTimeInterval: Self.presencePollInterval, repeats: true
        ) { [weak self] _ in
            self?.updatePresence()
        }
        // Keep firing while menus/modal tracking are up.
        RunLoop.main.add(timer, forMode: .common)
        presenceTimer = timer
    }

    private func updatePresence() {
        // The mtime walk can touch disk; keep it off the main thread.
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let active = EngineRunner.presenceActive()
            DispatchQueue.main.async {
                guard let self else { return }
                self.working = active
                self.statusItem.button?.image = MenuBarIcon.image(working: active)
                self.statusItem.button?.image?.isTemplate = !active // green dot is intentional color
                self.statusItem.button?.toolTip = active
                    ? "PracticeGraph — working now (local only)"
                    : "PracticeGraph — quiet (local only)"
                self.statusHeaderItem.title = active ? "Status: working now" : "Status: quiet"
            }
        }
    }
}

// MARK: - Login item (mirror the Windows HKMU Run tray autostart)

/// Launch-at-login via SMAppService.mainApp (macOS 13+). Registering the main
/// app as a login item is the modern replacement for a LaunchAgent plist and
/// mirrors the Windows installer's HKMU Run key.
enum LoginItem {
    static var isEnabled: Bool {
        SMAppService.mainApp.status == .enabled
    }

    static func setEnabled(_ enabled: Bool) {
        do {
            if enabled {
                if SMAppService.mainApp.status != .enabled {
                    try SMAppService.mainApp.register()
                }
            } else {
                if SMAppService.mainApp.status == .enabled {
                    try SMAppService.mainApp.unregister()
                }
            }
        } catch {
            // Non-fatal: surface nothing intrusive. The menu check state will
            // simply reflect the actual (unchanged) status on next open.
            NSLog("PracticeGraph: login-item toggle failed: \(error.localizedDescription)")
        }
    }
}
