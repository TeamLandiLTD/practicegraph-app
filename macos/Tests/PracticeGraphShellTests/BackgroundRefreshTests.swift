import Foundation
import XCTest
@testable import PracticeGraphShell

final class BackgroundRefreshTests: XCTestCase {
    let now = ISO8601DateFormatter().date(from: "2026-09-23T14:30:00Z")!

    func status(_ lastTickAt: String) -> Data {
        Data(#"{"schema":"practicegraph.status/1","last_tick_at":"\#(lastTickAt)"}"#.utf8)
    }

    func testDueWhenNoTickHasEverRun() {
        XCTAssertTrue(BackgroundRefresh.tickDue(status: nil, now: now))
    }

    func testNotDueRightAfterAnotherTick() {
        // The LaunchAgent (or an earlier app tick) ran ten minutes ago.
        XCTAssertFalse(BackgroundRefresh.tickDue(status: status("2026-09-23T14:20:18+00:00"), now: now))
    }

    func testDueOnceTheEngineIntervalHasPassed() {
        XCTAssertTrue(BackgroundRefresh.tickDue(status: status("2026-09-23T14:15:00+00:00"), now: now))
        XCTAssertTrue(BackgroundRefresh.tickDue(status: status("2026-09-22T09:00:00+00:00"), now: now))
    }

    func testAnUnreadableRecordNeverSuppressesRefresh() {
        for raw in ["", "not json", "[]", #"{"last_tick_at":42}"#, #"{"last_tick_at":"yesterday"}"#] {
            XCTAssertTrue(BackgroundRefresh.tickDue(status: Data(raw.utf8), now: now), raw)
        }
    }

    func testAFutureStampFromClockSkewDoesNotBlockRefresh() {
        XCTAssertTrue(BackgroundRefresh.tickDue(status: status("2026-09-24T14:30:00+00:00"), now: now))
    }

    func testIntervalMatchesTheEngineDefault() {
        // agent.py DEFAULT_INTERVAL_S and the LaunchAgent's StartInterval.
        XCTAssertEqual(BackgroundRefresh.interval, 900)
    }
}
