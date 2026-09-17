// SPDX-License-Identifier: Apache-2.0
import CoreMedia
import CoreVideo
import Foundation
import HelperCore
import IOSurface
import VideoToolbox

/// A device's screen as H.264, encoded the moment the simulator presents a change.
///
/// The framebuffer says when a frame is presented; `FrameScheduler` says whether to encode it now, later or not at all;
/// VideoToolbox encodes it on the Mac's hardware encoder in its low-latency mode, with no frame reordering, and each
/// frame comes out as one Annex-B access unit (`AnnexB`).
final class H264Stream: @unchecked Sendable {
    /// Frames VideoToolbox may hold at once before a new one waits: past this the encoder is behind, and the latest
    /// picture is encoded when it catches up.
    static let maxInFlight = 2

    private let framebuffer: Framebuffer
    private let pictures: Pictures
    private let plan: EncoderPlan
    private let log: Log
    private let lock = NSLock()
    private var scheduler: FrameScheduler
    private var session: VTCompressionSession?
    private var observer: UUID?
    private var lastSeed: UInt32?
    private var inFlight = 0
    private var timer: DispatchSourceTimer?
    private var waitingUntil: Double?
    private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
    private let started = DispatchTime.now().uptimeNanoseconds

    init(framebuffer: Framebuffer, pictures: Pictures, settings: StreamSettings, idleKeyFrames: Bool, log: Log) throws {
        let surface = try framebuffer.surface(waiting: 2)
        plan = try EncoderPlan.make(settings, surfaceWidth: IOSurfaceGetWidth(surface), surfaceHeight: IOSurfaceGetHeight(surface))
        scheduler = FrameScheduler(fps: plan.fps, keyFrameInterval: plan.keyFrameInterval, idleKeyFrames: idleKeyFrames)
        self.framebuffer = framebuffer
        self.pictures = pictures
        self.log = log
        session = try Self.session(plan)
        log.debug("a \(plan.width)x\(plan.height) H.264 stream's encoder is ready after \(Self.milliseconds(since: started))ms")
    }

    static func milliseconds(since start: UInt64) -> Int {
        Int((DispatchTime.now().uptimeNanoseconds - start) / 1_000_000)
    }

