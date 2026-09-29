// SPDX-License-Identifier: Apache-2.0
import Foundation
import HelperCore

/// A device's screen served from a pixel source: screenshots, the H.264 stream, and both made ready ahead of the first.
///
/// Every source is read the same way, so a simulator and a cabled device share this; only how long the first picture
/// may take differs -- a cabled device's first capture switches its cable into another mode, which takes seconds.
final class ScreenFeed: @unchecked Sendable {
    let source: PixelSource
    private let pictures: Pictures
    private let idleKeyFrames: Bool
    private let firstPictureS: Double
    private let log: Log

    init(source: PixelSource, pictures: Pictures, idleKeyFrames: Bool, firstPictureS: Double, log: Log) {
        self.source = source
        self.pictures = pictures
        self.idleKeyFrames = idleKeyFrames
        self.firstPictureS = firstPictureS
        self.log = log
    }

    func screenshot(_ request: ScreenshotRequest, screen: ScreenGeometry) throws -> JPEG {
        try source.hold()
        defer { source.letGo() }
        let picture = try source.picture(waiting: firstPictureS)
        let plan = try ScreenshotPlan.make(request, surfaceWidth: picture.width, surfaceHeight: picture.height, screen: screen)
        return try withExtendedLifetime(picture) { try pictures.jpeg(picture.surface, plan: plan) }
    }

    func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> {
        try H264Stream(
            source: source, pictures: pictures, settings: settings, idleKeyFrames: idleKeyFrames, firstPictureS: firstPictureS,
            log: log
        ).units()
    }

    /// Open what the first screenshot and stream need -- a picture, the JPEG pipeline and the H.264 encoder -- so
    /// neither waits for them; what stops the first picture is thrown, and the request that needs it says so again.
    func warm(screen: ScreenGeometry, waiting seconds: Double) throws {
        let started = DispatchTime.now().uptimeNanoseconds
        try source.hold()
        defer { source.letGo() }
        let picture = try source.picture(waiting: seconds)
        let pictures = self.pictures
        // The JPEG pipeline and the encoder share nothing, so they warm at once.
        DispatchQueue.concurrentPerform(iterations: 2) { part in
            if part == 1 {
                H264Stream.warm(picture, pictures: pictures)
                return
            }
            let request = ScreenshotRequest(maxWidth: 160, quality: 40)
            if let plan = try? ScreenshotPlan.make(request, surfaceWidth: picture.width, surfaceHeight: picture.height, screen: screen) {
                _ = try? pictures.jpeg(picture.surface, plan: plan)
            }
        }
        log.debug("warmed the screen, the JPEG pipeline and the H.264 encoder in \(H264Stream.milliseconds(since: started))ms")
    }
}
