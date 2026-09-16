import Foundation

/// The native shell admits only its exact authenticated loopback origin.
enum DesktopSecurity {
    static func validToken(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil
    }

    static func sameOrigin(_ url: URL, endpoint: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == endpoint.port
            && url.user == nil && url.password == nil
    }

    static func localDownload(_ url: URL, endpoint: URL) -> Bool {
        if sameOrigin(url, endpoint: endpoint) { return true }
        guard url.scheme == "blob", let inner = URL(string: String(url.absoluteString.dropFirst(5)))
        else { return false }
        return sameOrigin(inner, endpoint: endpoint)
    }
}

/// A ping must never redirect the local credential to a different destination.
final class LocalPingDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
