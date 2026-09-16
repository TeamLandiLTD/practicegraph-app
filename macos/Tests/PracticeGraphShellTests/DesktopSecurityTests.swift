import Foundation
import XCTest
@testable import PracticeGraphShell

final class DesktopSecurityTests: XCTestCase {
    let endpoint = URL(string: "http://127.0.0.1:45001/?token=test")!

    func testOriginDoesNotIncludeOtherLocalServices() {
        XCTAssertTrue(DesktopSecurity.sameOrigin(URL(string: "http://127.0.0.1:45001/#practice")!, endpoint: endpoint))
        for value in ["http://127.0.0.1:45002/", "http://localhost:45001/", "https://example.com/",
                      "http://user@127.0.0.1:45001/", "file:///etc/passwd"] {
            XCTAssertFalse(DesktopSecurity.sameOrigin(URL(string: value)!, endpoint: endpoint))
        }
    }

    func testExportsOnlyFromTheDashboard() {
        XCTAssertTrue(DesktopSecurity.localDownload(URL(string: "blob:http://127.0.0.1:45001/id")!, endpoint: endpoint))
        XCTAssertFalse(DesktopSecurity.localDownload(URL(string: "blob:https://example.com/id")!, endpoint: endpoint))
    }

    func testTokenCannotChangeTheNavigationURL() {
        XCTAssertTrue(DesktopSecurity.validToken(String(repeating: "a", count: 43)))
        XCTAssertFalse(DesktopSecurity.validToken("short"))
        XCTAssertFalse(DesktopSecurity.validToken(String(repeating: "a", count: 40) + "&next=x"))
    }
}
