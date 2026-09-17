// SPDX-License-Identifier: Apache-2.0
import Foundation

/// A device's screen: its size in pixels and in points, and the scale between them. Portrait.
public struct ScreenGeometry: Equatable, Sendable, Encodable {
    public let widthPx: Int
    public let heightPx: Int
    public let widthPt: Int
    public let heightPt: Int
    public let scale: Double

    public init(widthPx: Int, heightPx: Int, widthPt: Int, heightPt: Int, scale: Double) {
        self.widthPx = widthPx
        self.heightPx = heightPx
        self.widthPt = widthPt
        self.heightPt = heightPt
        self.scale = scale
    }

    /// The screen of a device type that says its size in pixels and its scale. Refuses a size or scale that is none.
    public static func of(pixelWidth: Double, pixelHeight: Double, scale: Double) throws -> ScreenGeometry {
        guard pixelWidth.isFinite, pixelHeight.isFinite, scale.isFinite, pixelWidth >= 1, pixelHeight >= 1, scale > 0
        else {
            throw HelperFailure("the device reports no screen size (\(pixelWidth)x\(pixelHeight) at \(scale)x)")
        }
        let width = Int(pixelWidth.rounded())
        let height = Int(pixelHeight.rounded())
        return ScreenGeometry(
            widthPx: width,
            heightPx: height,
            widthPt: Int((pixelWidth / scale).rounded()),
            heightPt: Int((pixelHeight / scale).rounded()),
            scale: scale
        )
    }

    enum CodingKeys: String, CodingKey {
        case widthPx = "width_px", heightPx = "height_px", widthPt = "width_pt", heightPt = "height_pt", scale
    }
}

/// Which of a device's framebuffers is its screen.
///
/// A simulator shows several: the screen, and planes such as a presentation surface Device Hub adds while it resizes a
/// window (7680x4320 on Xcode 27). The screen is the one the device type's size says, turned either way; when none is,
/// the largest that has a picture.
public enum SurfaceChoice {
    public static func pick(_ sizes: [(width: Int, height: Int)], screen: ScreenGeometry) -> Int? {
        let live = sizes.indices.filter { sizes[$0].width > 0 && sizes[$0].height > 0 }
        if let exact = live.first(where: {
            (sizes[$0].width, sizes[$0].height) == (screen.widthPx, screen.heightPx)
                || (sizes[$0].width, sizes[$0].height) == (screen.heightPx, screen.widthPx)
        }) {
            return exact
        }
        return live.max { sizes[$0].width * sizes[$0].height < sizes[$1].width * sizes[$1].height }
    }
}

/// A region of the screen, in points.
public struct Crop: Equatable, Sendable, Codable {
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

/// A rectangle of whole pixels, measured from the top left.
public struct PixelRect: Equatable, Sendable {
    public let x: Int
    public let y: Int
    public let width: Int
    public let height: Int

    public init(x: Int, y: Int, width: Int, height: Int) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

/// What a screenshot asks for: no wider than `maxWidth`, at a JPEG quality of 1 to 100, of all or part of the screen.
public struct ScreenshotRequest: Equatable, Sendable {
    public let maxWidth: Int
    public let quality: Int
    public let crop: Crop?

    public init(maxWidth: Int, quality: Int, crop: Crop? = nil) {
        self.maxWidth = maxWidth
        self.quality = quality
        self.crop = crop
    }
}

/// How a screenshot is cut from a framebuffer: which pixels, the size it comes out at, and its JPEG quality.
public struct ScreenshotPlan: Equatable, Sendable {
    public let source: PixelRect
    public let width: Int
    public let height: Int
    /// From 0 to 1, as ImageIO takes it.
    public let quality: Double

    /// The plan for a request on a framebuffer of `surfaceWidth` by `surfaceHeight` pixels showing `screen`.
    ///
    /// The framebuffer's own size decides how many pixels a point is, not the device type's scale: the two agree on
    /// every device measured, but the pixels are what is cut.
    public static func make(
        _ request: ScreenshotRequest, surfaceWidth: Int, surfaceHeight: Int, screen: ScreenGeometry
    ) throws -> ScreenshotPlan {
        guard surfaceWidth > 0, surfaceHeight > 0 else { throw HelperFailure("the screen has no picture yet", status: 503) }
        guard request.maxWidth > 0 else { throw HelperFailure("max_width must be at least 1", status: 400) }
        guard (1...100).contains(request.quality) else { throw HelperFailure("quality must be 1 to 100", status: 400) }
        let source = try cut(request.crop, surfaceWidth: surfaceWidth, surfaceHeight: surfaceHeight, screen: screen)
        let width = min(source.width, request.maxWidth)
        let height = max(1, Int((Double(source.height) * Double(width) / Double(source.width)).rounded()))
        return ScreenshotPlan(source: source, width: width, height: height, quality: Double(request.quality) / 100)
    }

    private static func cut(
        _ crop: Crop?, surfaceWidth: Int, surfaceHeight: Int, screen: ScreenGeometry
    ) throws -> PixelRect {
        guard let crop else { return PixelRect(x: 0, y: 0, width: surfaceWidth, height: surfaceHeight) }
        let perPoint = Double(surfaceWidth) / Double(max(screen.widthPt, 1))
        let left = max(0, Int((crop.x * perPoint).rounded(.down)))
        let top = max(0, Int((crop.y * perPoint).rounded(.down)))
        let right = min(surfaceWidth, Int(((crop.x + crop.width) * perPoint).rounded(.up)))
        let bottom = min(surfaceHeight, Int(((crop.y + crop.height) * perPoint).rounded(.up)))
        guard right > left, bottom > top else {
            throw HelperFailure("the region \(crop.x),\(crop.y) \(crop.width)x\(crop.height) is not on the screen", status: 400)
        }
        return PixelRect(x: left, y: top, width: right - left, height: bottom - top)
    }
}
