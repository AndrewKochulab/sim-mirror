// SPDX-License-Identifier: Apache-2.0
import Foundation
import HelperCore
import IOSurface

/// The selector a newer CoreSimulator's display descriptor answers, declared so it can be sent to any object.
@objc protocol SimScreenCallbacks {
    @objc(registerScreenCallbacksWithUUID:callbackQueue:frameCallback:surfacesChangedCallback:propertiesChangedCallback:)
    func registerScreenCallbacks(
        uuid: UUID,
        callbackQueue: DispatchQueue,
        frameCallback: @convention(block) @escaping () -> Void,
        surfacesChangedCallback: @convention(block) @escaping () -> Void,
        propertiesChangedCallback: @convention(block) @escaping () -> Void
    )

    @objc(unregisterScreenCallbacksWithUUID:)
    func unregisterScreenCallbacks(uuid: UUID)
}

/// The selectors an older CoreSimulator's display descriptor answers instead.
@objc protocol SimDisplayCallbacks {
    @objc(registerCallbackWithUUID:ioSurfacesChangeCallback:)
    func registerSurfacesCallback(uuid: UUID, ioSurfacesChangeCallback: @convention(block) @escaping (AnyObject?) -> Void)

    @objc(registerCallbackWithUUID:damageRectanglesCallback:)
    func registerDamageCallback(uuid: UUID, damageRectanglesCallback: @convention(block) @escaping (AnyObject?) -> Void)

    @objc(unregisterIOSurfacesChangeCallbackWithUUID:)
    func unregisterSurfacesCallback(uuid: UUID)

    @objc(unregisterDamageRectanglesCallbackWithUUID:)
    func unregisterDamageCallback(uuid: UUID)
}

/// A device's screen as shared memory: the IOSurface the simulator draws into, and a call each time it presents.
///
/// Registering for a display's callbacks is what has SimulatorKit hand its surface to this process, so every display
/// port is registered and the screen picked from them (`SurfaceChoice`). Reading the surface copies nothing.
final class Framebuffer: PixelSource, @unchecked Sendable {
    static let displayPort = "com.apple.framebuffer.display"

    private let signal = FrameSignal(label: "sim-mirror.framebuffer")
    private var queue: DispatchQueue { signal.queue }
    private let lock = NSLock()
    private let screen: ScreenGeometry
    private var registrations: [(descriptor: NSObject, uuid: UUID, modern: Bool)] = []

    init(device: SimDeviceHandle, screen: ScreenGeometry) throws {
        self.screen = screen
        let descriptors = try Self.displays(of: device)
        guard !descriptors.isEmpty else { throw HelperFailure("the simulator \(device.udid) shows no display", status: 409) }
        for descriptor in descriptors {
            let uuid = UUID()
            let changed: @convention(block) () -> Void = { [weak self] in self?.presented() }
            if descriptor.responds(to: #selector(SimScreenCallbacks.registerScreenCallbacks)) {
                try ObjC.guarded("registering for a display's frames") {
                    (descriptor as AnyObject).registerScreenCallbacks(
                        uuid: uuid, callbackQueue: queue, frameCallback: changed, surfacesChangedCallback: changed,
                        propertiesChangedCallback: {}
                    )
                }
                registrations.append((descriptor, uuid, true))
            } else if descriptor.responds(to: #selector(SimDisplayCallbacks.registerSurfacesCallback)) {
                let changedWith: @convention(block) (AnyObject?) -> Void = { [weak self] _ in self?.presented() }
                try ObjC.guarded("registering for a display's surfaces") {
                    (descriptor as AnyObject).registerSurfacesCallback(uuid: uuid, ioSurfacesChangeCallback: changedWith)
                    (descriptor as AnyObject).registerDamageCallback(uuid: uuid, damageRectanglesCallback: changedWith)
                }
                registrations.append((descriptor, uuid, false))
            }
        }
        guard !registrations.isEmpty else {
            throw HelperFailure("this CoreSimulator's displays offer no frame callbacks SimMirror knows", status: 409)
        }
    }

    private static func displays(of device: SimDeviceHandle) throws -> [NSObject] {
        try ObjC.guarded("reading the device's ports") {
            guard let io = ObjC.object(device.object, "io") else { return [] }
            _ = ObjC.anyObject(io, "updateIOPorts")
            let ports = io.value(forKey: "deviceIOPorts") as? [NSObject] ?? []
            return ports.compactMap { port -> NSObject? in
                guard let name = ObjC.anyObject(port, "portIdentifier"), "\(name)" == displayPort else { return nil }
                return ObjC.object(port, "descriptor")
            }
        }
    }

    /// The screen's surface now, or nil while the simulator has not drawn one. Its seed changes each time it is drawn.
    func picture() -> Picture? {
        lock.lock()
        let descriptors = registrations.map(\.descriptor)
        lock.unlock()
        let surfaces: [IOSurface?] = descriptors.map { descriptor in
            (try? ObjC.guarded("reading a display's surface") {
                (ObjC.anyObject(descriptor, "framebufferSurface") ?? ObjC.anyObject(descriptor, "ioSurface"))
                    .map { unsafeBitCast($0, to: IOSurface.self) }
            }) ?? nil
        }
        let sizes = surfaces.map { surface in surface.map { (IOSurfaceGetWidth($0), IOSurfaceGetHeight($0)) } ?? (0, 0) }
        return SurfaceChoice.pick(sizes, screen: screen).flatMap { surfaces[$0] }.map {
            Picture(surface: $0, version: UInt64(IOSurfaceGetSeed($0)))
        }
    }

    func picture(waiting seconds: Double) throws -> Picture {
        try FrameSignal.waiting(seconds, for: picture, otherwise: HelperFailure("the simulator has not drawn its screen yet", status: 503))
    }

    /// A simulator draws whether or not anyone looks, so there is nothing to start.
    func hold() throws {}
    func letGo() {}

    func observe(_ onFrame: @escaping @Sendable () -> Void) -> UUID { signal.observe(onFrame) }
    func forget(_ id: UUID) { signal.forget(id) }
    func async(_ body: @escaping @Sendable () -> Void) { signal.async(body) }
    func async(after seconds: Double, _ body: @escaping @Sendable () -> Void) { signal.async(after: seconds, body) }

    private func presented() {
        signal.presented()
    }

    func close() {
        lock.lock()
        let registered = registrations
        registrations = []
        lock.unlock()
        signal.clear()
        for (descriptor, uuid, modern) in registered {
            _ = try? ObjC.guarded("letting go of a display") {
                if modern {
                    (descriptor as AnyObject).unregisterScreenCallbacks(uuid: uuid)
                } else {
                    (descriptor as AnyObject).unregisterSurfacesCallback(uuid: uuid)
                    (descriptor as AnyObject).unregisterDamageCallback(uuid: uuid)
                }
            }
        }
    }
}
