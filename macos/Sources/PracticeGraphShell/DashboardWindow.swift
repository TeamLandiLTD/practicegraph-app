//
//  DashboardWindow.swift
//  The macOS mirror of shell/src/app.rs.
//
//  The local UI served by the core engine, hosted in a WKWebView inside a
//  plain AppKit window — the dashboard never opens in a browser tab. The
//  shell stays read-only: the engine (`ui serve`) is launched through the
//  core CLI and owns all state (C-4); this process only ensures it is up and
//  points a webview at it.
//
//  Unlike Windows (WebView2 can be absent on a managed image), WKWebView is
//  part of macOS — there is no runtime to probe for or fall back from. The
//  only failure path is "the engine never came up", which degrades to the
//  newest rendered report, exactly like the Rust shell.
//

import AppKit
import WebKit

/// Owns the single dashboard window and its webview. One instance per app
/// (see AppDelegate) — a second "open" request surfaces the existing window
/// rather than making another.
final class DashboardWindowController: NSObject, NSWindowDelegate, WKNavigationDelegate,
                                       WKDownloadDelegate, NSMenuItemValidation {

    static let defaultSize = NSSize(width: 1280, height: 900)
    static let minSize = NSSize(width: 900, height: 600)

    /// The design system's paper ground (#fcfbf8). Everything that can show
    /// through before or around the page uses this one value, so there is no
    /// white flash and no seam at the edges of an overscroll.
    static let paperGround = NSColor(
        calibratedRed: 0.988, green: 0.984, blue: 0.973, alpha: 1.0)

    private var window: NSWindow?
    private var webView: WKWebView?

    /// Bring the dashboard to the front, creating it (and ensuring the engine)
    /// on first use. Safe to call repeatedly; the window is reused.
    ///
    /// `startBreak` is the notification door (the macOS mirror of the Windows
    /// `app --start break`): the INITIAL navigation carries `#break`, which
    /// the page consumes exactly once to start the guided break. It applies
    /// only when this call creates the window — with a window already open
    /// the intent is dropped, matching the Windows single-instance rule
    /// (documented there; an IPC relay is future work on both shells).
    ///
    /// The engine handshake blocks up to ~15s, so it runs on a background
    /// queue and the window is built back on the main thread.
    func show(startBreak: Bool = false) {
        if let window {
            surface(window)
            return
        }

        // Placeholder window up front so a click feels instant while the
        // engine warms; its content is swapped once we have an endpoint.
        let window = makeWindow()
        self.window = window
        surface(window)

        let fragment = startBreak ? "#break" : nil
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let endpoint = EngineRunner.ensureUiServer()
            DispatchQueue.main.async {
                guard let self, let window = self.window else { return }
                if let endpoint {
                    self.attachWebView(
                        to: window, endpoint: endpoint, fragment: fragment)
                } else {
                    // The engine never came up. The newest rendered report is
                    // the one artifact that is always openable.
                    self.presentEngineUnavailable(in: window)
                }
            }
        }
    }

    /// Is the dashboard window currently on screen? Used by the menu-bar
    /// left-click to toggle sensibly.
    var isVisible: Bool { window?.isVisible ?? false }

    // MARK: - Window construction

    private func makeWindow() -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(origin: .zero, size: Self.defaultSize),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "PracticeGraph"
        window.minSize = Self.minSize
        window.center()
        window.isReleasedWhenClosed = false // we keep the controller's reference
        window.delegate = self
        window.setFrameAutosaveName("PracticeGraphDashboard")

        // Warm paper ground while the webview loads, so there is no white
        // flash before first paint.
        window.backgroundColor = Self.paperGround

        // A stable, layer-backed container is the content view for the window's
        // whole life; the loading view and later the webview are SUBVIEWS of
        // it. Swapping `window.contentView` out from under a window that is
        // already on screen is what broke rendering before: the replacement
        // webview kept a correct DOM but its compositing updates never reached
        // the screen, so the dashboard froze on an early, mostly-empty frame
        // until something forced a full invalidation (resizing the window).
        // Adding a subview to a container that is already in the hierarchy is
        // the ordinary path, and it repaints normally.
        let container = NSView(frame: NSRect(origin: .zero, size: Self.defaultSize))
        container.wantsLayer = true
        container.layer?.backgroundColor = Self.paperGround.cgColor
        container.autoresizesSubviews = true
        window.contentView = container

        let loading = LoadingView(frame: container.bounds)
        loading.autoresizingMask = [.width, .height]
        container.addSubview(loading)
        return window
    }

    private func makeWebView(frame: NSRect) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // Loopback-only, self-contained app. The engine already sends a strict
        // CSP; we additionally keep the webview from wandering off-host in
        // decidePolicyFor below.
        configuration.websiteDataStore = .default()

        let webView = WKWebView(frame: frame, configuration: configuration)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        // The page paints its own ground; this just tells WebKit what sits
        // under it so overscroll and the pre-paint frame match instead of
        // flashing white.
        //
        // This used to be `setValue(false, forKey: "drawsBackground")` — the
        // pre-macOS-12 recipe, and undeclared API. `underPageBackgroundColor`
        // is the supported spelling and has been available since macOS 12,
        // below our deployment target.
        webView.underPageBackgroundColor = Self.paperGround
        // A stable, honest UA — the app is not pretending to be Safari-at-large.
        webView.customUserAgent = "PracticeGraph-macOS/\(AppInfo.version)"
        return webView
    }

    private func attachWebView(
        to window: NSWindow, endpoint: UiEndpoint, fragment: String? = nil
    ) {
        // The fragment rides only this INITIAL navigation (the fragment goes
        // after the token query); user reloads go through the page's own
        // cleared hash, so the intent can never replay.
        guard let url = URL(string: endpoint.urlString + (fragment ?? "")) else {
            presentEngineUnavailable(in: window)
            return
        }
        guard let container = window.contentView else { return }
        let webView = makeWebView(frame: container.bounds)
        self.webView = webView
        self.lastLoadedURL = url
        // Behind the loading view, so the page is never seen half-painted; the
        // loading view is removed on first paint (didFinish).
        container.addSubview(webView, positioned: .below, relativeTo: nil)
        webView.load(URLRequest(url: url))
    }

    /// Drop the "Reading this machine…" placeholder once the page has painted.
    private func dismissLoadingView() {
        guard let container = window?.contentView else { return }
        for subview in container.subviews where subview is LoadingView {
            subview.removeFromSuperview()
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // A clean load resets the cold-start retry budget.
        loadRetries = 0
        dismissLoadingView()
    }

    // MARK: - Navigation policy

    /// Keep the app inside its own loopback origin. Any http(s) link the page
    /// tries to open in-place (an external article, a docs URL) goes to the
    /// user's real browser instead of hijacking the dashboard webview.
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url, let endpoint = lastLoadedURL else {
            decisionHandler(.cancel)
            return
        }
        if DesktopSecurity.localDownload(url, endpoint: endpoint)
            && (navigationAction.shouldPerformDownload || url.scheme == "blob") {
            decisionHandler(.download)
            return
        }
        if DesktopSecurity.sameOrigin(url, endpoint: endpoint) {
            decisionHandler(.allow)
            return
        }
        if navigationAction.navigationType == .linkActivated,
           url.user == nil, url.password == nil,
           url.scheme == "https" || url.scheme == "mailto" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                 didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = navigationResponse.response.url, let endpoint = lastLoadedURL,
              DesktopSecurity.localDownload(url, endpoint: endpoint) else {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse,
                 didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        guard let window, let url = response.url, let endpoint = lastLoadedURL,
              DesktopSecurity.localDownload(url, endpoint: endpoint) else {
            completionHandler(nil)
            return
        }
        let panel = NSSavePanel()
        panel.title = "Save PracticeGraph export"
        panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
    }

    func webView(
        _ webView: WKWebView,
        didFail navigation: WKNavigation!,
        withError error: Error
    ) {
        // A transient load failure right after `ui serve` starts is expected
        // during the engine's cold start; retry once after a short beat before
        // giving up. (Mirrors the Windows shell riding out the cold start
        // rather than flashing an error — commit 607faf9.)
        retryOrFail()
    }

    func webView(
        _ webView: WKWebView,
        didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        retryOrFail()
    }

    private var loadRetries = 0
    private var lastLoadedURL: URL?
    private func retryOrFail() {
        guard let window else { return }
        // The webview keeps its URL across a failed provisional load; fall back
        // to the URL we last asked it to load if it somehow lost it.
        guard let webView, let url = webView.url ?? lastLoadedURL else {
            presentEngineUnavailable(in: window)
            return
        }
        if loadRetries < 3 {
            loadRetries += 1
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                webView.load(URLRequest(url: url))
            }
        } else {
            presentEngineUnavailable(in: window)
        }
    }

    // MARK: - Fallbacks

    private func presentEngineUnavailable(in window: NSWindow) {
        // Open the newest rendered report so the user still gets their data,
        // then close the empty shell window.
        let opened = EngineRunner.openLatestReport(fragment: nil)
        if !opened {
            let alert = NSAlert()
            alert.messageText = "PracticeGraph could not start its dashboard"
            alert.informativeText = """
                The local analysis engine did not come up, and there is no \
                rendered report to fall back to yet. Try opening PracticeGraph \
                again in a moment, or run it once from the command line to \
                initialize your data.
                """
            alert.alertStyle = .warning
            alert.addButton(withTitle: "OK")
            alert.runModal()
        }
        window.close()
    }

    // MARK: - Helpers

    private func surface(_ window: NSWindow) {
        // Become a regular app while a window is up: this puts a temporary Dock
        // tile there and lets the window take focus / appear in Cmd-Tab. We
        // drop back to .accessory when it closes, so the resting state is a
        // pure menu-bar app (LSUIElement). Without this an .accessory app
        // cannot reliably foreground its own window.
        if NSApp.activationPolicy() != .regular {
            NSApp.setActivationPolicy(.regular)
        }
        NSApp.activate(ignoringOtherApps: true)
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
    }

    // MARK: - View menu

    /// Zoom steps, matching what a browser's Cmd-+ does. Clamped so the page
    /// cannot be zoomed into uselessness in either direction.
    private static let zoomRange: ClosedRange<CGFloat> = 0.5...2.5
    private static let zoomStep: CGFloat = 0.1

    @objc func reloadDashboard(_ sender: Any?) {
        // reload() on a page that failed its provisional load has nothing to
        // reload, so fall back to the URL we last asked for.
        guard let webView else { return }
        if webView.url != nil {
            webView.reload()
        } else if let url = lastLoadedURL {
            webView.load(URLRequest(url: url))
        }
    }

    @objc func resetDashboardZoom(_ sender: Any?) { webView?.pageZoom = 1.0 }
    @objc func zoomDashboardIn(_ sender: Any?) { nudgeZoom(by: Self.zoomStep) }
    @objc func zoomDashboardOut(_ sender: Any?) { nudgeZoom(by: -Self.zoomStep) }

    private func nudgeZoom(by delta: CGFloat) {
        guard let webView else { return }
        webView.pageZoom = min(
            Self.zoomRange.upperBound,
            max(Self.zoomRange.lowerBound, webView.pageZoom + delta))
    }

    /// Grey the View menu out when there is no webview to act on, rather than
    /// offering items that silently do nothing.
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(reloadDashboard(_:)), #selector(resetDashboardZoom(_:)),
             #selector(zoomDashboardIn(_:)), #selector(zoomDashboardOut(_:)):
            return webView != nil
        default:
            return true
        }
    }

    // NSWindowDelegate: the window closing does not quit the app — the
    // menu-bar item keeps it alive, and left-click reopens (matching the
    // Windows tray keeping the process running after the window closes).
    func windowWillClose(_ notification: Notification) {
        // Drop the webview so a reopen builds a fresh one against a
        // possibly-new endpoint; keep the controller.
        webView = nil
        window = nil
        loadRetries = 0
        lastLoadedURL = nil
        // Return to the resting menu-bar-only state (no Dock tile) now that no
        // window is showing.
        NSApp.setActivationPolicy(.accessory)
    }
}

