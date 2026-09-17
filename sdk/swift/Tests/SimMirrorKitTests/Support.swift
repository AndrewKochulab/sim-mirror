// SPDX-License-Identifier: Apache-2.0
import Darwin
import Foundation
import Testing
import UIKit

@testable import SimMirrorKit

/// The repository this package is in: the app SDK protocol's schemas and examples are read from it.
enum Repository {
    static let root = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()

    static func data(_ relative: String) throws -> Data {
        try Data(contentsOf: root.appendingPathComponent(relative))
    }

    static func json(_ relative: String) throws -> Any {
        try JSONSerialization.jsonObject(with: data(relative))
    }
}

/// A JSON document as Foundation reads it, for comparing two documents whatever their key order or spacing.
func document(_ data: Data) throws -> NSObject {
    try #require(try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) as? NSObject)
}

/// A folder of its own under the test's temporary folder, removed when the test is done with it.
final class TemporaryFolder {
    let path: String

    init() throws {
        let base = NSTemporaryDirectory() + "SimMirrorKitTests-" + UUID().uuidString
        try FileManager.default.createDirectory(atPath: base, withIntermediateDirectories: true)
        path = (base as NSString).resolvingSymlinksInPath
    }

    deinit {
        try? FileManager.default.removeItem(atPath: path)
    }

    func mode(of relative: String) -> mode_t? {
        var status = stat()
        guard lstat("\(path)/\(relative)", &status) == 0 else { return nil }
        return status.st_mode & 0o777
    }
}

/// A plain blocking HTTP client over a socket, so a test says exactly which bytes it sends.
enum RawClient {
    struct Answer {
        let status: Int
        let head: String
        let body: Data
    }

    /// Sends bytes to 127.0.0.1:port and reads the answer until the server closes the connection. The blocking calls
    /// run on a thread of their own, never on the threads Swift's concurrency shares out between tests.
    static func send(_ bytes: Data, port: UInt16, readAnswer: Bool = true) async -> Answer? {
        await withCheckedContinuation { done in
            Thread.detachNewThread {
                done.resume(returning: exchange(bytes, port: port, readAnswer: readAnswer))
            }
        }
    }

    private static func exchange(_ bytes: Data, port: UInt16, readAnswer: Bool) -> Answer? {
        do {
            let descriptor = socket(AF_INET, SOCK_STREAM, 0)
            guard descriptor >= 0 else { return nil }
            defer { close(descriptor) }
            var yes: Int32 = 1
            setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))
            var limit = timeval(tv_sec: 30, tv_usec: 0)
            setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &limit, socklen_t(MemoryLayout<timeval>.size))
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = port.bigEndian
            address.sin_addr.s_addr = inet_addr("127.0.0.1")
            let connected = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            guard connected == 0 else { return nil }
            _ = bytes.withUnsafeBytes { Darwin.send(descriptor, $0.baseAddress, $0.count, 0) }
            guard readAnswer else { return Answer(status: 0, head: "", body: Data()) }
            var received = Data()
            var chunk = [UInt8](repeating: 0, count: 65536)
            while true {
                let count = recv(descriptor, &chunk, chunk.count, 0)
                guard count > 0 else { break }
                received.append(contentsOf: chunk[0..<count])
            }
            guard let split = received.range(of: Data("\r\n\r\n".utf8)),
                let head = String(data: received[..<split.lowerBound], encoding: .utf8),
                let status = Int(head.split(separator: " ").dropFirst().first ?? "")
            else { return nil }
            return Answer(status: status, head: head, body: received[split.upperBound...])
        }
    }

    static func request(
        port: UInt16, secret: String?, path: String = "/v1/hierarchy", method: String = "GET",
        extra: [String] = [], host: String? = nil
    ) async -> Answer? {
        var lines = ["\(method) \(path) HTTP/1.1", "Host: \(host ?? "127.0.0.1:\(port)")"]
        if let secret { lines.append("Authorization: Bearer \(secret)") }
        lines += extra
        return await send(Data((lines.joined(separator: "\r\n") + "\r\n\r\n").utf8), port: port)
    }
}

/// A node for tests, with only what a test cares about given.
func testNode(
    _ kind: SimMirror.Kind = .container, label: String? = nil,
    frame: CGRect = CGRect(x: 0, y: 0, width: 10, height: 10),
    children: [Node] = []
) -> Node {
    Node(
        kind: kind, label: label, labelSource: label == nil ? nil : .title, frame: Frame(frame), typeName: "UIView",
        children: children)
}

/// A hierarchy for tests.
func testHierarchy(nodes: [Node] = [], notes: [String] = []) -> Hierarchy {
    Hierarchy(
        protocolVersion: 1, sdkVersion: SimMirror.sdkVersion,
        app: AppInfo(bundleID: "io.github.andrewkochulab.simmirror.tests", name: "Tests", pid: 42, active: true),
        screen: ScreenInfo(widthPt: 402, heightPt: 874, scale: 3, orientation: .portrait),
        modal: nil, keyboard: nil, windows: [Window(level: 0, key: true, nodes: nodes)], truncated: false,
        nodeCount: nodes.reduce(0) { $0 + $1.count }, captureMs: 1, notes: notes
    )
}

/// A window of the screen's size with views laid out in it, shown so frames convert to the screen.
@MainActor
func testWindow(_ views: UIView...) -> UIWindow {
    let window = UIWindow(frame: UIScreen.main.bounds)
    for view in views { window.addSubview(view) }
    window.isHidden = false
    window.layoutIfNeeded()
    return window
}
