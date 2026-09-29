// SPDX-License-Identifier: Apache-2.0
import Foundation
import HelperCore
import IOSurface

/// A booted simulator reached through Apple's frameworks: the `Device` the helper serves.
///
/// Each part is opened the first time it is asked for and kept: the framebuffer, the transport input goes through, and
/// the accessibility translator. A part that cannot be opened says why each time it is asked, and the others still work.
public final class SimulatorDevice: Device, @unchecked Sendable {
    public let coreSimulator: String?
    /// A simulator's screen is its framebuffer, which is not worth saying.
    public let source: String? = nil
    private let handle: SimDeviceHandle
    private let preference: TransportPolicy.Preference
    private let idleKeyFrames: Bool
    private let log: Log
    /// How long a screenshot or stream waits for the simulator's first picture, and how long warming up does.
    static let firstPictureS = 2.0
    static let warmingS = 5.0
    private let lock = NSLock()
    private var geometry: ScreenGeometry?
    private var feed: ScreenFeed?
    private var driver: InputDriver?
    private var reader: AccessibilityReader?

    public init(options: DeviceOptions, idleKeyFrames: Bool = true, log: Log = Log()) throws {
        let developerDir = SimulatorFrameworks.developerDir()
        coreSimulator = try SimulatorFrameworks.load(developerDir: developerDir)
        handle = try SimDeviceHandle(udid: options.udid, developerDir: developerDir)
        try handle.requireBooted()
        preference = options.hid
        self.idleKeyFrames = idleKeyFrames
        self.log = log
    }

    public func screen() throws -> ScreenGeometry {
        lock.lock()
        defer { lock.unlock() }
        if let geometry { return geometry }
        let found = try handle.screen()
        geometry = found
        return found
    }

    /// The screen, read from the simulator's framebuffer, opened the first time it is asked for.
    private func display() throws -> ScreenFeed {
        let screen = try self.screen()
        lock.lock()
        defer { lock.unlock() }
        if let feed { return feed }
        let opened = ScreenFeed(
            source: try Framebuffer(device: handle, screen: screen), pictures: Pictures(), idleKeyFrames: idleKeyFrames,
            firstPictureS: Self.firstPictureS, log: log
        )
        feed = opened
        return opened
    }

    public func screenshot(_ request: ScreenshotRequest) async throws -> JPEG {
        try display().screenshot(request, screen: try screen())
    }

    public func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> {
        try display().stream(settings)
    }

    public func input() throws -> InputDriver {
        let screen = try self.screen()
        lock.lock()
        defer { lock.unlock() }
        if let driver { return driver }
        var reasons: [String] = []
        for name in TransportPolicy.order(preference, coreSimulator: coreSimulator) {
            do {
                let transport: HIDTransport = name == "dtuhid" ? try DTUHIDTransport(device: handle) : try IndigoTransport(device: handle)
                let opened = InputDriver(transport: transport, screen: screen)
                driver = opened
                log.info("input goes through \(name)")
                return opened
            } catch {
                reasons.append("\(name): \(HelperFailure.from(error).message)")
            }
        }
        throw HelperFailure("input cannot reach the simulator (\(reasons.joined(separator: "; ")))", status: 409)
    }

    public func tree() async throws -> AXNode {
        let reader: AccessibilityReader = try {
            lock.lock()
            defer { lock.unlock() }
            if let reader = self.reader { return reader }
            let opened = try AccessibilityReader(device: handle)
            self.reader = opened
            return opened
        }()
        return try await reader.tree()
    }

    private let warming = Warming()

    /// Warm the device in the background, once: `warmed` returns when that is done.
    public func startWarming() {
        warming.start { [self] in warm() }
    }

    public func warmed() async {
        await warming.done()
    }

    /// Open what the first screenshot and stream need -- the framebuffer, the JPEG pipeline and the H.264 encoder --
    /// so neither waits for them. Nothing that fails here is a failure: the request that needs it says why.
    func warm() {
        guard let feed = try? display(), let screen = try? screen() else { return }
        try? feed.warm(screen: screen, waiting: Self.warmingS)
    }

    /// Whether the simulator is still booted.
    public var booted: Bool {
        handle.state == "Booted"
    }

    public func close() {
        lock.lock()
        let feed = self.feed
        self.feed = nil
        driver = nil
        lock.unlock()
        feed?.source.close()
    }
}
