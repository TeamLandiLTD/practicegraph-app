//
//  EngineRunner.swift
//  The macOS mirror of shell/src/corerun.rs.
//
//  Launching the analysis core and reading its published artifacts. The
//  shell writes nothing itself: state changes go through the core CLI only
//  (C-4). Everything here is either "spawn the engine with these args" or
//  "read a world-readable file the engine wrote".
//

import Foundation

/// Which analysis core this install carries (`status`/diagnostics).
enum CoreKind {
    /// Compiled engine binary next to the app resources (shipping bundle).
    case engine
    /// A Python interpreter running the source package (dev/checkout).
    case python
    /// Neither found — the install is broken.
    case missing
}

/// The endpoint the core's `ui serve` publishes in ui.json.
struct UiEndpoint {
    let port: UInt16
    let token: String

    var urlString: String { "http://127.0.0.1:\(port)/?token=\(token)" }
}

enum EngineRunner {

    // MARK: - Locations

    /// Directory holding bundled resources (engine binary, webui/, icons).
    /// In a shipping .app this is `Contents/Resources`; when running the bare
    /// SwiftPM binary in development it is the executable's own directory.
    static var resourcesDir: URL {
        if let resourceURL = Bundle.main.resourceURL,
           FileManager.default.fileExists(atPath: resourceURL.path) {
            return resourceURL
        }
        return Bundle.main.bundleURL.deletingLastPathComponent()
    }

    /// Machine data directory. This must match the engine's own resolution
    /// exactly (config.py `default_data_dir`): env override wins, otherwise
    /// `$XDG_DATA_HOME/practicegraph` else `~/.local/share/practicegraph`.
    /// Note the lowercase name and the XDG root — NOT ~/Library/Application
    /// Support. If this drifts from the engine, ui.json is read from the wrong
    /// place and the window never finds the server.
    static var dataDir: URL {
        let env = ProcessInfo.processInfo.environment
        if let override = env["PRACTICEGRAPH_DATA_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        let base: URL
        if let xdg = env["XDG_DATA_HOME"], !xdg.isEmpty {
            base = URL(fileURLWithPath: xdg, isDirectory: true)
        } else {
            base = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".local", isDirectory: true)
                .appendingPathComponent("share", isDirectory: true)
        }
        return base.appendingPathComponent("practicegraph", isDirectory: true)
    }

    /// The compiled engine binary shipped beside the app resources. Dev
    /// checkouts have none and fall back to a Python interpreter.
    private static var engineBinary: URL? {
        let candidate = resourcesDir.appendingPathComponent("practicegraph-engine")
        return isExecutableFile(candidate) ? candidate : nil
    }

    /// The repository root when running from a source checkout, discovered by
    /// walking up from the executable until a `src/practicegraph` package is
    /// found. Only used by the Python dev fallback.
    private static var checkoutRoot: URL? {
        var dir = Bundle.main.bundleURL
        for _ in 0..<8 {
            let pkg = dir
                .appendingPathComponent("src", isDirectory: true)
                .appendingPathComponent("practicegraph", isDirectory: true)
            if FileManager.default.fileExists(atPath: pkg.path) {
                return dir
            }
            dir = dir.deletingLastPathComponent()
        }
        // Env escape hatch for unusual dev layouts.
        if let root = ProcessInfo.processInfo.environment["PRACTICEGRAPH_SRC"], !root.isEmpty {
            return URL(fileURLWithPath: root, isDirectory: true)
        }
        return nil
    }

    static var coreKind: CoreKind {
        if engineBinary != nil { return .engine }
        if checkoutRoot != nil, pythonInterpreter() != nil { return .python }
        return .missing
    }

    // MARK: - Command construction

    /// Build a Process invoking the core with the given args — the Swift
    /// analogue of corerun.rs `core_command`. Mirrors its environment:
    /// PRACTICEGRAPH_DATA_DIR pinned to `dataDir`. Unlike the Windows service
    /// shell, the per-user Mac app does NOT set PRACTICEGRAPH_SCAN_PROFILES:
    /// it runs as the logged-in user and reads that user's own logs.
    private static func coreCommand(_ args: [String]) -> Process? {
        let process = Process()
        var environment = ProcessInfo.processInfo.environment
        environment["PRACTICEGRAPH_DATA_DIR"] = dataDir.path

        if let engine = engineBinary {
            process.executableURL = engine
            process.arguments = args
        } else if let root = checkoutRoot, let python = pythonInterpreter() {
            process.executableURL = python
            process.arguments = ["-m", "practicegraph"] + args
            // Import the package straight from the checkout's src/ layout.
            let srcPath = root.appendingPathComponent("src", isDirectory: true).path
            let existing = environment["PYTHONPATH"]
            environment["PYTHONPATH"] = existing.map { "\(srcPath):\($0)" } ?? srcPath
            process.currentDirectoryURL = root
        } else {
            return nil
        }
        process.environment = environment
        return process
    }

