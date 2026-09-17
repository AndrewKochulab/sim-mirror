// SPDX-License-Identifier: Apache-2.0
import Darwin
import Foundation
import HelperCore

/// A unix socket the helper listens on, readable and writable by this user only.
public final class UnixSocketListener: @unchecked Sendable {
    public let path: String
    private let descriptor: Int32

    public init(path: String) throws {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let capacity = MemoryLayout.size(ofValue: address.sun_path)
        guard path.utf8.count < capacity else {
            throw HelperFailure("the socket path \(path) is longer than a socket's \(capacity - 1) bytes", status: 400)
        }
        unlink(path)
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw HelperFailure("no socket: \(String(cString: strerror(errno)))") }
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            path.utf8CString.withUnsafeBytes { raw.copyMemory(from: $0) }
        }
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard bound == 0, chmod(path, 0o600) == 0, listen(fd, 8) == 0 else {
            let reason = String(cString: strerror(errno))
            Darwin.close(fd)
            throw HelperFailure("cannot listen on \(path): \(reason)")
        }
        self.path = path
        descriptor = fd
    }

    /// Accept connections on a thread of their own, handing each to `serve`, until the listener is closed.
    public func accept(_ serve: @escaping @Sendable (ByteChannel) -> Void) {
        let thread = Thread { [descriptor] in
            while true {
                let client = Darwin.accept(descriptor, nil, nil)
                guard client >= 0 else {
                    if errno == EINTR { continue }
                    return
                }
                var noSignal: Int32 = 1
                setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
                serve(SocketChannel(descriptor: client))
            }
        }
        thread.name = "sim-mirror.accept"
        thread.start()
    }

    public func close() {
        Darwin.close(descriptor)
        unlink(path)
    }
}

/// One accepted connection. Reads and writes block, so each runs on a queue of its own rather than on Swift's threads.
final class SocketChannel: ByteChannel, @unchecked Sendable {
    static let readSize = 1 << 16

    private let descriptor: Int32
    private let reads = DispatchQueue(label: "sim-mirror.socket.read")
    private let writes = DispatchQueue(label: "sim-mirror.socket.write")
    private let lock = NSLock()
    private var closed = false

    init(descriptor: Int32) {
        self.descriptor = descriptor
    }

    func read() async throws -> Data? {
        try await withCheckedThrowingContinuation { continuation in
            reads.async { [descriptor] in
                var buffer = [UInt8](repeating: 0, count: Self.readSize)
                while true {
                    let count = Darwin.read(descriptor, &buffer, buffer.count)
                    if count > 0 { return continuation.resume(returning: Data(buffer[0..<count])) }
                    if count == 0 { return continuation.resume(returning: nil) }
                    if errno == EINTR { continue }
                    return continuation.resume(throwing: HelperFailure("reading the socket: \(String(cString: strerror(errno)))"))
                }
            }
        }
    }

    func write(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            writes.async { [descriptor] in
                let failure: HelperFailure? = data.withUnsafeBytes { raw in
                    var offset = 0
                    while offset < raw.count {
                        let written = Darwin.write(descriptor, raw.baseAddress! + offset, raw.count - offset)
                        if written > 0 {
                            offset += written
                        } else if written < 0 && errno == EINTR {
                            continue
                        } else {
                            return HelperFailure("writing the socket: \(String(cString: strerror(errno)))")
                        }
                    }
                    return nil
                }
                if let failure { continuation.resume(throwing: failure) } else { continuation.resume() }
            }
        }
    }

    func close() {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        closed = true
        shutdown(descriptor, SHUT_RDWR)
        Darwin.close(descriptor)
    }
}
