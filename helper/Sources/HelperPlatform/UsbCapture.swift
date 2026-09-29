// SPDX-License-Identifier: Apache-2.0
import AVFoundation
import CoreMediaIO
import CoreVideo
import Foundation
import HelperCore
import IOSurface
import VideoToolbox

/// The Mac's capture devices that show a cabled iPhone's or iPad's screen, as QuickTime lists them.
enum ScreenCaptureDevices {
    /// Let cabled devices' screens be listed among the Mac's capture devices: they are not, until a process asks.
    static func allow() {
        var property = CMIOObjectPropertyAddress(
            mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
            mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
            mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
        )
        var allow: UInt32 = 1
        _ = CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &property, 0, nil, UInt32(MemoryLayout<UInt32>.size), &allow)
    }

    /// The screens listed now, waiting up to `seconds` for the first: they appear a few hundred milliseconds after
    /// `allow`.
    static func list(waiting seconds: Double) -> [AVCaptureDevice] {
        allow()
        let deadline = Date().addingTimeInterval(seconds)
        while true {
            let found = AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .muxed, position: .unspecified).devices
            if !found.isEmpty || Date() >= deadline { return found }
            Thread.sleep(forTimeInterval: 0.1)
        }
    }
}

/// macOS's Camera permission, which reading a screen by cable needs, asked of this process for itself (`Disclaim`).
enum CameraPermission {
    /// Ask for it when it has not been asked for, waiting for the answer; refuse when it is not given.
    static func require() throws {
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            let answered = DispatchSemaphore(value: 0)
            AVCaptureDevice.requestAccess(for: .video) { _ in answered.signal() }
            answered.wait()
        }
        guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
            throw HelperFailure(
                "macOS has not let sim-mirror-helper use the Camera, which reading a cabled device's screen needs: allow it in "
                    + "System Settings > Privacy & Security > Camera",
                status: 403
            )
        }
    }
}