    /// Locate a Python 3 interpreter for the dev fallback. Prefers a checkout
    /// virtualenv, then PATH `python3`. Shipping builds never reach this.
    private static func pythonInterpreter() -> URL? {
        if let root = checkoutRoot {
            for venvPython in [".venv/bin/python3", ".venv/bin/python"] {
                let candidate = root.appendingPathComponent(venvPython)
                if isExecutableFile(candidate) { return candidate }
            }
        }
        for path in ["/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"] {
            let candidate = URL(fileURLWithPath: path)
            if isExecutableFile(candidate) { return candidate }
        }
        return nil
    }

    // MARK: - Fire-and-forget / blocking invocations (C-4)

    /// Spawn the core detached, ignoring output. Used where we only care that
    /// the write happened (the webview reflects it on its next poll).
    @discardableResult
    private static func spawnDetached(_ args: [String]) -> Bool {
        guard let process = coreCommand(args) else { return false }
        detachStandardIO(process)
        do {
            try process.run()
            return true
        } catch {
            return false
        }
    }

    /// Run the core to completion and report success (exit status 0).
    @discardableResult
    private static func runToCompletion(_ args: [String]) -> Bool {
        guard let process = coreCommand(args) else { return false }
        detachStandardIO(process)
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    /// Record a daily self-rating through the core CLI (protocol back-compat;
    /// the dashboard normally POSTs /api/checkin in-page).
    @discardableResult
    static func recordCheckin(_ rating: String) -> Bool {
        runToCompletion(["checkin", rating])
    }

    /// Dismiss a suggestion or tip through the core CLI. `kindArgs` is the
    /// closed subcommand pair (["suggest","dismiss"] / ["focus","dismiss"]);
    /// `id` charset is validated by the caller, and the CLI rejects unknown ids.
    @discardableResult
    static func dismiss(_ kindArgs: [String], id: String) -> Bool {
        runToCompletion(kindArgs + [id])
    }

    /// Record a focus-timer lifecycle event (protocol back-compat; the
    /// dashboard normally POSTs /api/block in-page). Fire-and-forget.
    static func recordFocusEvent(_ event: String) {
        spawnDetached(["focus", "record", event])
    }

    /// Run one agent tick via the core CLI. Best-effort and blocking: call it
    /// off the main thread (BackgroundRefresher does).
    @discardableResult
    static func runCoreTick() -> Bool {
        runToCompletion(["agent", "run", "--once"])
    }

    // MARK: - UI server lifecycle (mirror corerun.rs ensure_ui_server)

    private static var uiStateFile: URL {
        dataDir.appendingPathComponent("ui.json")
    }

    /// Parse ui.json ({"port": N, "token": "..."}). Written by our own core,
    /// so Foundation JSON is safe and cheap here (the Rust side hand-parses to
    /// avoid a dependency; Swift has JSONSerialization in the stdlib).
    private static func readEndpointFile() -> UiEndpoint? {
        guard let data = try? Data(contentsOf: uiStateFile),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }

        // port may decode as Int (JSON number) — normalize to UInt16.
        let port: UInt16?
        switch object["port"] {
        case let value as Int where value > 0 && value <= Int(UInt16.max):
            port = UInt16(value)
        case let value as NSNumber where value.intValue > 0 && value.intValue <= Int(UInt16.max):
            port = UInt16(value.intValue)
        default:
            port = nil
        }
        guard let port, let token = object["token"] as? String,
              DesktopSecurity.validToken(token) else {
            return nil
        }
        return UiEndpoint(port: port, token: token)
    }

    /// Is something actually listening on the published port? A stale ui.json
    /// (server gone) must not be trusted — same socket probe the Rust shell does.
    private static func isListening(port: UInt16) -> Bool {
        let sock = socket(AF_INET, SOCK_STREAM, 0)
        guard sock >= 0 else { return false }
        defer { close(sock) }

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let result = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }

