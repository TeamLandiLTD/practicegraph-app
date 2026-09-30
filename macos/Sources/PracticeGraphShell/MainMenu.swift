//
//  MainMenu.swift
//  The menu bar an app is required to have once it shows a window.
//
//  This was missing entirely, and the absence is easy to miss because the
//  window still opens and still renders. What breaks is quieter: macOS routes
//  key equivalents through the main menu, so with `NSApp.mainMenu` nil there is
//  no Cmd-C, Cmd-V, Cmd-A, Cmd-W or Cmd-Q anywhere in the app — including
//  inside the dashboard's text fields. And because an app with no menu bar
//  leaves the previously active app's menus on screen, activating PracticeGraph
//  appeared to leave the frontmost app unchanged.
//
//  Nothing here is app-specific behaviour: every action is a first-responder
//  selector that AppKit and WKWebView already implement. The menu exists to
//  give those the key equivalents users expect, not to add features.
//

import AppKit

enum MainMenu {

    /// Build and install the menu bar. Called once, before the first window.
    static func install(appName: String = "PracticeGraph") {
        let main = NSMenu()
        main.addItem(appMenu(appName))
        main.addItem(editMenu())
        main.addItem(viewMenu())
        main.addItem(windowMenu())
        NSApp.mainMenu = main
    }

    // MARK: - Sections

    private static func appMenu(_ appName: String) -> NSMenuItem {
        let item = NSMenuItem()
        let menu = NSMenu()
        menu.addItem(
            withTitle: "About \(appName)",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
            keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Hide \(appName)",
            action: #selector(NSApplication.hide(_:)),
            keyEquivalent: "h")
        let hideOthers = menu.addItem(
            withTitle: "Hide Others",
            action: #selector(NSApplication.hideOtherApplications(_:)),
            keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        menu.addItem(
            withTitle: "Show All",
            action: #selector(NSApplication.unhideAllApplications(_:)),
            keyEquivalent: "")
        menu.addItem(.separator())
        // Quit really does quit — closing the window only hides it (the
        // menu-bar item keeps the app alive), so this is the one way out.
        menu.addItem(
            withTitle: "Quit \(appName)",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q")
        item.submenu = menu
        return item
    }

    /// Standard editing, so selecting and copying a reading out of the
    /// dashboard works. WKWebView implements all of these as first responder.
    private static func editMenu() -> NSMenuItem {
        let item = NSMenuItem()
        let menu = NSMenu(title: "Edit")
        menu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = menu.addItem(
            withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        menu.addItem(
            withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        menu.addItem(
            withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(
            withTitle: "Select All",
            action: #selector(NSText.selectAll(_:)),
            keyEquivalent: "a")
        item.submenu = menu
        return item
    }

    private static func viewMenu() -> NSMenuItem {
        let item = NSMenuItem()
        let menu = NSMenu(title: "View")
        // Reload is genuinely useful here: the dashboard is a live local page
        // and a manual refresh is the obvious recovery if it ever looks stale.
        menu.addItem(
            withTitle: "Reload",
            action: #selector(WKWebViewReloading.reloadDashboard(_:)),
            keyEquivalent: "r")
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Actual Size",
            action: #selector(WKWebViewReloading.resetDashboardZoom(_:)),
            keyEquivalent: "0")
        menu.addItem(
            withTitle: "Zoom In",
            action: #selector(WKWebViewReloading.zoomDashboardIn(_:)),
            keyEquivalent: "+")
        menu.addItem(
            withTitle: "Zoom Out",
            action: #selector(WKWebViewReloading.zoomDashboardOut(_:)),
            keyEquivalent: "-")
        item.submenu = menu
        return item
    }

    private static func windowMenu() -> NSMenuItem {
        let item = NSMenuItem()
        let menu = NSMenu(title: "Window")
        menu.addItem(
            withTitle: "Minimize",
            action: #selector(NSWindow.performMiniaturize(_:)),
            keyEquivalent: "m")
        menu.addItem(
            withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Close",
            action: #selector(NSWindow.performClose(_:)),
            keyEquivalent: "w")
        item.submenu = menu
        NSApp.windowsMenu = menu
        return item
    }
}

/// The selectors the View menu targets. They travel the responder chain to the
/// dashboard controller, which owns the webview — the menu itself holds no
/// reference to it, so it stays correct across a window being closed and
/// reopened against a fresh webview.
@objc protocol WKWebViewReloading {
    func reloadDashboard(_ sender: Any?)
    func resetDashboardZoom(_ sender: Any?)
    func zoomDashboardIn(_ sender: Any?)
    func zoomDashboardOut(_ sender: Any?)
}