    /// The access units, from a key frame, until the stream is let go of.
    func units() -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream(bufferingPolicy: .unbounded) { continuation in
            lock.lock()
            self.continuation = continuation
            lock.unlock()
            continuation.onTermination = { [weak self] _ in self?.stop() }
            observer = framebuffer.observe { [weak self] in self?.presented() }
            let timer = DispatchSource.makeTimerSource(queue: nil)
            timer.schedule(deadline: .now(), repeating: min(plan.keyFrameInterval / 2, 0.25))
            let framebuffer = self.framebuffer
            timer.setEventHandler { framebuffer.async { [weak self] in self?.decide(changed: false) } }
            self.timer = timer
            timer.resume()
            framebuffer.async { [weak self] in self?.decide(changed: true) }
        }
    }

    private var now: Double {
        Double(DispatchTime.now().uptimeNanoseconds - started) / 1e9
    }

    /// Called on the framebuffer's queue for every presented frame; a frame whose pixels have not changed is not one.
    private func presented() {
        guard let surface = framebuffer.surface() else { return }
        let seed = IOSurfaceGetSeed(surface)
        lock.lock()
        let changed = seed != lastSeed
        lastSeed = seed
        lock.unlock()
        if changed { decide(changed: true) }
    }

    private func decide(changed: Bool) {
        lock.lock()
        guard session != nil else {
            lock.unlock()
            return
        }
        let at = now
        let decision = changed ? scheduler.frameChanged(at: at) : scheduler.tick(at: at)
        switch decision {
        case .idle:
            lock.unlock()
        case .wait(let until):
            let schedule = waitingUntil == nil
            waitingUntil = until
            lock.unlock()
            if schedule {
                framebuffer.async(after: max(0, until - at)) { [weak self] in
                    self?.lock.lock()
                    self?.waitingUntil = nil
                    self?.lock.unlock()
                    self?.decide(changed: false)
                }
            }
        case .encode(let key):
            guard inFlight < Self.maxInFlight else {
                lock.unlock()
                return
            }
            inFlight += 1
            scheduler.encoded(at: at, key: key)
            let session = self.session
            lock.unlock()
            if let session { encode(on: session, key: key, at: at) }
        }
    }

    private func encode(on session: VTCompressionSession, key: Bool, at: Double) {
        guard let surface = framebuffer.surface(), let pool = VTCompressionSessionGetPixelBufferPool(session) else {
            return finished(failed: nil)
        }
        var buffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let buffer else {
            return finished(failed: nil)
        }
        lock.lock()
        lastSeed = IOSurfaceGetSeed(surface)
        lock.unlock()
        do {
            try pictures.draw(surface, into: buffer)
        } catch {
            return finished(failed: error)
        }
        if key { log.debug("a key frame is drawn after \(Self.milliseconds(since: started))ms") }
        let options = key ? [kVTEncodeFrameOptionKey_ForceKeyFrame: kCFBooleanTrue!] as CFDictionary : nil
        let status = VTCompressionSessionEncodeFrame(
            session, imageBuffer: buffer, presentationTimeStamp: CMTime(seconds: at, preferredTimescale: 1_000_000),
            duration: .invalid, frameProperties: options, infoFlagsOut: nil
        ) { [weak self] status, flags, sample in
            guard let self else { return }
            guard status == noErr else {
                return self.finished(failed: HelperFailure("the H.264 encoder failed (\(status))"))
            }
            guard let sample, !flags.contains(.frameDropped) else { return self.finished(failed: nil) }
            do {
                if key { self.log.debug("a key frame is encoded after \(Self.milliseconds(since: self.started))ms") }
                self.emit(try Self.accessUnit(sample))
                self.finished(failed: nil)
            } catch {
                self.finished(failed: error)
            }
        }
        if status != noErr { finished(failed: HelperFailure("the H.264 encoder refused a frame (\(status))")) }
    }

    private func emit(_ unit: Data) {
        lock.lock()
        let continuation = self.continuation
        lock.unlock()
        continuation?.yield(unit)
    }

    private func finished(failed: Error?) {
        lock.lock()
        inFlight -= 1
        let continuation = self.continuation
        lock.unlock()
        if let failed {
            log.error("\(failed)")
            continuation?.finish(throwing: failed)
        } else {
            // A change that arrived while the encoder was full is encoded as soon as it has room.
            framebuffer.async { [weak self] in self?.decide(changed: false) }
        }
    }

    func stop() {
        lock.lock()
        let session = self.session
        self.session = nil
        let observer = self.observer
        self.observer = nil
        let timer = self.timer
        self.timer = nil
        continuation = nil
        lock.unlock()
        observer.map(framebuffer.forget)
        timer?.cancel()
        if let session {
            VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid)
            VTCompressionSessionInvalidate(session)
        }
    }

    /// Encode one small frame and let the encoder go, so the first stream does not wait while VideoToolbox loads it.
    ///
    /// Measured on macOS 26.6 (2026-09-17): the first low-latency encoder a process makes takes 0.5 to 0.8 seconds, and
    /// every one after it a millisecond or two.
    static func warm(framebuffer: Framebuffer, pictures: Pictures) {
        guard let surface = framebuffer.surface(),
            let plan = try? EncoderPlan.make(
                StreamSettings(fps: 30, scale: 0.25, keyFrameS: 1, bitrate: EncoderPlan.minimumBitrate),
                surfaceWidth: IOSurfaceGetWidth(surface), surfaceHeight: IOSurfaceGetHeight(surface)
            ),
            let session = try? session(plan)
        else { return }
        defer { VTCompressionSessionInvalidate(session) }
        var buffer: CVPixelBuffer?
        guard let pool = VTCompressionSessionGetPixelBufferPool(session),
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let buffer
        else { return }
        guard (try? pictures.draw(surface, into: buffer)) != nil else { return }
        _ = VTCompressionSessionEncodeFrame(
            session, imageBuffer: buffer, presentationTimeStamp: .zero, duration: .invalid, frameProperties: nil,
            infoFlagsOut: nil
        ) { _, _, _ in }
        VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid)
    }

    static func session(_ plan: EncoderPlan) throws -> VTCompressionSession {
        let source: [CFString: Any] = [
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey: plan.width,
            kCVPixelBufferHeightKey: plan.height,
            kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
        ]
        var created: VTCompressionSession?
        for specification in [[kVTVideoEncoderSpecification_EnableLowLatencyRateControl: kCFBooleanTrue!] as CFDictionary, nil] {
            let status = VTCompressionSessionCreate(
                allocator: nil, width: Int32(plan.width), height: Int32(plan.height), codecType: kCMVideoCodecType_H264,
                encoderSpecification: specification, imageBufferAttributes: source as CFDictionary,
                compressedDataAllocator: nil, outputCallback: nil, refcon: nil, compressionSessionOut: &created
            )
            if status == noErr, created != nil { break }
            created = nil
        }
        guard let session = created else { throw HelperFailure("this Mac has no H.264 encoder for \(plan.width)x\(plan.height)") }
        let properties: [(CFString, CFTypeRef)] = [
            (kVTCompressionPropertyKey_RealTime, kCFBooleanTrue),
            (kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse),
            (kVTCompressionPropertyKey_ProfileLevel, kVTProfileLevel_H264_High_AutoLevel),
            (kVTCompressionPropertyKey_ExpectedFrameRate, plan.fps as CFNumber),
            (kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, plan.keyFrameInterval as CFNumber),
            (kVTCompressionPropertyKey_AverageBitRate, plan.bitrate as CFNumber),
            (kVTCompressionPropertyKey_DataRateLimits, [plan.bytesPerSecondLimit, 1] as CFArray),
        ]
        for (key, value) in properties {
            // An encoder that does not take a property still encodes; the low-latency one sets its own rate limits.
            _ = VTSessionSetProperty(session, key: key, value: value)
        }
        VTCompressionSessionPrepareToEncodeFrames(session)
        return session
    }

    /// An encoded sample as an Annex-B access unit, with its parameter sets when it is a key frame.
    static func accessUnit(_ sample: CMSampleBuffer) throws -> Data {
        guard let block = CMSampleBufferGetDataBuffer(sample) else { throw HelperFailure("an encoded frame without data") }
        var length = 0
        var pointer: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &pointer) == noErr,
            let pointer
        else { throw HelperFailure("an encoded frame whose data cannot be read") }
        let avcc = Data(bytes: pointer, count: length)
        var lengthSize: Int32 = 4
        var sets: [Data] = []
        if isKeyFrame(sample), let format = CMSampleBufferGetFormatDescription(sample) {
            var count = 0
            _ = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: 0, parameterSetPointerOut: nil, parameterSetSizeOut: nil, parameterSetCountOut: &count, nalUnitHeaderLengthOut: &lengthSize)
            for index in 0..<count {
                var set: UnsafePointer<UInt8>?
                var size = 0
                if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: index, parameterSetPointerOut: &set, parameterSetSizeOut: &size, parameterSetCountOut: nil, nalUnitHeaderLengthOut: nil) == noErr, let set {
                    sets.append(Data(bytes: set, count: size))
                }
            }
        } else if let format = CMSampleBufferGetFormatDescription(sample) {
            _ = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: 0, parameterSetPointerOut: nil, parameterSetSizeOut: nil, parameterSetCountOut: nil, nalUnitHeaderLengthOut: &lengthSize)
        }
        return try AnnexB.accessUnit(avcc: avcc, lengthSize: Int(lengthSize), parameterSets: sets)
    }

    static func isKeyFrame(_ sample: CMSampleBuffer) -> Bool {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[CFString: Any]],
            let first = attachments.first
        else { return true }
        return (first[kCMSampleAttachmentKey_NotSync] as? Bool) != true
    }
}
