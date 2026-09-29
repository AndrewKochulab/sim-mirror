// SPDX-License-Identifier: Apache-2.0
import AVFoundation
import CoreImage
import CoreMedia
import CoreVideo
import Foundation
import HelperCore
import ImageIO
import UniformTypeIdentifiers
import VideoToolbox

/// Renders a recording as SimMirror asks (`RenderJob`): an MP4 and a GIF, each at a steady rate in its own time (`Ticks`),
/// each frame the recording's picture at that moment with the touches that were landing then drawn over it
/// (`TouchMarks`). The recording is a movie the device's own tool wrote, or SimMirror's frame file of H.264 access units
/// or JPEGs -- all read forward once for each file written.
public enum RecordingRenderer {
    /// Frames a second in a rendered MP4.
    public static let movieFps = 30

    public static func run(_ job: RenderJob) async throws -> RenderReport {
        var files: [RenderedFile] = []
        if let mp4 = job.mp4 {
            var source = try await pictures(job)
            files.append(try await MovieWriter.write(&source, job: job, to: URL(fileURLWithPath: mp4)))
        }
        if let gif = job.gif {
            var source = try await pictures(job)
            files.append(try GIFWriter.write(&source, job: job, to: URL(fileURLWithPath: gif)))
        }
        return RenderReport(files: files)
    }

    static func pictures(_ job: RenderJob) async throws -> any PictureSource {
        let url = URL(fileURLWithPath: job.input)
        switch job.inputKind {
        case .movie: return try await MoviePictures(url: url)
        case .frames: return try FramePictures(url: url)
        }
    }
}

/// A recording's pictures, read forward: the one shown at each moment asked for, which only ever moves on.
protocol PictureSource {
    var width: Int { get }
    var height: Int { get }
    /// How long the recording runs, in seconds.
    var duration: Double { get }
    mutating func picture(at time: Double) throws -> CIImage?
}

/// Moves a source's pictures on to a moment: keeps the last one shown by then, reading ahead one picture.
struct Playhead {
    private(set) var shown: CIImage?
    private var ahead: (time: Double, image: CIImage)?
    private var ended = false

    mutating func advance(to time: Double, next: () throws -> (time: Double, image: CIImage)?) rethrows -> CIImage? {
        while !ended {
            if ahead == nil {
                ahead = try next()
                if ahead == nil { ended = true; break }
            }
            guard let upcoming = ahead, upcoming.time <= time || shown == nil else { break }
            shown = upcoming.image
            ahead = nil
        }
        return shown
    }
}

/// A movie's pictures, through AVAssetReader.
struct MoviePictures: PictureSource {
    let width: Int
    let height: Int
    let duration: Double
    private let reader: AVAssetReader
    private let output: AVAssetReaderTrackOutput
    private let transform: CGAffineTransform
    private var playhead = Playhead()

    init(url: URL) async throws {
        let asset = AVURLAsset(url: url)
        guard let track = try await asset.loadTracks(withMediaType: .video).first else {
            throw HelperFailure("the recording has no picture")
        }
        let (natural, transform) = try await track.load(.naturalSize, .preferredTransform)
        let upright = CGRect(origin: .zero, size: natural).applying(transform)
        width = Int(abs(upright.width))
        height = Int(abs(upright.height))
        duration = try await asset.load(.duration).seconds
        self.transform = transform
        reader = try AVAssetReader(asset: asset)
        output = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
        output.alwaysCopiesSampleData = false
        reader.add(output)
        guard reader.startReading() else { throw HelperFailure("the recording cannot be read: \(reader.error?.localizedDescription ?? "no reason")") }
    }

    mutating func picture(at time: Double) throws -> CIImage? {
        let (output, transform) = (self.output, self.transform)
        return playhead.advance(to: time) {
            guard let sample = output.copyNextSampleBuffer(), let buffer = CMSampleBufferGetImageBuffer(sample) else { return nil }
            let image = CIImage(cvPixelBuffer: buffer).transformed(by: transform)
            let moved = image.transformed(by: CGAffineTransform(translationX: -image.extent.minX, y: -image.extent.minY))
            return (CMSampleBufferGetPresentationTimeStamp(sample).seconds, moved)
        }
    }
}

