// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Darwin
    import Foundation
    import UIKit

    /// The SDK while it runs: the server, the listing that says where it is, and the app's comings and goings.
    @MainActor
    final class Runtime {
        static let shared = Runtime()

        let describers = DescriberRegistry()
        private let host: AppHost
        private let process: ProcessContext
        private let syscalls: Syscalls
        private let fileSystem: FileSystem
        private let random: Secret.Random
        private let log: Log
        private let now: () -> Date
        /// How long a request waits for the main thread before it is answered busy.
        private let mainThreadTimeout: TimeInterval
        private var server: LoopbackServer?
        private var writer: DiscoveryWriter?
        private var listing: Listing?

        init(
            host: AppHost = UIKitAppHost(),
            process: ProcessContext = .current(),
            syscalls: Syscalls = .live,
            fileSystem: FileSystem = .live,
            random: @escaping Secret.Random = Secret.systemRandom,
            log: Log = .system,
            now: @escaping () -> Date = Date.init,
            mainThreadTimeout: TimeInterval = 2
        ) {
            self.host = host
            self.process = process
            self.syscalls = syscalls
            self.fileSystem = fileSystem
            self.random = random
            self.log = log
            self.now = now
            self.mainThreadTimeout = mainThreadTimeout
        }

        var isRunning: Bool { server != nil }

        /// Where the listing was written, while the SDK runs.
        var listingPath: String? { writer?.path }

        func start(_ options: SimMirror.Options) {
            guard server == nil else { return }
            guard process.refusal == nil, let udid = process.deviceUDID, let bundleID = process.bundleID else {
                log.write("SimMirror does not start: \(process.refusal ?? "")")
                return
            }
            let policy = DebugDataPolicy.decide(requested: options.swiftUIDebugData, os: process.os)
            if policy == .on { setenv(DebugDataPolicy.variable, DebugDataPolicy.properties, 0) }
            guard let secret = Secret.make(random: random) else {
                log.write("SimMirror does not start: the system gave no random bytes for its secret")
                return
            }
            let capturer = HierarchyCapturer(
                app: AppInfo(
                    bundleID: bundleID, name: String(process.name.prefix(100)), pid: process.pid, active: true),
                describers: describers, redactValues: options.redactValues, debugData: policy
            )
            let maxNodes = max(1, options.maxNodes)
            let timeout = mainThreadTimeout
            let server = LoopbackServer(syscalls: syscalls) { [weak self] request, port in
                Responder(secret: secret, port: port, maxNodes: maxNodes) { limit in
                    Self.onMain(timeout: timeout) { self?.capture(with: capturer, maxNodes: limit) }
                }.respond(to: request)
            }
            let port: UInt16
            do {
                port = try server.start(port: options.port)
            } catch {
                log.write("SimMirror does not start: it \(error)")
                return
            }
            let writer = DiscoveryWriter(sharedFolder: process.sharedFolder, home: process.home, fileSystem: fileSystem)
            let listing = Listing(
                protocolVersion: WireProtocol.version, sdkVersion: SimMirror.sdkVersion, deviceUDID: udid,
                bundleID: bundleID, name: String(process.name.prefix(100)), pid: process.pid, port: port,
                secret: secret, active: host.isActive, startedAt: Self.timestamp(now())
            )
            guard let path = writer.write(listing) else {
                server.stop()
                log.write("SimMirror does not start: it could not write where it listens under Library/Caches")
                return
            }
            self.server = server
            self.writer = writer
            self.listing = listing
            host.observe { [weak self] event in self?.handle(event) }
            log.write("SimMirror answers on 127.0.0.1:\(port); it said so in \(path)")
        }

        func stop() {
            guard let server else { return }
            server.stop()
            host.stopObserving()
            if let listing { writer?.remove(listing) }
            self.server = nil
            writer = nil
            listing = nil
            log.write("SimMirror stopped")
        }

        func handle(_ event: AppEvent) {
            switch event {
            case .terminating:
                stop()
            case .active, .inactive:
                guard var listing, let writer else { return }
                listing.active = event == .active
                self.listing = listing
                writer.write(listing)
            }
        }

        func capture(with capturer: HierarchyCapturer, maxNodes: Int) -> CaptureResult {
            guard host.isActive else { return .inactive }
            return .hierarchy(capturer.capture(windows: host.windows, keyboard: host.keyboardFrame, maxNodes: maxNodes))
        }

        /// Runs work on the main thread and waits for it, busy when it does not get there in time.
        nonisolated static func onMain(
            timeout: TimeInterval,
            _ work: @escaping @MainActor @Sendable () -> CaptureResult?
        ) -> CaptureResult {
            let done = DispatchSemaphore(value: 0)
            let box = ResultBox()
            Task { @MainActor in
                box.set(work())
                done.signal()
            }
            guard done.wait(timeout: .now() + timeout) == .success else { return .busy }
            return box.value ?? .busy
        }

        nonisolated static func timestamp(_ date: Date) -> String {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            formatter.timeZone = TimeZone(identifier: "UTC")
            return formatter.string(from: date)
        }
    }

    /// A value handed from the main thread to the thread waiting for it.
    final class ResultBox: @unchecked Sendable {
        private let lock = NSLock()
        private var stored: CaptureResult?

        func set(_ value: CaptureResult?) { lock.withLock { stored = value } }
        var value: CaptureResult? { lock.withLock { stored } }
    }
#endif
