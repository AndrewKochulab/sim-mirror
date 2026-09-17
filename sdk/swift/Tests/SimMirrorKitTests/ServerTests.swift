// SPDX-License-Identifier: Apache-2.0
import Darwin
import Foundation
import Testing

@testable import SimMirrorKit

struct ServerTests {
    private static func echo(_ request: HTTPRequest, port: UInt16) -> HTTPResponse {
        HTTPResponse(status: 200, body: Data("\(request.method) \(request.path) \(port)".utf8))
    }

    @Test func itAnswersOnLoopbackOnThePortItPickedAndStopsWhenAsked() async throws {
        let server = LoopbackServer { request, port in Self.echo(request, port: port) }
        let port = try server.start(port: 0)
        #expect(port >= 1024)
        let answer = try #require(await RawClient.request(port: port, secret: nil, path: "/v1/hierarchy?x=1"))
        #expect(answer.status == 200 && String(decoding: answer.body, as: UTF8.self) == "GET /v1/hierarchy \(port)")
        #expect(answer.head.contains("Connection: close"))
        server.stop()
        server.stop()
        try await Task.sleep(nanoseconds: 100_000_000)
        #expect(await RawClient.request(port: port, secret: nil) == nil)
    }

    @Test func aRequestThatIsNotHTTPIsRefusedAndOneThatStopsHalfwayIsLeft() async throws {
        let server = LoopbackServer { request, port in Self.echo(request, port: port) }
        defer { server.stop() }
        let port = try server.start(port: 0)
        let garbage = try #require(await RawClient.send(Data("HELLO\r\n\r\n".utf8), port: port))
        #expect(garbage.status == 400)
        let huge = Data(("GET / HTTP/1.1\r\nX: " + String(repeating: "a", count: 9000)).utf8)
        #expect(try #require(await RawClient.send(huge, port: port)).status == 431)
        #expect(await RawClient.send(Data("GET / HTTP/1.1\r\n".utf8), port: port, readAnswer: false) != nil)
        let later = try #require(await RawClient.request(port: port, secret: nil))
        #expect(later.status == 200)
    }

    @Test func aSecondServerCannotTakeAPortInUse() throws {
        let first = LoopbackServer { request, port in Self.echo(request, port: port) }
        defer { first.stop() }
        let port = try first.start(port: 0)
        let second = LoopbackServer { request, port in Self.echo(request, port: port) }
        #expect(throws: ServerError.bind(port: port, errno: EADDRINUSE)) { try second.start(port: port) }
    }

