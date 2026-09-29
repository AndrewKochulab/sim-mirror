// SPDX-License-Identifier: Apache-2.0
import AVFoundation
import CoreImage
import Foundation
import HelperCore
import ImageIO

/// A cabled real device's screen: the `Device` `sim-mirror-helper capture` serves.
///
/// It reads the screen only -- touches, typing and the element tree go through WebDriverAgent, not the cable. Nothing
/// is opened until it warms up, in the background: macOS's Camera permission, which may be asked for the first time
/// and waited on, then the device among the Mac's capture devices, then its first picture. What stops any of that is
/// what its hello answers, so SimMirror says why and reads the screen another way.
public final class CaptureDevice: Device, @unchecked Sendable {
    public let coreSimulator: String? = nil
    private let options: CaptureOptions
    private let log: Log
    private let warming = Warming()
    private let lock = NSLock()
    private var opened: (capture: UsbCapture, feed: ScreenFeed)?
    private var failure: HelperFailure?

    public init(options: CaptureOptions, log: Log = Log()) {
        self.options = options
        self.log = log
    }

    public var source: String? {
        lock.lock()
        defer { lock.unlock() }
        return opened?.capture.device.uniqueID
    }

    /// The screen as devicectl measured it -- or, once warming up failed, why it cannot be read, which is what a hello
    /// answered after warming up says.
    public func screen() throws -> ScreenGeometry {
        lock.lock()
        defer { lock.unlock() }
        if let failure { throw failure }
        return options.screen
    }

    private func feed() throws -> ScreenFeed {
        lock.lock()
        defer { lock.unlock() }
        if let failure { throw failure }
        guard let opened else { throw HelperFailure("the device's screen is not open yet", status: 503) }
        return opened.feed
    }

    public func screenshot(_ request: ScreenshotRequest) async throws -> JPEG {
        try feed().screenshot(request, screen: options.screen)
    }

    public func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> {
        try feed().stream(settings)
    }

    public func input() throws -> InputDriver {
        throw HelperFailure("a real device's touches go through WebDriverAgent, not its cable", status: 409)
    }

    public func tree() async throws -> AXNode {
        throw HelperFailure("a real device's element tree is read through WebDriverAgent, not its cable", status: 409)
    }

    public func startWarming() {
        warming.start { [self] in
            do {
                try open()
            } catch {
                let failure = HelperFailure.from(error)
                log.error(failure.message)
                lock.lock()
                self.failure = failure
                lock.unlock()
            }
        }
    }

    public func warmed() async {
        await warming.done()
    }

    private func open() throws {
        try CameraPermission.require()
        let capture = try UsbCapture(device: try find(), lingerS: options.lingerS, log: log)
        let feed = ScreenFeed(source: capture, pictures: Pictures(), idleKeyFrames: options.idleKeyFrames, firstPictureS: options.waitS, log: log)
        do {
            try feed.warm(screen: options.screen, waiting: options.waitS)
        } catch {
            capture.close()
            throw error
        }
        log.info("reading the screen of \(options.name) from capture device \(capture.device.uniqueID)")
        lock.lock()
        opened = (capture, feed)
        lock.unlock()
    }

    /// The capture device that shows this device's screen (`CaptureChoice`).
    private func find() throws -> AVCaptureDevice {
        func choice(_ devices: [AVCaptureDevice]) -> CaptureChoice {
            let candidates = devices.map { CaptureCandidate(id: $0.uniqueID, name: $0.localizedName) }
            return CaptureChoice.pick(candidates, name: options.name, captureId: options.captureId)
        }
        let devices = ScreenCaptureDevices.list(waiting: options.waitS) { choice($0) != .none }
        switch choice(devices) {
        case .use(let id):
            return devices.first { $0.uniqueID == id }!
        case .none:
            throw HelperFailure(
                "no screen of a cabled device called \(options.name) is among this Mac's capture devices: is it plugged in by cable, trusted and unlocked?",
                status: 409
            )
        case .compare(let ids):
            return try nearest(devices.filter { ids.contains($0.uniqueID) })
        }
    }

    /// Of several devices that share a name, the one whose screen looks like the picture taken of it another way.
    private func nearest(_ devices: [AVCaptureDevice]) throws -> AVCaptureDevice {
        guard let reference = options.reference, let wanted = Thumbnail.of(file: reference) else {
            throw HelperFailure("\(devices.count) cabled devices are called \(options.name), and no picture of its screen tells them apart", status: 409)
        }
        let pictures: [[UInt8]?] = devices.map { device in
            guard let capture = try? UsbCapture(device: device, lingerS: 0, log: log) else { return nil }
            defer { capture.close() }
            guard (try? capture.hold()) != nil else { return nil }
            defer { capture.letGo() }
            return (try? capture.picture(waiting: options.waitS)).flatMap { Thumbnail.of($0) }
        }
        guard let index = Likeness.nearest(wanted, among: pictures) else {
            throw HelperFailure("none of the \(devices.count) cabled devices called \(options.name) sent a picture of its screen", status: 409)
        }
        return devices[index]
    }

    /// Whether the device is still plugged in: a device that is not is let go of.
    public var connected: Bool {
        lock.lock()
        defer { lock.unlock() }
        return opened?.capture.device.isConnected ?? true
    }

    public func close() {
        lock.lock()
        let opened = self.opened
        self.opened = nil
        lock.unlock()
        opened?.capture.close()
    }
}

/// A picture shrunk to the few grey pixels `Likeness` compares.
enum Thumbnail {
    private static let context = CIContext(options: [.cacheIntermediates: false])

    static func of(file path: String) -> [UInt8]? {
        guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { return nil }
        return grey(image)
    }

    static func of(_ picture: Picture) -> [UInt8]? {
        withExtendedLifetime(picture) {
            let image = CIImage(ioSurface: picture.surface)
            return context.createCGImage(image, from: image.extent).flatMap(grey)
        }
    }

    private static func grey(_ image: CGImage) -> [UInt8]? {
        let (width, height) = (Likeness.width, Likeness.height)
        var pixels = [UInt8](repeating: 0, count: width * height)
        let drawn = pixels.withUnsafeMutableBytes { bytes -> Bool in
            guard let context = CGContext(
                data: bytes.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
                space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue
            ) else { return false }
            context.interpolationQuality = .medium
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        return drawn ? pixels : nil
    }
}
