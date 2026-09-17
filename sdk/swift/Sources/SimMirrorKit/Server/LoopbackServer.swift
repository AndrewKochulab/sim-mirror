// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Darwin
    import Foundation

    /// The system calls the server makes, one closure each, so every way they fail can be tested.
    struct Syscalls: Sendable {
        var socket: @Sendable () -> Int32
        /// Lets the port be reused and makes accepting non-blocking.
        var configureListener: @Sendable (Int32) -> Bool
        /// Binds to 127.0.0.1 on a port, 0 for any.
        var bind: @Sendable (Int32, UInt16) -> Int32
        var listen: @Sendable (Int32, Int32) -> Int32
        var boundPort: @Sendable (Int32) -> UInt16?
        var accept: @Sendable (Int32) -> Int32
        /// Makes a connection blocking, with a timeout for each read and write, and no SIGPIPE.
        var configureConnection: @Sendable (Int32, TimeInterval) -> Bool
        var receive: @Sendable (Int32, UnsafeMutableRawPointer, Int) -> Int
        var send: @Sendable (Int32, UnsafeRawPointer, Int) -> Int
        var close: @Sendable (Int32) -> Void
        var errno: @Sendable () -> Int32
    }

    extension Syscalls {
        static let live = Syscalls(
            socket: { Darwin.socket(AF_INET, SOCK_STREAM, 0) },
            configureListener: { descriptor in
                var yes: Int32 = 1
                let reused = setsockopt(descriptor, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
                return reused == 0 && fcntl(descriptor, F_SETFL, fcntl(descriptor, F_GETFL) | O_NONBLOCK) != -1
            },
            bind: { descriptor, port in
                var address = sockaddr_in()
                address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
                address.sin_family = sa_family_t(AF_INET)
                address.sin_port = port.bigEndian
                address.sin_addr.s_addr = inet_addr("127.0.0.1")
                return withUnsafePointer(to: &address) { pointer in
                    pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                        Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                    }
                }
            },
            listen: { Darwin.listen($0, $1) },
            boundPort: { descriptor in
                var address = sockaddr_in()
                var length = socklen_t(MemoryLayout<sockaddr_in>.size)
                let named = withUnsafeMutablePointer(to: &address) { pointer in
                    pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(descriptor, $0, &length) }
                }
                return named == 0 ? UInt16(bigEndian: address.sin_port) : nil
            },
            accept: { Darwin.accept($0, nil, nil) },
            configureConnection: { descriptor, timeout in
                var yes: Int32 = 1
                var limit = timeval(tv_sec: Int(timeout), tv_usec: 0)
                let size = socklen_t(MemoryLayout<timeval>.size)
                return fcntl(descriptor, F_SETFL, fcntl(descriptor, F_GETFL) & ~O_NONBLOCK) != -1
                    && setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size)) == 0
                    && setsockopt(descriptor, SOL_SOCKET, SO_RCVTIMEO, &limit, size) == 0
                    && setsockopt(descriptor, SOL_SOCKET, SO_SNDTIMEO, &limit, size) == 0
            },
            receive: { Darwin.recv($0, $1, $2, 0) },
            send: { Darwin.send($0, $1, $2, 0) },
            close: { _ = Darwin.close($0) },
            errno: { Darwin.errno }
        )
    }

    /// Why the server could not start.
    enum ServerError: Error, Equatable, CustomStringConvertible {
        case socket(Int32)
        case configure(Int32)
        case bind(port: UInt16, errno: Int32)
        case listen(Int32)
        case noPort

        var description: String {
            switch self {
            case .socket(let code): "could not open a socket (\(String(cString: strerror(code))))"
            case .configure(let code): "could not set up the socket (\(String(cString: strerror(code))))"
            case .bind(let port, let code):
                "could not listen on 127.0.0.1:\(port) (\(String(cString: strerror(code))))"
            case .listen(let code): "could not listen (\(String(cString: strerror(code))))"
            case .noPort: "could not tell which port it listens on"
            }
        }
    }

    /// An HTTP server on 127.0.0.1 and nowhere else: one request per connection, a few connections at once, and each
    /// read and write given up after a timeout.
    final class LoopbackServer: @unchecked Sendable {
        static let maxConnections = 2
        static let timeout: TimeInterval = 2
        static let backlog: Int32 = 8

        private let syscalls: Syscalls
        private let handle: @Sendable (HTTPRequest, UInt16) -> HTTPResponse
        private let lock = NSLock()
        private let acceptQueue = DispatchQueue(label: "SimMirrorKit.accept")
        private var source: DispatchSourceRead?
        private var active = 0
        private var port: UInt16 = 0

        /// `handle` answers a request, given the port the server listens on.
        init(syscalls: Syscalls = .live, handle: @escaping @Sendable (HTTPRequest, UInt16) -> HTTPResponse) {
            self.syscalls = syscalls
            self.handle = handle
        }

        /// Starts listening, answering the port it listens on.
        func start(port: UInt16) throws -> UInt16 {
            let listener = syscalls.socket()
            guard listener >= 0 else { throw ServerError.socket(syscalls.errno()) }
            do {
                guard syscalls.configureListener(listener) else { throw ServerError.configure(syscalls.errno()) }
                guard syscalls.bind(listener, port) == 0 else {
                    throw ServerError.bind(port: port, errno: syscalls.errno())
                }
                guard syscalls.listen(listener, Self.backlog) == 0 else { throw ServerError.listen(syscalls.errno()) }
                guard let bound = syscalls.boundPort(listener) else { throw ServerError.noPort }
                let source = DispatchSource.makeReadSource(fileDescriptor: listener, queue: acceptQueue)
                let syscalls = syscalls
                source.setEventHandler { [weak self] in self?.acceptPending(on: listener) }
                source.setCancelHandler { syscalls.close(listener) }
                lock.withLock {
                    self.source = source
                    self.port = bound
                }
                source.resume()
                return bound
            } catch {
                syscalls.close(listener)
                throw error
            }
        }

        /// Stops accepting connections; one being answered is still answered.
        func stop() {
            let source = lock.withLock { () -> DispatchSourceRead? in
                defer { self.source = nil }
                return self.source
            }
            source?.cancel()
        }

        var connections: Int { lock.withLock { active } }

        /// Accepts every connection waiting on the listener.
        func acceptPending(on listener: Int32) {
            var connection = syscalls.accept(listener)
            while connection >= 0 {
                admit(connection)
                connection = syscalls.accept(listener)
            }
        }

        private func admit(_ connection: Int32) {
            guard syscalls.configureConnection(connection, Self.timeout) else {
                syscalls.close(connection)
                return
            }
            let admitted = lock.withLock { () -> Bool in
                guard active < Self.maxConnections else { return false }
                active += 1
                return true
            }
            guard admitted else {
                write(.error(.busy, "the app is answering other requests"), to: connection)
                syscalls.close(connection)
                return
            }
            // A thread of its own: answering waits for the app's main thread, and blocking work on a dispatch queue
            // can hold up the app's own work, or wait behind it, where the system caps how many threads run.
            Thread.detachNewThread { [self] in
                serve(connection)
                lock.withLock { active -= 1 }
            }
        }

        /// Reads one request, answers it and closes the connection; a peer that goes quiet first is let go.
        func serve(_ connection: Int32) {
            defer { syscalls.close(connection) }
            var buffer = Data()
            var chunk = [UInt8](repeating: 0, count: 4096)
            var parsed = HTTPParse.incomplete
            while parsed == .incomplete {
                let count = chunk.withUnsafeMutableBytes { raw in
                    raw.baseAddress.map { syscalls.receive(connection, $0, raw.count) } ?? 0
                }
                guard count > 0 else { return }
                buffer.append(contentsOf: chunk[0..<count])
                parsed = HTTPRequestParser.parse(buffer)
            }
            if case .request(let request) = parsed {
                write(handle(request, lock.withLock { port }), to: connection)
            } else if case .failure(let code, let message) = parsed {
                write(.error(code, message), to: connection)
            }
        }

        private func write(_ response: HTTPResponse, to connection: Int32) {
            let bytes = response.bytes
            bytes.withUnsafeBytes { raw in
                var sent = 0
                while sent < raw.count {
                    guard let base = raw.baseAddress else { return }
                    let wrote = syscalls.send(connection, base.advanced(by: sent), raw.count - sent)
                    guard wrote > 0 else { return }
                    sent += wrote
                }
            }
        }
    }
#endif
