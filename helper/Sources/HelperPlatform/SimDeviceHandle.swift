// SPDX-License-Identifier: Apache-2.0
import CoreGraphics
import Foundation
import HelperCore

/// A booted `SimDevice`, found by its UDID in the default device set of the Xcode in use.
final class SimDeviceHandle: @unchecked Sendable {
    let udid: String
    let object: NSObject

    init(udid: String, developerDir: String) throws {
        guard let contextClass = NSClassFromString("SimServiceContext") as? NSObject.Type else {
            throw HelperFailure("CoreSimulator has no SimServiceContext", status: 409)
        }
        let context = try ObjC.guarded("SimServiceContext") {
            contextClass.perform(
                NSSelectorFromString("sharedServiceContextForDeveloperDir:error:"), with: developerDir, with: nil
            )?.takeUnretainedValue() as? NSObject
        }
        guard let context else { throw HelperFailure("CoreSimulator's service for \(developerDir) is not reachable", status: 409) }
        let set = try ObjC.guarded("the default device set") {
            context.perform(NSSelectorFromString("defaultDeviceSetWithError:"), with: nil)?.takeUnretainedValue() as? NSObject
        }
        let devices = try ObjC.guarded("the device list") { set?.value(forKey: "devices") as? [NSObject] } ?? []
        guard let device = devices.first(where: { ($0.value(forKey: "UDID") as? NSUUID)?.uuidString == udid }) else {
            throw HelperFailure("no simulator \(udid) in the device set of \(developerDir)", status: 404)
        }
        self.udid = udid
        object = device
    }

    var state: String {
        (try? ObjC.guarded("the device's state") { object.value(forKey: "stateString") as? String }) ?? nil ?? "Unknown"
    }

    func requireBooted() throws {
        let state = self.state
        guard state == "Booted" else { throw HelperFailure("the simulator \(udid) is not booted (\(state))", status: 409) }
    }

    /// The device type's screen: its size in pixels and its scale.
    func screen() throws -> ScreenGeometry {
        guard let deviceType = try ObjC.guarded("the device type", { object.value(forKey: "deviceType") as? NSObject }) else {
            throw HelperFailure("the simulator \(udid) has no device type")
        }
        typealias GetSize = @convention(c) (AnyObject, Selector) -> CGSize
        typealias GetFloat = @convention(c) (AnyObject, Selector) -> Float
        let sizeSelector = NSSelectorFromString("mainScreenSize")
        let scaleSelector = NSSelectorFromString("mainScreenScale")
        guard deviceType.responds(to: sizeSelector), deviceType.responds(to: scaleSelector) else {
            throw HelperFailure("the simulator's device type does not say its screen size")
        }
        let size = unsafeBitCast(deviceType.method(for: sizeSelector), to: GetSize.self)(deviceType, sizeSelector)
        let scale = unsafeBitCast(deviceType.method(for: scaleSelector), to: GetFloat.self)(deviceType, scaleSelector)
        return try ScreenGeometry.of(pixelWidth: Double(size.width), pixelHeight: Double(size.height), scale: Double(scale))
    }

    /// The Mach port of a service the device's launchd publishes, or nil when it has none.
    func lookup(_ service: String) -> mach_port_t? {
        typealias Lookup = @convention(c) (AnyObject, Selector, NSString, AutoreleasingUnsafeMutablePointer<NSError?>) -> mach_port_t
        let selector = NSSelectorFromString("lookup:error:")
        guard object.responds(to: selector) else { return nil }
        var error: NSError?
        let port = unsafeBitCast(object.method(for: selector), to: Lookup.self)(object, selector, service as NSString, &error)
        return port == 0 ? nil : port
    }
}
