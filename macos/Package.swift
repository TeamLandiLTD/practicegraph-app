// swift-tools-version:5.9
//
// PracticeGraph macOS shell (spec §9 C-2, macOS edition of shell/ Rust).
//
// One small app, the same handful of jobs the Windows shell does — but
// nothing more. It never analyzes anything and never writes state: it
// launches the portable Python engine (C-4, C-6) and reads only the
// world-readable artifacts it produces (ui.json, rendered reports).
//
// A Swift Package (not an .xcodeproj) on purpose: it builds from the command
// line with `swift build`, the .app is assembled by packaging/macos, and the
// whole thing stays reviewable as plain source. Nothing here depends on
// Xcode's project format.
import PackageDescription

let package = Package(
    name: "PracticeGraph",
    platforms: [
        // WKWebView + SMAppService (login-item) + modern SwiftUI menu-bar app.
        // 13.0 is the floor for SMAppService.mainApp.
        .macOS(.v13)
    ],
    targets: [
        .executableTarget(
            name: "PracticeGraphShell",
            path: "Sources/PracticeGraphShell"
            // The app is self-contained; it links only system frameworks
            // (AppKit, WebKit, ServiceManagement). Zero third-party packages,
            // mirroring the engine's zero-runtime-dependency discipline.
            //
            // Note: we intentionally do NOT set -warnings-as-errors here. The
            // shell uses a couple of deliberate KVC calls (e.g. the webview's
            // background) that can emit SDK-version-dependent warnings; gating
            // the build on a zero-warning slate would make the build brittle
            // across Xcode versions. Treat warnings as review signal, not a
            // hard gate (the Windows shell likewise runs clippy as a separate
            // step, not baked into the compile).
        ),
        .testTarget(name: "PracticeGraphShellTests", dependencies: ["PracticeGraphShell"])
    ]
)