/// SimMirror's frame file's pictures: H.264 decoded by VideoToolbox, JPEGs by ImageIO.
struct FramePictures: PictureSource {
    let width: Int
    let height: Int
    let duration: Double
    private var reader: FrameFile.Reader
    private let decoder = H264Decoder()
    private var playhead = Playhead()

    init(url: URL) throws {
        var scan = try FramePictures.open(url)
        var last = 0.0
        var first: CIImage?
        let probe = H264Decoder()
        while let frame = try scan.next() {
            last = frame.time
            if first == nil { first = try FramePictures.decode(frame, with: probe) }
        }
        guard let first else { throw HelperFailure("the recording took no pictures") }
        width = Int(first.extent.width)
        height = Int(first.extent.height)
        duration = last
        reader = try FramePictures.open(url)
    }

    static func open(_ url: URL) throws -> FrameFile.Reader {
        let handle = try FileHandle(forReadingFrom: url)
        return try FrameFile.Reader { count in try handle.read(upToCount: count) ?? Data() }
    }

    static func decode(_ frame: FrameFile.Frame, with decoder: H264Decoder) throws -> CIImage? {
        switch frame.kind {
        case .jpeg:
            guard let source = CGImageSourceCreateWithData(frame.data as CFData, nil),
                  let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw HelperFailure("a recorded JPEG cannot be read") }
            return CIImage(cgImage: image)
        case .h264:
            return try decoder.decode(frame.data).map { CIImage(cvPixelBuffer: $0) }
        }
    }

    mutating func picture(at time: Double) throws -> CIImage? {
        var reader = self.reader
        let decoder = self.decoder
        defer { self.reader = reader }
        return try playhead.advance(to: time) {
            while let frame = try reader.next() {
                if let image = try FramePictures.decode(frame, with: decoder) { return (frame.time, image) }
            }
            return nil
        }
    }
}

/// H.264 access units decoded into pixels, a session made anew whenever the parameter sets change.
final class H264Decoder {
    private var session: VTDecompressionSession?
    private var format: CMVideoFormatDescription?
    private var parameterSets: [Data] = []

    deinit {
        if let session { VTDecompressionSessionInvalidate(session) }
    }

    /// The picture an access unit makes, or nil for a unit that makes none -- one before the first key frame.
    func decode(_ unit: Data) throws -> CVPixelBuffer? {
        let nals = AnnexB.nalUnits(unit)
        let sets = nals.filter { [7, 8].contains(AnnexB.type($0)) }
        if sets.count >= 2, sets != parameterSets { try restart(sets) }
        let slices = nals.filter { (1...5).contains(AnnexB.type($0)) }
        guard let session, let format, !slices.isEmpty else { return nil }
        var avcc = Data()
        for nal in slices {
            var length = UInt32(nal.count).bigEndian
            avcc.append(Data(bytes: &length, count: 4))
            avcc.append(nal)
        }
        var block: CMBlockBuffer?
        let bytes = avcc.count
        guard CMBlockBufferCreateWithMemoryBlock(allocator: nil, memoryBlock: nil, blockLength: bytes, blockAllocator: nil,
                                                 customBlockSource: nil, offsetToData: 0, dataLength: bytes, flags: 0, blockBufferOut: &block) == noErr,
              let block,
              avcc.withUnsafeBytes({ CMBlockBufferReplaceDataBytes(with: $0.baseAddress!, blockBuffer: block, offsetIntoDestination: 0, dataLength: bytes) }) == noErr
        else { throw HelperFailure("a recorded frame cannot be read") }
        var sample: CMSampleBuffer?
        var size = bytes
        guard CMSampleBufferCreateReady(allocator: nil, dataBuffer: block, formatDescription: format, sampleCount: 1, sampleTimingEntryCount: 0,
                                        sampleTimingArray: nil, sampleSizeEntryCount: 1, sampleSizeArray: &size, sampleBufferOut: &sample) == noErr,
              let sample else { throw HelperFailure("a recorded frame cannot be read") }
        var decoded: CVPixelBuffer?
        let status = VTDecompressionSessionDecodeFrame(session, sampleBuffer: sample, flags: [], infoFlagsOut: nil) { status, _, buffer, _, _ in
            if status == noErr { decoded = buffer }
        }
        VTDecompressionSessionWaitForAsynchronousFrames(session)
        guard status == noErr else { throw HelperFailure("a recorded frame cannot be decoded (\(status))") }
        return decoded
    }