    private static func liveEndpoint() -> UiEndpoint? {
        guard let endpoint = readEndpointFile(), isListening(port: endpoint.port) else {
            return nil
        }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(endpoint.port)/api/ping")!)
        request.setValue(endpoint.token, forHTTPHeaderField: "X-PracticeGraph-Token")
        request.timeoutInterval = 1
        let session = URLSession(configuration: .ephemeral, delegate: LocalPingDelegate(), delegateQueue: nil)
        let completed = DispatchSemaphore(value: 0)
        var authenticated = false
        let task = session.dataTask(with: request) { data, response, _ in
            if let response = response as? HTTPURLResponse, response.statusCode == 200,
               let data, data.count <= 4096,
               let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               body["ok"] as? Bool == true {
                authenticated = true
            }
            completed.signal()
        }
        task.resume()
        let finished = completed.wait(timeout: .now() + 2) == .success
        session.invalidateAndCancel()
        return finished && authenticated ? endpoint : nil
    }

    /// Ensure the local UI server is running and return its endpoint — never
    /// opens a browser. A live published endpoint is reused; otherwise the
    /// engine's `ui serve` is spawned detached and ui.json + the socket are
    /// polled for up to ~15 s (mirror corerun.rs ensure_ui_server).
    ///
    /// Blocking: call this off the main thread (see WindowController).
    static func ensureUiServer() -> UiEndpoint? {
        if let endpoint = liveEndpoint() { return endpoint }
        _ = spawnDetached(["ui", "serve"])

        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline {
            Thread.sleep(forTimeInterval: 0.3)
            if let endpoint = liveEndpoint() { return endpoint }
        }
        return nil
    }

    // MARK: - Presence (mirror corerun.rs presence_active)

    /// "working" means an AI-tool log changed in the last 10 minutes. A cheap
    /// early-exit walk over the known log roots — no parsing, no content. Kept
    /// byte-for-byte equivalent to the Rust/Python presence logic so the icon
    /// means the same thing on every platform.
    private static let presenceFreshSeconds: TimeInterval = 10 * 60
    private static let presenceMaxDepth = 6

    static func presenceActive() -> Bool {
        let cutoff = Date().addingTimeInterval(-presenceFreshSeconds)
        let home = FileManager.default.homeDirectoryForCurrentUser
        var roots = [
            home.appendingPathComponent(".claude/projects"),
            home.appendingPathComponent(".codex/sessions"),
        ]
        if let codexHome = ProcessInfo.processInfo.environment["CODEX_HOME"], !codexHome.isEmpty {
            roots.append(URL(fileURLWithPath: codexHome).appendingPathComponent("sessions"))
        }
        return roots.contains { anyFresh($0, cutoff: cutoff, depth: presenceMaxDepth) }
    }

    private static func anyFresh(_ dir: URL, cutoff: Date, depth: Int) -> Bool {
        guard depth > 0 else { return false }
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: dir,
            includingPropertiesForKeys: [.isDirectoryKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return false }

        for entry in entries {
            let values = try? entry.resourceValues(
                forKeys: [.isDirectoryKey, .contentModificationDateKey])
            if values?.isDirectory == true {
                if anyFresh(entry, cutoff: cutoff, depth: depth - 1) { return true }
            } else if let modified = values?.contentModificationDate, modified >= cutoff {
                return true
            }
        }
        return false
    }

    // MARK: - Fallback: open the newest rendered report

    /// Open the newest rendered daily report in the default browser; fall back
    /// to asking the core to render one (FR-RPT-6). This is the last-resort
    /// path when the dashboard window can't come up — the report is the one
    /// artifact that is always openable.
    @discardableResult
    static func openLatestReport(fragment: String?) -> Bool {
        let reportsDir = dataDir.appendingPathComponent("reports", isDirectory: true)
        let newest = (try? FileManager.default.contentsOfDirectory(
            at: reportsDir, includingPropertiesForKeys: nil))?
            .filter {
                let name = $0.lastPathComponent
                return name.hasPrefix("daily-") && name.hasSuffix(".html")
            }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .last

        if let newest {
            var target = newest
            if let fragment {
                // NSWorkspace.open drops URL fragments on file URLs, so append
                // it into a fresh URL string.
                if let withFragment = URL(string: newest.absoluteString + "#" + fragment) {
                    target = withFragment
                }
            }
            return NSWorkspaceOpen(target)
        }
        return runToCompletion(["report", "--open"])
    }

    // MARK: - Small helpers

    private static func isExecutableFile(_ url: URL) -> Bool {
        FileManager.default.isExecutableFile(atPath: url.path)
    }

    /// Detach a child's std streams. The engine is GUI-subsystem-silent on
    /// Windows; on macOS there is no console to flash, but we still route its
    /// output to /dev/null so nothing leaks into our own logs (tokens ride the
    /// ui.json path, never stdout, but belt-and-suspenders).
    private static func detachStandardIO(_ process: Process) {
        let devNull = FileHandle.nullDevice
        process.standardOutput = devNull
        process.standardError = devNull
        process.standardInput = devNull
    }
}
