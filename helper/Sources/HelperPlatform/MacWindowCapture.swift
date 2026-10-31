// SPDX-License-Identifier: Apache-2.0
import CoreMedia
import CoreVideo
import Foundation
import HelperCore
import IOSurface
import ScreenCaptureKit

/// A desktop-independent window stream. The filter is always one window owned by the session's process.
final class MacWindowCapture: NSObject, PixelSource, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    let window: SCWindow
    let geometry: ScreenGeometry
    private let signal = FrameSignal(label: "sim-mirror.mac.frames")
    private let lock = NSLock()
    private var latest: Picture?
    private var frames: UInt64 = 0
    private var failure: Error?
    private var stream: SCStream?

    init(window: SCWindow, pid: Int32) throws {
        guard window.owningApplication?.processID == pid else { throw HelperFailure("the window belongs to another process", status: 403) }
        self.window = window
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let scale = Double(filter.pointPixelScale)
        geometry = try ScreenGeometry.of(pixelWidth: filter.contentRect.width * scale, pixelHeight: filter.contentRect.height * scale, scale: scale)
        super.init()
        let config = SCStreamConfiguration()
        config.width = geometry.widthPx
        config.height = geometry.heightPx
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.minimumFrameInterval = CMTime(value: 1, timescale: 120)
        config.queueDepth = 3
        config.showsCursor = false
        config.capturesAudio = false
        config.ignoreShadowsSingleWindow = true
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: signal.queue)
        self.stream = stream
    }

    func start() async throws { try await stream?.startCapture() }
    func hold() throws {
        lock.lock(); defer { lock.unlock() }
        if let failure { throw failure }
    }
    func letGo() {}
    func picture() -> Picture? { lock.lock(); defer { lock.unlock() }; return latest }
    func picture(waiting seconds: Double) throws -> Picture {
        try hold()
        return try FrameSignal.waiting(seconds, for: picture, otherwise: HelperFailure("the app window has no frame; restore a minimized window", status: 409))
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sampleBuffer.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let status = attachments.first?[.status] as? Int, status == SCFrameStatus.complete.rawValue,
              let buffer = sampleBuffer.imageBuffer,
              let surface = CVPixelBufferGetIOSurface(buffer)?.takeUnretainedValue() else { return }
        lock.lock()
        frames += 1
        latest = Picture(surface: surface, version: frames, keeping: buffer)
        lock.unlock()
        signal.presented()
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock(); failure = error; latest = nil; lock.unlock()
    }
    func observe(_ onFrame: @escaping @Sendable () -> Void) -> UUID { signal.observe(onFrame) }
    func forget(_ id: UUID) { signal.forget(id) }
    func async(_ body: @escaping @Sendable () -> Void) { signal.async(body) }
    func async(after seconds: Double, _ body: @escaping @Sendable () -> Void) { signal.async(after: seconds, body) }
    func close() {
        signal.clear()
        stream?.stopCapture { _ in }
        lock.lock(); latest = nil; lock.unlock()
    }
}