/// Minimal "warming up" content shown for the sub-second before the webview
/// attaches. Deliberately plain — the real UI paints over it immediately.
private final class LoadingView: NSView {
    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        DashboardWindowController.paperGround.setFill()
        bounds.fill()

        let text = "Reading this machine…"
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 13, weight: .regular),
            .foregroundColor: NSColor(calibratedRed: 0.35, green: 0.40, blue: 0.38, alpha: 1.0),
            .paragraphStyle: paragraph,
        ]
        let size = text.size(withAttributes: attributes)
        let rect = NSRect(
            x: 0,
            y: (bounds.height - size.height) / 2,
            width: bounds.width,
            height: size.height
        )
        text.draw(in: rect, withAttributes: attributes)
    }
}

// MARK: - URL helpers shared with EngineRunner

extension URL {
    /// True for our own dashboard origin (loopback on any ephemeral port).
    var isLoopback: Bool {
        guard let host, scheme == "http" else { return false }
        return host == "127.0.0.1" || host == "localhost"
    }
}

/// AppKit bridge used by EngineRunner (which stays Foundation-only). Opening
/// the rendered-report fallback lives here so the runner never imports AppKit.
@discardableResult
func NSWorkspaceOpen(_ url: URL) -> Bool {
    NSWorkspace.shared.open(url)
}
