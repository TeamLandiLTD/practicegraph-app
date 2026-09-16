//
//  ProtocolRouter.swift
//  The macOS mirror of shell/src/main.rs `handle_url`.
//
//  Closed protocol verbs (C-5). Anything unrecognized — including the bare
//  `practicegraph:` and legacy `practicegraph:open-report` links — opens the
//  newest report; the scheme never carries free-form data.
//
//  On macOS the dashboard is the primary surface and records check-ins,
//  dismissals, and timer blocks in-page through the engine's /api/* endpoints,
//  so these protocol verbs are mostly back-compat. They are implemented in
//  full anyway, so a practicegraph: link from anywhere (a rendered report, a
//  notification) does exactly what it does on Windows.
//

import AppKit

enum ProtocolRouter {

    /// Dispatch a practicegraph: URL. `dashboard` is passed through so verbs
    /// that should surface the window (none today, but the seam is here) can.
    static func handle(_ url: URL, dashboard: DashboardWindowController) {
        let verb = normalize(url)

        // Prefixed, id-carrying verbs: dismiss-suggestion-<id> / dismiss-tip-<id>.
        for (prefix, cli) in [
            ("dismiss-suggestion-", ["suggest", "dismiss"]),
            ("dismiss-tip-", ["focus", "dismiss"]),
        ] {
            if verb.hasPrefix(prefix) {
                let id = String(verb.dropFirst(prefix.count))
                if isSafeId(id) {
                    _ = EngineRunner.dismiss(cli, id: id)
                }
                return
            }
        }

        // checkin-1..5
        if verb.hasPrefix("checkin-") {
            let rating = String(verb.dropFirst("checkin-".count))
            if ["1", "2", "3", "4", "5"].contains(rating) {
                _ = EngineRunner.recordCheckin(rating)
            }
            return
        }

        switch verb {
        case "timer-focus":
            EngineRunner.recordFocusEvent("block-started")
        case "timer-break":
            EngineRunner.recordFocusEvent("break-started")
        case "break":
            // The break-nudge door: open the app straight into the guided
            // break (the page consumes #break once). Same closed verb the
            // Windows toast carries.
            dashboard.show(startBreak: true)
        case "open-privacy":
            EngineRunner.openLatestReport(fragment: "privacy")
        case "open":
            dashboard.show()
        default:
            // Unknown / bare scheme: the newest report is the safe landing.
            EngineRunner.openLatestReport(fragment: nil)
        }
    }

    /// Strip the scheme and surrounding slashes/whitespace, matching the Rust
    /// `strip_prefix("practicegraph:").trim_matches('/')`. URL parsing may put
    /// the verb in the host (`practicegraph://open`) or the path
    /// (`practicegraph:open`), so normalize from the raw string.
    private static func normalize(_ url: URL) -> String {
        var raw = url.absoluteString
        if let range = raw.range(of: "practicegraph:") {
            raw = String(raw[range.upperBound...])
        }
        return raw.trimmingCharacters(in: CharacterSet(charactersIn: "/ \t\n"))
    }

    /// Same charset rule as the Rust shell: non-empty, lowercase ascii /
    /// digits / hyphen only. The CLI independently rejects unknown ids, but we
    /// refuse to hand it anything outside the closed charset.
    private static func isSafeId(_ id: String) -> Bool {
        guard !id.isEmpty else { return false }
        return id.allSatisfy { $0.isASCII && ($0.isLowercase || $0.isNumber || $0 == "-") }
    }
}
