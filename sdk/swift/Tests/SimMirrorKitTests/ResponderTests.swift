// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import SimMirrorKit

struct ResponderTests {
    static let secret = "a-secret-long-enough-for-simmirror-tests"

    final class Asked: @unchecked Sendable {
        private let lock = NSLock()
        private var limits: [Int] = []
        func record(_ limit: Int) { lock.withLock { limits.append(limit) } }
        var all: [Int] { lock.withLock { limits } }
    }

    private func responder(_ result: CaptureResult, asked: Asked = Asked(), maxBody: Int = Responder.maxBodyBytes)
        -> Responder
    {
        Responder(secret: Self.secret, port: 5000, maxNodes: 300, maxBodyBytes: maxBody) { limit in
            asked.record(limit)
            return result
        }
    }

    private func request(
        _ path: String = "/v1/hierarchy", method: String = "GET", query: [String: String] = [:],
        headers: [String: String] = ["authorization": "Bearer \(secret)", "host": "127.0.0.1:5000"]
    ) -> HTTPRequest {
        HTTPRequest(method: method, path: path, query: query, headers: headers)
    }

    private func error(_ response: HTTPResponse) throws -> ErrorBody {
        try JSONDecoder().decode(ErrorBody.self, from: response.body)
    }

    @Test func noSecretIsAnsweredUnauthorizedBeforeAnythingElse() throws {
        let asked = Asked()
        let responder = responder(.inactive, asked: asked)
        for headers in [
            [:], ["authorization": "Bearer wrong"], ["authorization": Self.secret], ["origin": "https://x"],
        ] {
            let response = responder.respond(to: request("/nowhere", method: "POST", headers: headers))
            #expect(response.status == 401)
            #expect(try error(response) == ErrorBody(.unauthorized, "SimMirror's secret is required"))
        }
        #expect(asked.all.isEmpty)
    }

    @Test func aRequestFromAPageOrToAnotherHostIsRefused() throws {
        let responder = responder(.inactive)
        let fromPage = responder.respond(
            to: request(headers: ["authorization": "Bearer \(Self.secret)", "host": "127.0.0.1:5000", "origin": "null"])
        )
        #expect(try error(fromPage) == ErrorBody(.badRequest, "a request from a web page is refused"))
        for host in ["localhost:5000", "127.0.0.1:5001", "evil.example"] {
            let response = responder.respond(
                to: request(headers: ["authorization": "Bearer \(Self.secret)", "host": host]))
            #expect(try error(response) == ErrorBody(.badRequest, "a request is addressed to 127.0.0.1:5000"))
        }
        let noHost = responder.respond(to: request(headers: ["authorization": "Bearer \(Self.secret)"]))
        #expect(noHost.status == 400)
    }

    @Test func onlyAGetOfTheHierarchyIsServed() throws {
        let responder = responder(.inactive)
        #expect(
            try error(responder.respond(to: request("/v1/other")))
                == ErrorBody(.notFound, "only /v1/hierarchy is served"))
        #expect(
            try error(responder.respond(to: request(method: "POST")))
                == ErrorBody(.methodNotAllowed, "/v1/hierarchy is read with GET"))
    }

    @Test func maxNodesIsAWholeNumberAndNeverMoreThanTheAppAllows() throws {
        let asked = Asked()
        let responder = responder(.inactive, asked: asked)
        for bad in ["0", "-3", "many", ""] {
            let response = responder.respond(to: request(query: ["max_nodes": bad]))
            #expect(try error(response) == ErrorBody(.badRequest, "max_nodes is a whole number above 0"))
        }
        _ = responder.respond(to: request(query: ["max_nodes": "25"]))
        _ = responder.respond(to: request(query: ["max_nodes": "9000"]))
        _ = responder.respond(to: request())
        #expect(asked.all == [25, 300, 300])
    }

    @Test func anAppNotInFrontOrStuckSaysSo() throws {
        #expect(
            try error(responder(.inactive).respond(to: request())) == ErrorBody(.inactive, "the app is not in front"))
        let busy = responder(.busy).respond(to: request())
        #expect(busy.status == 503)
        #expect(try error(busy) == ErrorBody(.busy, "the app's main thread did not answer in time"))
    }

    @Test func aHierarchyIsAnsweredAsJSON() throws {
        let hierarchy = testHierarchy(nodes: [testNode(.button, label: "Save")])
        let response = responder(.hierarchy(hierarchy)).respond(to: request())
        #expect(response.status == 200)
        #expect(try JSONDecoder().decode(Hierarchy.self, from: response.body) == hierarchy)
    }

    @Test func aHierarchyTooLargeIsCutDownAndOneThatStaysTooLargeIsRefused() throws {
        let nodes = (0..<40).map { testNode(.text, label: String(repeating: "x", count: 100) + "\($0)") }
        let hierarchy = testHierarchy(nodes: nodes)
        let full = WireJSON.encode(hierarchy).count
        let cut = responder(.hierarchy(hierarchy), maxBody: full / 3).respond(to: request())
        let answered = try JSONDecoder().decode(Hierarchy.self, from: cut.body)
        #expect(cut.status == 200 && answered.truncated && answered.nodeCount == 10)
        let refused = responder(.hierarchy(hierarchy), maxBody: 10).respond(to: request())
        #expect(try error(refused) == ErrorBody(.tooLarge, "the hierarchy is too large to send even cut down"))
    }

    @Test func pruningKeepsNodesBreadthFirstAcrossWindows() {
        let deep = testNode(label: "a", children: [testNode(label: "a1", children: [testNode(label: "a11")])])
        let wide = testNode(label: "b", children: [testNode(label: "b1"), testNode(label: "b2")])
        var hierarchy = testHierarchy(nodes: [deep, wide])
        hierarchy.windows.append(Window(level: 1, key: false, nodes: [testNode(label: "c")]))
        hierarchy.nodeCount = 7
        let pruned = Pruner.prune(hierarchy, to: 4)
        #expect(pruned.nodeCount == 4 && pruned.truncated)
        #expect(pruned.windows[0].nodes.map(\.label) == ["a", "b"])
        #expect(pruned.windows[0].nodes[0].children.map(\.label) == ["a1"])
        #expect(pruned.windows[0].nodes[0].children[0].children.isEmpty)
        #expect(pruned.windows[0].nodes[1].children.map(\.label) == ["b1"])
        #expect(pruned.windows[1].nodes.isEmpty)
        let whole = Pruner.prune(hierarchy, to: 100)
        #expect(whole.nodeCount == 7 && !whole.truncated)
    }

    @Test func aBudgetSaysWhenItRanOut() {
        var budget = NodeBudget(limit: 1)
        #expect(budget.take() && !budget.exhausted && budget.remaining == 0)
        #expect(!budget.take() && budget.exhausted)
        #expect(NodeBudget(limit: -4).remaining == 0)
    }
}
