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
    private let handle: SimDeviceHandle
    private let preference: TransportPolicy.Preference
    private let idleKeyFrames: Bool
    private let log: Log
    private let pictures = Pictures()
    private let lock = NSLock()
    private var geometry: ScreenGeometry?
    private var framebuffer: Framebuffer?
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

    private func display() throws -> Framebuffer {
        let screen = try self.screen()
        lock.lock()
        defer { lock.unlock() }
        if let framebuffer { return framebuffer }
        let opened = try Framebuffer(device: handle, screen: screen)
        framebuffer = opened
        return opened
    }

    public func screenshot(_ request: ScreenshotRequest) async throws -> JPEG {
        let framebuffer = try display()
        let surface = try framebuffer.surface(waiting: 2)
        let plan = try ScreenshotPlan.make(
            request, surfaceWidth: IOSurfaceGetWidth(surface), surfaceHeight: IOSurfaceGetHeight(surface), screen: try screen()
        )
        return try pictures.jpeg(surface, plan: plan)
    }

    public func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> {
        try H264Stream(framebuffer: display(), pictures: pictures, settings: settings, idleKeyFrames: idleKeyFrames, log: log).units()
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

    private let warming = DispatchGroup()
    private var warmStarted = false

    /// Warm the device in the background, once: `warmed` returns when that is done.
    public func startWarming() {
        lock.lock()
        defer { lock.unlock() }
        guard !warmStarted else { return }
        warmStarted = true
        warming.enter()
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            warm()
            warming.leave()
        }
    }

    public func warmed() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            warming.notify(queue: .global()) { continuation.resume() }
        }
    }

    /// Open what the first screenshot and stream need -- the framebuffer, the JPEG pipeline and the H.264 encoder --
    /// so neither waits for them. Nothing that fails here is a failure: the request that needs it says why.
    func warm() {
        let started = DispatchTime.now().uptimeNanoseconds
        defer { log.debug("warmed the framebuffer, the JPEG pipeline and the H.264 encoder in \((DispatchTime.now().uptimeNanoseconds - started) / 1_000_000)ms") }
        guard let framebuffer = try? display(), let surface = try? framebuffer.surface(waiting: 5), let screen = try? screen()
        else { return }
        let pictures = self.pictures
        // The JPEG pipeline and the encoder share nothing, so they warm at once.
        DispatchQueue.concurrentPerform(iterations: 2) { part in
            if part == 1 {
                H264Stream.warm(framebuffer: framebuffer, pictures: pictures)
                return
            }
            let request = ScreenshotRequest(maxWidth: 160, quality: 40)
            let width = IOSurfaceGetWidth(surface)
            let height = IOSurfaceGetHeight(surface)
            if let plan = try? ScreenshotPlan.make(request, surfaceWidth: width, surfaceHeight: height, screen: screen) {
                _ = try? pictures.jpeg(surface, plan: plan)
            }
        }
    }

    /// Whether the simulator is still booted.
    public var booted: Bool {
        handle.state == "Booted"
    }

    public func close() {
        lock.lock()
        let framebuffer = self.framebuffer
        self.framebuffer = nil
        driver = nil
        lock.unlock()
        framebuffer?.close()
    }
}
