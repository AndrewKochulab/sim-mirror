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
final class Framebuffer: @unchecked Sendable {
    static let displayPort = "com.apple.framebuffer.display"

    private let queue = DispatchQueue(label: "sim-mirror.framebuffer", qos: .userInteractive)
    private let lock = NSLock()
    private let screen: ScreenGeometry
    private var registrations: [(descriptor: NSObject, uuid: UUID, modern: Bool)] = []
    private var observers: [UUID: @Sendable () -> Void] = [:]

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

    /// The screen's surface now, or nil while the simulator has not drawn one.
    func surface() -> IOSurface? {
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
        return SurfaceChoice.pick(sizes, screen: screen).flatMap { surfaces[$0] }
    }

    /// The surface, waiting up to `seconds` for the simulator to draw one.
    func surface(waiting seconds: Double) throws -> IOSurface {
        let deadline = Date().addingTimeInterval(seconds)
        while true {
            if let surface = surface() { return surface }
            guard Date() < deadline else { throw HelperFailure("the simulator has not drawn its screen yet", status: 503) }
            Thread.sleep(forTimeInterval: 0.02)
        }
    }

    /// Call `onFrame` on the framebuffer's queue each time the simulator presents, until `forget`.
    func observe(_ onFrame: @escaping @Sendable () -> Void) -> UUID {
        let id = UUID()
        lock.lock()
        observers[id] = onFrame
        lock.unlock()
        return id
    }

    func forget(_ id: UUID) {
        lock.lock()
        observers[id] = nil
        lock.unlock()
    }

    /// Run on the framebuffer's queue, where the simulator's calls arrive.
    func async(_ body: @escaping @Sendable () -> Void) {
        queue.async(execute: body)
    }

    func async(after seconds: Double, _ body: @escaping @Sendable () -> Void) {
        queue.asyncAfter(deadline: .now() + seconds, execute: body)
    }

    private func presented() {
        lock.lock()
        let calls = Array(observers.values)
        lock.unlock()
        for call in calls { call() }
    }

    func close() {
        lock.lock()
        let registered = registrations
        registrations = []
        observers = [:]
        lock.unlock()
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
