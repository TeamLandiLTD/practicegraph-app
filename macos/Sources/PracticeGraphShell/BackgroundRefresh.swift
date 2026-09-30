//
//  BackgroundRefresh.swift
//  Keeps every edition fresh while the app is running.
//
//  The dashboard's own server (`ui serve`) refreshes only the editions its
//  pages poll — news, build ideas, community, playbooks, training and token
//  prices. Everything else (model guidance and benchmarks, advisor, docs,
//  skills, the served rate card, harness releases) is pulled by the agent
//  tick. On Windows that tick runs in the service; on macOS it was left to an
//  optional, hand-installed LaunchAgent, so a Mac installed from the disk
//  image never pulled those editions at all and the Models page stayed empty.
//
//  The app now runs the tick itself, through the core CLI like every other
//  write (C-4). It reads the engine's closed status file first and ticks only
//  when the last tick is older than the engine's interval, so with the
//  LaunchAgent installed nothing runs twice.
//

import Foundation

enum BackgroundRefresh {
    /// The engine's tick cadence: agent.py DEFAULT_INTERVAL_S and the
    /// LaunchAgent's StartInterval.
    static let interval: TimeInterval = 900

    /// Let the dashboard's first ingest have the machine before the tick.
    static let firstCheckDelay: TimeInterval = 60

    /// Whether a tick is due, from the engine's `status.json`. Anything that
    /// cannot prove a recent tick counts as due: a missing or unreadable
    /// record must never suppress refresh, and a stamp in the future (clock
    /// skew) must not block it until the clock catches up.
    static func tickDue(status: Data?, now: Date, interval: TimeInterval = interval) -> Bool {
        guard let status,
              let record = try? JSONSerialization.jsonObject(with: status) as? [String: Any],
              let stamp = record["last_tick_at"] as? String,
              let lastTick = ISO8601DateFormatter().date(from: stamp)
        else { return true }
        let age = now.timeIntervalSince(lastTick)
        return age >= interval || age < -60
    }
}

/// Runs the due-check on a timer; at most one tick in flight at a time.
final class BackgroundRefresher {
    private var timer: Timer?
    private var inFlight = false  // main thread only

    func start() {
        DispatchQueue.main.asyncAfter(deadline: .now() + BackgroundRefresh.firstCheckDelay) {
            [weak self] in self?.refreshIfDue()
        }
        let timer = Timer.scheduledTimer(
            withTimeInterval: BackgroundRefresh.interval, repeats: true
        ) { [weak self] _ in
            self?.refreshIfDue()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func refreshIfDue() {
        guard !inFlight else { return }
        inFlight = true
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let statusURL = EngineRunner.dataDir.appendingPathComponent("status.json")
            if BackgroundRefresh.tickDue(status: try? Data(contentsOf: statusURL), now: Date()) {
                EngineRunner.runCoreTick()
            }
            DispatchQueue.main.async { self?.inFlight = false }
        }
    }
}
