// SPDX-License-Identifier: Apache-2.0
import CoreImage
import CoreVideo
import Foundation
import HelperCore
import ImageIO
import IOSurface
import UniformTypeIdentifiers
import VideoToolbox

/// Pictures of a framebuffer: a JPEG cut and scaled to a plan, and a pixel buffer scaled for an encoder.
///
/// A screenshot is drawn by Core Image on the GPU straight from the IOSurface -- one render and one JPEG encode, with no
/// copy of the full screen in between. A frame for the encoder is scaled by VideoToolbox, which the encoder works with.
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

    private let transferLock = NSLock()
    private var transfer: VTPixelTransferSession?

    /// The whole surface scaled into `buffer`, by VideoToolbox's scaler: the surface is wrapped, not copied, and the
    /// scaler starts in milliseconds where a GPU pipeline takes hundreds.
    func draw(_ surface: IOSurface, into buffer: CVPixelBuffer) throws {
        var source: Unmanaged<CVPixelBuffer>?
        guard CVPixelBufferCreateWithIOSurface(nil, surface, nil, &source) == kCVReturnSuccess, let source else {
            throw HelperFailure("the screen's surface cannot be read as pixels")
        }
        let pixels = source.takeRetainedValue()
        transferLock.lock()
        defer { transferLock.unlock() }
        if transfer == nil {
            var made: VTPixelTransferSession?
            guard VTPixelTransferSessionCreate(allocator: nil, pixelTransferSessionOut: &made) == noErr, let made else {
                throw HelperFailure("this Mac has no VideoToolbox scaler")
            }
            VTSessionSetProperty(made, key: kVTPixelTransferPropertyKey_ScalingMode, value: kVTScalingMode_Normal)
            transfer = made
        }
        let status = VTPixelTransferSessionTransferImage(transfer!, from: pixels, to: buffer)
        guard status == noErr else { throw HelperFailure("the screen could not be scaled (\(status))") }
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