/// A cabled device's screen, read through AVFoundation the way QuickTime reads it: each frame the device sends, turned
/// upright into portrait, as a `PixelSource`.
///
/// It runs only while something needs pictures and for a while after (`CaptureDemand`): while it runs, the device's
/// status bar reads 9:41 and its cable carries the picture.
final class UsbCapture: NSObject, PixelSource, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    let device: AVCaptureDevice
    private let signal = FrameSignal(label: "sim-mirror.capture")
    /// Where the capture is started and stopped, one change at a time.
    private let control = DispatchQueue(label: "sim-mirror.capture.control")
    private let session = AVCaptureSession()
    private let log: Log
    private let lock = NSLock()
    private var demand: CaptureDemand
    private var latest: Picture?
    private var frames: UInt64 = 0
    private var turner: Turner?

    init(device: AVCaptureDevice, lingerS: Double, log: Log) throws {
        self.device = device
        self.log = log
        demand = CaptureDemand(lingerS: lingerS)
        super.init()
        let input: AVCaptureDeviceInput
        do {
            input = try AVCaptureDeviceInput(device: device)
        } catch {
            throw HelperFailure("the screen of \(device.localizedName) cannot be read: \(error.localizedDescription)", status: 409)
        }
        let output = AVCaptureVideoDataOutput()
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.alwaysDiscardsLateVideoFrames = true
        output.setSampleBufferDelegate(self, queue: signal.queue)
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        guard session.canAddInput(input), session.canAddOutput(output) else {
            throw HelperFailure("the screen of \(device.localizedName) cannot be read here", status: 409)
        }
        session.addInput(input)
        session.addOutput(output)
    }

    private var now: Double { ProcessInfo.processInfo.systemUptime }

    func hold() throws {
        lock.lock()
        demand.hold(at: now)
        lock.unlock()
        control.sync { settle() }
    }

    func letGo() {
        lock.lock()
        demand.release(at: now)
        let idle = demand.idleAt()
        lock.unlock()
        guard let idle else { return }
        control.asyncAfter(deadline: .now() + max(0, idle - now) + 0.05) { [weak self] in self?.settle() }
    }

    /// Start or stop the capture as demand says; on the control queue.
    private func settle() {
        lock.lock()
        let wanted = demand.running(at: now)
        if !wanted { latest = nil }
        lock.unlock()
        if wanted, !session.isRunning {
            log.debug("capturing the screen of \(device.localizedName)")
            session.startRunning()
        } else if !wanted, session.isRunning {
            log.debug("no longer capturing the screen of \(device.localizedName)")
            session.stopRunning()
        }
    }

    func picture() -> Picture? {
        lock.lock()
        defer { lock.unlock() }
        return latest
    }

    func picture(waiting seconds: Double) throws -> Picture {
        try FrameSignal.waiting(
            seconds, for: picture,
            otherwise: HelperFailure("\(device.localizedName) has sent no picture of its screen in \(Int(seconds)) seconds: is it unlocked?", status: 503)
        )
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let captured = CMSampleBufferGetImageBuffer(sample) else { return }
        let upright: CVPixelBuffer
        switch CaptureTurn.of(width: CVPixelBufferGetWidth(captured), height: CVPixelBufferGetHeight(captured)) {
        case .none:
            upright = captured
        case .quarter:
            guard let turned = turned(captured) else { return }
            upright = turned
        }
        guard let surface = CVPixelBufferGetIOSurface(upright)?.takeUnretainedValue() else { return }
        lock.lock()
        frames += 1
        latest = demand.running(at: now) ? Picture(surface: surface, version: frames, keeping: upright) : nil
        lock.unlock()
        signal.presented()
    }

    /// A frame wider than tall, turned a quarter into portrait; on the source's queue.
    private func turned(_ frame: CVPixelBuffer) -> CVPixelBuffer? {
        let width = CVPixelBufferGetHeight(frame)
        let height = CVPixelBufferGetWidth(frame)
        if turner?.fits(width: width, height: height) != true {
            turner = Turner(width: width, height: height)
            if turner == nil { log.warning("a \(height)x\(width) frame cannot be turned upright") }
        }
        return turner?.turn(frame)
    }

    func observe(_ onFrame: @escaping @Sendable () -> Void) -> UUID { signal.observe(onFrame) }
    func forget(_ id: UUID) { signal.forget(id) }
    func async(_ body: @escaping @Sendable () -> Void) { signal.async(body) }
    func async(after seconds: Double, _ body: @escaping @Sendable () -> Void) { signal.async(after: seconds, body) }

    func close() {
        signal.clear()
        control.sync {
            if session.isRunning { session.stopRunning() }
        }
        lock.lock()
        latest = nil
        lock.unlock()
    }
}

/// Turns frames a quarter, into pixel buffers of its own that screenshots and the encoder read as they read any.
private final class Turner {
    private let session: VTPixelRotationSession
    private let pool: CVPixelBufferPool
    private let width: Int
    private let height: Int

    init?(width: Int, height: Int) {
        var made: VTPixelRotationSession?
        guard VTPixelRotationSessionCreate(nil, &made) == noErr, let made else { return nil }
        VTSessionSetProperty(made, key: kVTPixelRotationPropertyKey_Rotation, value: kVTRotation_CW90)
        let attributes: [CFString: Any] = [
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey: width,
            kCVPixelBufferHeightKey: height,
            kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
        ]
        var created: CVPixelBufferPool?
        guard CVPixelBufferPoolCreate(nil, nil, attributes as CFDictionary, &created) == kCVReturnSuccess, let created else {
            VTPixelRotationSessionInvalidate(made)
            return nil
        }
        session = made
        pool = created
        self.width = width
        self.height = height
    }

    deinit {
        VTPixelRotationSessionInvalidate(session)
    }

    func fits(width: Int, height: Int) -> Bool {
        (self.width, self.height) == (width, height)
    }

    func turn(_ frame: CVPixelBuffer) -> CVPixelBuffer? {
        var buffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let buffer,
            VTPixelRotationSessionRotateImage(session, frame, buffer) == noErr
        else { return nil }
        return buffer
    }
}