    private func restart(_ sets: [Data]) throws {
        if let session { VTDecompressionSessionInvalidate(session) }
        session = nil
        let pointers = sets.map { [UInt8]($0) }
        var made: CMVideoFormatDescription?
        let status = pointers[0].withUnsafeBufferPointer { sps in
            pointers[1].withUnsafeBufferPointer { pps in
                let sets = [sps.baseAddress!, pps.baseAddress!]
                let sizes = [sps.count, pps.count]
                return CMVideoFormatDescriptionCreateFromH264ParameterSets(allocator: nil, parameterSetCount: 2, parameterSetPointers: sets,
                                                                           parameterSetSizes: sizes, nalUnitHeaderLength: 4, formatDescriptionOut: &made)
            }
        }
        guard status == noErr, let made else { throw HelperFailure("a recording's parameter sets cannot be read (\(status))") }
        var created: VTDecompressionSession?
        let attributes = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA] as CFDictionary
        guard VTDecompressionSessionCreate(allocator: nil, formatDescription: made, decoderSpecification: nil,
                                           imageBufferAttributes: attributes, outputCallback: nil, decompressionSessionOut: &created) == noErr,
              let created else { throw HelperFailure("this Mac cannot decode the recording") }
        format = made
        session = created
        parameterSets = sets
    }
}

/// Touch marks drawn over a picture: rings where fingers are, and the paths moving ones took -- an agent's orange, a
/// person's blue, both edged in white so they show on any screen.
enum TouchPainter {
    static let space = CGColorSpace(name: CGColorSpace.sRGB)!

    static func paint(_ marks: [TouchMark], width: Int, height: Int) -> CIImage? {
        guard !marks.isEmpty,
              let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: space, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let w = CGFloat(width), h = CGFloat(height)
        func at(_ x: Double, _ y: Double) -> CGPoint { CGPoint(x: CGFloat(x) * w, y: (1 - CGFloat(y)) * h) }
        for mark in marks {
            let color = mark.byAgent ? CGColor(srgbRed: 1, green: 0.54, blue: 0.1, alpha: 1) : CGColor(srgbRed: 0.04, green: 0.52, blue: 1, alpha: 1)
            context.setAlpha(CGFloat(mark.alpha))
            switch mark.shape {
            case .trail(let points):
                guard let first = points.first else { continue }
                context.setLineCap(.round)
                context.setLineJoin(.round)
                context.setLineWidth(w * 0.024)
                context.setStrokeColor(color.copy(alpha: 0.7)!)
                context.move(to: at(first[0], first[1]))
                for point in points.dropFirst() { context.addLine(to: at(point[0], point[1])) }
                context.strokePath()
            case .ring(let x, let y, let radius):
                let r = CGFloat(radius) * w
                let rect = CGRect(x: at(x, y).x - r, y: at(x, y).y - r, width: r * 2, height: r * 2)
                context.setFillColor(color.copy(alpha: 0.5)!)
                context.fillEllipse(in: rect)
                context.setLineWidth(max(3, w * 0.009))
                context.setStrokeColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 0.95))
                context.strokeEllipse(in: rect)
            }
        }
        return context.makeImage().map { CIImage(cgImage: $0) }
    }
}

/// One frame of a rendered file: the recording's picture then, scaled to the file's size, with its touches over it.
enum Frames {
    static func compose(_ picture: CIImage, touches: [RecordedTouch], at time: Double, width: Int, height: Int) -> CIImage {
        let extent = picture.extent
        let scaled = extent.width > 0 && extent.height > 0
            ? picture.transformed(by: CGAffineTransform(scaleX: CGFloat(width) / extent.width, y: CGFloat(height) / extent.height))
            : picture
        let marks = TouchMarks.at(time, touches)
        guard let overlay = TouchPainter.paint(marks, width: width, height: height) else { return scaled }
        return overlay.composited(over: scaled)
    }
}

