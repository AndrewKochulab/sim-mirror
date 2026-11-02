// SPDX-License-Identifier: Apache-2.0
#if DEBUG && os(macOS)
    import AppKit
    import CryptoKit
    import Foundation

    /// A bounded result shared between the socket thread and the main actor. A timed-out queued input is cancelled.
    final class MacReply: @unchecked Sendable {
        private let lock = NSLock()
        private var cancelled = false
        private var result: HTTPResponse?
        func finish(_ work: () -> HTTPResponse) {
            lock.lock()
            guard !cancelled else { lock.unlock(); return }
            lock.unlock()
            let completed = work()
            lock.lock(); defer { lock.unlock() }
            if !cancelled { result = completed }
        }
        func take() -> HTTPResponse? {
            lock.lock(); defer { lock.unlock() }
            cancelled = true
            return result
        }
    }

    /// A Debug-only server and private listing. Sandboxed apps use their container and need the network.server entitlement.
    @MainActor
    final class MacRuntime {
        static let shared = MacRuntime()
        private var server: LoopbackServer?
        private var writer: DiscoveryWriter?
        private var listing: Listing?
        private var previousAccessibility: Any?
        private var activity: NSObjectProtocol?
        private let bundle: Bundle
        private let home: String
        private let environment: [String: String]
        private let random: Secret.Random
        private let osMajor: Int
        private let responseWaitS: Double

        init(
            bundle: Bundle = .main, home: String = NSHomeDirectory(),
            environment: [String: String] = ProcessInfo.processInfo.environment,
            random: @escaping Secret.Random = Secret.systemRandom,
            osMajor: Int = ProcessInfo.processInfo.operatingSystemVersion.majorVersion, responseWaitS: Double = 2
        ) {
            self.bundle = bundle; self.home = home; self.environment = environment
            self.random = random; self.osMajor = osMajor; self.responseWaitS = responseWaitS
        }
        var isRunning: Bool { server != nil }

        func start(_ options: SimMirror.Options) {
            guard server == nil, environment["XCODE_RUNNING_FOR_PREVIEWS"] != "1",
                !bundle.bundlePath.hasSuffix(".appex"), let id = bundle.bundleIdentifier,
                id.range(of: "^[A-Za-z0-9.-]{1,155}$", options: .regularExpression) != nil,
                let secret = Secret.make(random: random)
            else { return }
            if options.swiftUIDebugData, osMajor == 26 {
                setenv(DebugDataPolicy.variable, DebugDataPolicy.properties, 1)
            }
            let responseWaitS = self.responseWaitS
            let server = LoopbackServer(syscalls: .live) { request, port in
                // Authenticate before parsing an input body or scheduling work on the UI thread.
                guard Secret.matches(request.header("Authorization") ?? "", "Bearer " + secret) else {
                    return .error(.unauthorized, "SimMirror's secret is required")
                }
                guard request.header("Origin") == nil, request.header("Host") == "127.0.0.1:\(port)" else {
                    return .error(.badRequest, "only a loopback client without Origin is accepted")
                }
                let reply = MacReply()
                let done = DispatchSemaphore(value: 0)
                Task { @MainActor in
                    reply.finish {
                        Self.respond(request, options: options)
                    }
                    done.signal()
                }
                _ = done.wait(timeout: .now() + responseWaitS)
                return reply.take()
                    ?? .error(.busy, "the app did not answer in time; inspect its state before retrying")
            }
            guard let port = try? server.start(port: options.port) else { return }
            let digest = SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x", $0) }.joined()
            let deviceID = "macapp-" + String(id.prefix(42)) + "-" + String(digest.prefix(8))
            let started = ISO8601DateFormatter().string(from: Date())
            let listing = Listing(
                protocolVersion: WireProtocol.version, sdkVersion: SimMirror.sdkVersion, deviceUDID: deviceID,
                bundleID: id, name: String(ProcessInfo.processInfo.processName.prefix(100)), pid: getpid(),
                port: port, secret: secret, active: NSApp.isActive, startedAt: started, platform: "macos")
            let writer = DiscoveryWriter(
                sharedFolder: nil, home: home, fileSystem: .live,
                relativeFolder: "Library/Application Support/SimMirror/apps")
            guard writer.write(listing) != nil else { server.stop(); return }
            activity = ProcessInfo.processInfo.beginActivity(
                options: .userInitiatedAllowingIdleSystemSleep, reason: "SimMirror Debug app session")
            previousAccessibility = NSApp.accessibilityAttributeValue(
                NSAccessibility.Attribute(rawValue: "AXEnhancedUserInterface"))
            NSApp.accessibilitySetValue(
                true, forAttribute: NSAccessibility.Attribute(rawValue: "AXEnhancedUserInterface"))
            self.server = server; self.writer = writer; self.listing = listing
        }
        func stop() {
            server?.stop()
            if let activity { ProcessInfo.processInfo.endActivity(activity) }
            activity = nil
            if let listing { writer?.remove(listing) }
            if let previousAccessibility {
                NSApp.accessibilitySetValue(
                    previousAccessibility, forAttribute: NSAccessibility.Attribute(rawValue: "AXEnhancedUserInterface"))
            }
            server = nil; listing = nil; writer = nil; previousAccessibility = nil
        }
        static func respond(_ request: HTTPRequest, options: SimMirror.Options, windows: [NSWindow]? = nil)
            -> HTTPResponse
        {
            let owned = windows ?? NSApp.windows
            if request.method == "POST", request.path == "/v1/input" {
                do {
                    let command = try JSONDecoder().decode(MacCommand.self, from: request.body)
                    try MacInput.perform(command, windows: owned)
                    return HTTPResponse(status: 200, body: Data("{}".utf8))
                } catch let failure as MacInputFailure { return .error(.badRequest, failure.message) } catch {
                    return .error(.badRequest, "input is malformed")
                }
            }
            guard request.method == "GET", request.path == WireProtocol.hierarchyPath else {
                return .error(.notFound, "only hierarchy and input are served")
            }
            let windows = owned.filter { $0.isVisible && !$0.isMiniaturized }
            let window =
                request.query["window_id"].flatMap(Int.init).flatMap { id in windows.first { $0.windowNumber == id } }
                ?? (request.query["window_id"] == nil ? windows.first : nil)
            guard let window else { return .error(.badRequest, "choose a visible window of this app") }
            if let asked = request.query["max_nodes"], Int(asked).map({ $0 > 0 }) != true {
                return .error(.badRequest, "max_nodes is a whole number above 0")
            }
            let limit = min(options.maxNodes, request.query["max_nodes"].flatMap(Int.init) ?? options.maxNodes)
            let hierarchy = MacCapture(options: options).capture(window, limit: limit)
            return Responder(secret: "", port: 0, maxNodes: limit, capture: { _ in .busy }).encoded(hierarchy)
        }
    }
#endif