    @Test func connectionsBeyondTheLimitAreAnsweredBusy() async throws {
        let release = DispatchSemaphore(value: 0)
        let server = LoopbackServer { request, port in
            _ = release.wait(timeout: .now() + 10)
            return Self.echo(request, port: port)
        }
        defer { server.stop() }
        let port = try server.start(port: 0)
        let slow = (0..<LoopbackServer.maxConnections).map { _ in
            Task { await RawClient.request(port: port, secret: nil) }
        }
        for _ in 0..<500 where server.connections < LoopbackServer.maxConnections {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        #expect(server.connections == LoopbackServer.maxConnections)
        let busy = try #require(await RawClient.request(port: port, secret: nil))
        #expect(busy.status == 503)
        #expect(try JSONDecoder().decode(ErrorBody.self, from: busy.body).error.code == .busy)
        for _ in slow { release.signal() }
        for task in slow { #expect(await task.value?.status == 200) }
    }

    /// Each system call answers as told, and says which were made.
    final class FakeSyscalls: @unchecked Sendable {
        var socket: Int32 = 7
        var configures = true
        var binds: Int32 = 0
        var listens: Int32 = 0
        var port: UInt16? = 4321
        var accepted: [Int32] = []
        var connectionConfigures = true
        var received: [Data] = []
        var sendResult: Int? = nil
        var sent = Data()
        var closed: [Int32] = []
        let lock = NSLock()

        var syscalls: Syscalls {
            Syscalls(
                socket: { self.socket },
                configureListener: { _ in self.configures },
                bind: { _, _ in self.binds },
                listen: { _, _ in self.listens },
                boundPort: { _ in self.port },
                accept: { _ in self.lock.withLock { self.accepted.isEmpty ? -1 : self.accepted.removeFirst() } },
                configureConnection: { _, _ in self.connectionConfigures },
                receive: { _, buffer, capacity in
                    self.lock.withLock {
                        guard !self.received.isEmpty else { return 0 }
                        let chunk = self.received.removeFirst().prefix(capacity)
                        chunk.copyBytes(to: buffer.assumingMemoryBound(to: UInt8.self), count: chunk.count)
                        return chunk.count
                    }
                },
                send: { _, bytes, count in
                    self.lock.withLock {
                        if let result = self.sendResult { return result }
                        self.sent.append(bytes.assumingMemoryBound(to: UInt8.self), count: count)
                        return count
                    }
                },
                close: { descriptor in self.lock.withLock { self.closed.append(descriptor) } },
                errno: { EACCES }
            )
        }
    }

    @Test func everyStepOfStartingThatFailsSaysWhichAndClosesTheSocket() throws {
        let steps: [((FakeSyscalls) -> Void, ServerError, Bool)] = [
            ({ $0.socket = -1 }, .socket(EACCES), false),
            ({ $0.configures = false }, .configure(EACCES), true),
            ({ $0.binds = -1 }, .bind(port: 80, errno: EACCES), true),
            ({ $0.listens = -1 }, .listen(EACCES), true),
            ({ $0.port = nil }, .noPort, true),
        ]
        for (breakIt, expected, closes) in steps {
            let fake = FakeSyscalls()
            breakIt(fake)
            let server = LoopbackServer(syscalls: fake.syscalls) { request, port in Self.echo(request, port: port) }
            #expect(throws: expected) { try server.start(port: 80) }
            #expect(fake.closed == (closes ? [7] : []))
        }
        #expect(ServerError.socket(EACCES).description == "could not open a socket (Permission denied)")
        #expect(ServerError.configure(EACCES).description == "could not set up the socket (Permission denied)")
        #expect(
            ServerError.bind(port: 80, errno: EADDRINUSE).description
                == "could not listen on 127.0.0.1:80 (Address already in use)")
        #expect(ServerError.listen(EACCES).description == "could not listen (Permission denied)")
        #expect(ServerError.noPort.description == "could not tell which port it listens on")
    }

    @Test func aConnectionThatCannotBeSetUpIsClosedAndTheNextOneServed() {
        let fake = FakeSyscalls()
        fake.accepted = [11]
        fake.connectionConfigures = false
        let server = LoopbackServer(syscalls: fake.syscalls) { request, port in Self.echo(request, port: port) }
        server.acceptPending(on: 7)
        #expect(fake.closed == [11] && server.connections == 0)
    }

    @Test func aRequestInPiecesIsAnsweredAndAPeerThatStopsReadingIsLetGo() {
        let fake = FakeSyscalls()
        fake.received = [Data("GET /v1/hierarchy HTT".utf8), Data("P/1.1\r\n\r\n".utf8)]
        let server = LoopbackServer(syscalls: fake.syscalls) { request, port in Self.echo(request, port: port) }
        server.serve(12)
        #expect(String(decoding: fake.sent, as: UTF8.self).hasSuffix("GET /v1/hierarchy 0"))
        #expect(fake.closed == [12])
        let stuck = FakeSyscalls()
        stuck.received = [Data("GET / HTTP/1.1\r\n\r\n".utf8)]
        stuck.sendResult = 0
        LoopbackServer(syscalls: stuck.syscalls) { request, port in Self.echo(request, port: port) }.serve(13)
        #expect(stuck.sent.isEmpty && stuck.closed == [13])
    }

    @Test func theLiveSystemCallsFailAsTheSystemDoes() {
        let live = Syscalls.live
        #expect(!live.configureListener(-1))
        #expect(live.bind(-1, 0) == -1)
        #expect(live.boundPort(-1) == nil)
        #expect(!live.configureConnection(-1, 1))
        #expect(live.accept(-1) == -1)
        #expect(live.errno() == EBADF)
    }
}