/// An MP4 at the recording's own size, H.264 or HEVC, through AVAssetWriter.
enum MovieWriter {
    static func write(_ source: inout any PictureSource, job: RenderJob, to url: URL) async throws -> RenderedFile {
        let (width, height) = renderedSize(sourceWidth: source.width, sourceHeight: source.height, width: nil)
        try? FileManager.default.removeItem(at: url)
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let bitrate = max(1_000_000, width * height * 4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: job.codec == "hevc" ? AVVideoCodecType.hevc : AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: bitrate],
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: width,
            kCVPixelBufferHeightKey as String: height,
        ])
        writer.add(input)
        guard writer.startWriting() else { throw HelperFailure("the MP4 cannot be written: \(writer.error?.localizedDescription ?? "no reason")") }
        writer.startSession(atSourceTime: .zero)
        let context = CIContext(options: [.cacheIntermediates: false])
        let ticks = Ticks.make(duration: source.duration, speed: job.speed, fps: RecordingRenderer.movieFps)
        for (index, tick) in ticks.times.enumerated() {
            guard let picture = try source.picture(at: tick.source) else { continue }
            while !input.isReadyForMoreMediaData { try await Task.sleep(nanoseconds: 2_000_000) }
            guard let pool = adaptor.pixelBufferPool else { throw HelperFailure("the MP4 has no pixel buffers") }
            var buffer: CVPixelBuffer?
            guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let buffer else {
                throw HelperFailure("the MP4 has no pixel buffer free")
            }
            context.render(Frames.compose(picture, touches: job.touches, at: tick.source, width: width, height: height), to: buffer)
            guard adaptor.append(buffer, withPresentationTime: CMTime(value: CMTimeValue(index), timescale: CMTimeScale(ticks.fps))) else {
                throw HelperFailure("the MP4 stopped taking frames: \(writer.error?.localizedDescription ?? "no reason")")
            }
        }
        input.markAsFinished()
        await writer.finishWriting()
        guard writer.status == .completed else { throw HelperFailure("the MP4 was not finished: \(writer.error?.localizedDescription ?? "no reason")") }
        return RenderedFile(path: url.path, format: "mp4", width: width, height: height, durationS: ticks.length)
    }
}

/// An animated GIF at its own width and rate, through ImageIO; a frame the same as the one before only lengthens it.
enum GIFWriter {
    static func write(_ source: inout any PictureSource, job: RenderJob, to url: URL) throws -> RenderedFile {
        let (width, height) = renderedSize(sourceWidth: source.width, sourceHeight: source.height, width: job.gifWidth)
        let ticks = Ticks.make(duration: source.duration, speed: job.speed, fps: job.gifFps)
        guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.gif.identifier as CFString, ticks.times.count, nil) else {
            throw HelperFailure("ImageIO cannot write a GIF here")
        }
        CGImageDestinationSetProperties(destination, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary)
        let context = CIContext(options: [.cacheIntermediates: false])
        let rect = CGRect(x: 0, y: 0, width: width, height: height)
        let step = 1 / Double(ticks.fps)
        var pending: (image: CGImage, bytes: Data, delay: Double)?
        func flush() {
            guard let pending else { return }
            let delay = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: pending.delay, kCGImagePropertyGIFUnclampedDelayTime: pending.delay]]
            CGImageDestinationAddImage(destination, pending.image, delay as CFDictionary)
        }
        for tick in ticks.times {
            guard let picture = try source.picture(at: tick.source),
                  let image = context.createCGImage(Frames.compose(picture, touches: job.touches, at: tick.source, width: width, height: height),
                                                    from: rect, format: .RGBA8, colorSpace: TouchPainter.space) else { continue }
            let bytes = image.dataProvider?.data.map { $0 as Data } ?? Data()
            if let held = pending, held.bytes == bytes {
                pending = (held.image, held.bytes, held.delay + step)
                continue
            }
            flush()
            pending = (image, bytes, step)
        }
        flush()
        guard CGImageDestinationFinalize(destination) else { throw HelperFailure("the GIF could not be written") }
        return RenderedFile(path: url.path, format: "gif", width: width, height: height, durationS: ticks.length)
    }
}
