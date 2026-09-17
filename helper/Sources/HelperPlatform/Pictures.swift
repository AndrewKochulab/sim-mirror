// SPDX-License-Identifier: Apache-2.0
import CoreImage
import CoreVideo
import Foundation
import HelperCore
import ImageIO
import IOSurface
import UniformTypeIdentifiers

/// Pictures of a framebuffer: a JPEG cut and scaled to a plan, and a pixel buffer scaled for an encoder.
///
/// Core Image draws on the GPU straight from the IOSurface, so a screenshot is one render and one JPEG encode with no
/// copy of the full screen in between.
final class Pictures: @unchecked Sendable {
    private let context = CIContext(options: [.cacheIntermediates: false, .name: "sim-mirror"])
    private let space = CGColorSpace(name: CGColorSpace.sRGB)!

    func jpeg(_ surface: IOSurface, plan: ScreenshotPlan) throws -> JPEG {
        let image = scaled(cut(CIImage(ioSurface: surface), plan.source, surfaceHeight: IOSurfaceGetHeight(surface)), plan.width, plan.height)
        guard let cgImage = context.createCGImage(image, from: CGRect(x: 0, y: 0, width: plan.width, height: plan.height), format: .RGBA8, colorSpace: space) else {
            throw HelperFailure("the screen could not be drawn")
        }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else {
            throw HelperFailure("ImageIO cannot write JPEG here")
        }
        CGImageDestinationAddImage(destination, cgImage, [kCGImageDestinationLossyCompressionQuality: plan.quality] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw HelperFailure("the screen could not be written as JPEG") }
        return JPEG(data: data as Data, width: plan.width, height: plan.height)
    }

    /// The whole surface drawn into `buffer` at the buffer's size.
    func draw(_ surface: IOSurface, into buffer: CVPixelBuffer) {
        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        context.render(scaled(CIImage(ioSurface: surface), width, height), to: buffer, bounds: CGRect(x: 0, y: 0, width: width, height: height), colorSpace: space)
    }

    /// The pixels a rectangle measured from the top left covers, in Core Image's space, which is measured from the bottom.
    private func cut(_ image: CIImage, _ rect: PixelRect, surfaceHeight: Int) -> CIImage {
        let bottom = surfaceHeight - rect.y - rect.height
        return image
            .cropped(to: CGRect(x: rect.x, y: bottom, width: rect.width, height: rect.height))
            .transformed(by: CGAffineTransform(translationX: CGFloat(-rect.x), y: CGFloat(-bottom)))
    }

    private func scaled(_ image: CIImage, _ width: Int, _ height: Int) -> CIImage {
        let extent = image.extent
        guard extent.width > 0, extent.height > 0, (Int(extent.width), Int(extent.height)) != (width, height) else { return image }
        let sx = CGFloat(width) / extent.width
        let sy = CGFloat(height) / extent.height
        return image.transformed(by: CGAffineTransform(scaleX: sx, y: sy), highQualityDownsample: true)
    }
}
