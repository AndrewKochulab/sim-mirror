// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Foundation

    /// What the app had to give when asked for its hierarchy.
    enum CaptureResult: Equatable, Sendable {
        case hierarchy(Hierarchy)
        /// The app is not in front.
        case inactive
        /// The app's main thread did not get to it in time -- paused in a debugger, or stuck.
        case busy
    }

    /// Answers a request: who may ask is checked first, then what they ask for.
    struct Responder: Sendable {
        /// The most bytes a hierarchy is sent as; a larger one is cut down, up to `pruneAttempts` times.
        static let maxBodyBytes = 2 * 1024 * 1024
        static let pruneAttempts = 3

        var secret: String
        var port: UInt16
        var maxNodes: Int
        var maxBodyBytes: Int = Responder.maxBodyBytes
        /// Captures the hierarchy with at most so many nodes, on the main thread, waiting for it.
        var capture: @Sendable (Int) -> CaptureResult

        func respond(to request: HTTPRequest) -> HTTPResponse {
            let authorization = request.header("Authorization") ?? ""
            let given = authorization.hasPrefix("Bearer ") ? String(authorization.dropFirst("Bearer ".count)) : ""
            guard Secret.matches(given, secret) else {
                return .error(.unauthorized, "SimMirror's secret is required")
            }
            guard request.header("Origin") == nil else {
                return .error(.badRequest, "a request from a web page is refused")
            }
            guard request.header("Host") == "127.0.0.1:\(port)" else {
                return .error(.badRequest, "a request is addressed to 127.0.0.1:\(port)")
            }
            guard request.path == WireProtocol.hierarchyPath else {
                return .error(.notFound, "only \(WireProtocol.hierarchyPath) is served")
            }
            guard request.method == "GET" else {
                return .error(.methodNotAllowed, "\(WireProtocol.hierarchyPath) is read with GET")
            }
            var limit = maxNodes
            if let asked = request.query["max_nodes"] {
                guard let count = Int(asked), count > 0 else {
                    return .error(.badRequest, "max_nodes is a whole number above 0")
                }
                limit = min(count, maxNodes)
            }
            switch capture(limit) {
            case .inactive:
                return .error(.inactive, "the app is not in front")
            case .busy:
                return .error(.busy, "the app's main thread did not answer in time")
            case .hierarchy(let hierarchy):
                return encoded(hierarchy)
            }
        }

        /// The hierarchy as JSON, cut down while it is too large to send.
        func encoded(_ hierarchy: Hierarchy) -> HTTPResponse {
            var current = hierarchy
            for _ in 0...Self.pruneAttempts {
                let body = WireJSON.encode(current)
                if body.count <= maxBodyBytes {
                    return HTTPResponse(status: 200, body: body)
                }
                current = Pruner.prune(current, to: current.nodeCount / 2)
            }
            return .error(.tooLarge, "the hierarchy is too large to send even cut down")
        }
    }

    /// Cuts a hierarchy down to a number of nodes, keeping those nearest the top of each window first.
    enum Pruner {
        static func prune(_ hierarchy: Hierarchy, to limit: Int) -> Hierarchy {
            var budget = NodeBudget(limit: limit)
            var result = hierarchy
            result.windows = hierarchy.windows.map { window in
                var window = window
                window.nodes = budget.keep(window.nodes)
                return window
            }
            result.nodeCount = result.windows.reduce(0) { total, window in
                total + window.nodes.reduce(0) { $0 + $1.count }
            }
            result.truncated = hierarchy.truncated || result.nodeCount < hierarchy.nodeCount
            return result
        }
    }

    /// How many more nodes an answer may hold.
    struct NodeBudget {
        private(set) var remaining: Int
        private(set) var exhausted = false

        init(limit: Int) { remaining = max(0, limit) }

        /// Takes one node, answering whether there was room for it.
        mutating func take() -> Bool {
            guard remaining > 0 else {
                exhausted = true
                return false
            }
            remaining -= 1
            return true
        }

        /// The nodes that fit, breadth first: every node of a level is kept before any node below it.
        mutating func keep(_ nodes: [Node]) -> [Node] {
            var kept = Set<[Int]>()
            var level = nodes.enumerated().map { ([$0.offset], $0.element) }
            walking: while !level.isEmpty {
                var next: [([Int], Node)] = []
                for (path, node) in level {
                    guard take() else { break walking }
                    kept.insert(path)
                    next += node.children.enumerated().map { (path + [$0.offset], $0.element) }
                }
                level = next
            }
            return Self.rebuild(nodes, at: [], keeping: kept)
        }

        private static func rebuild(_ nodes: [Node], at path: [Int], keeping kept: Set<[Int]>) -> [Node] {
            nodes.enumerated().compactMap { offset, node in
                let here = path + [offset]
                guard kept.contains(here) else { return nil }
                var node = node
                node.children = rebuild(node.children, at: here, keeping: kept)
                return node
            }
        }
    }
#endif
