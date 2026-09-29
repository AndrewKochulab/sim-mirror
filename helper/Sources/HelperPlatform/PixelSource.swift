// SPDX-License-Identifier: Apache-2.0
import Foundation
import HelperCore
import IOSurface

/// A device's screen as it is now: the surface it is drawn in, and a number that changes whenever its pixels may have.
struct Picture {
    let surface: IOSurface
    let version: UInt64
    /// What must stay alive while the surface is read: a captured frame's buffer, which its pool refills once let go.
    var keeping: AnyObject?

    var width: Int { IOSurfaceGetWidth(surface) }
    var height: Int { IOSurfaceGetHeight(surface) }
}

/// Where a device's pictures come from -- a simulator's framebuffer, or a cabled device's screen capture -- and a call
/// each time a new one is presented. Screenshots and the H.264 stream read any of them the same way (`ScreenFeed`).
protocol PixelSource: AnyObject, Sendable {
    /// The screen now, or nil while there is no picture of it.
    func picture() -> Picture?
    /// The screen, waiting up to `seconds` for a picture of it.
    func picture(waiting seconds: Double) throws -> Picture
    /// Something needs pictures until `letGo`: the source may run only while something does.
    func hold() throws
    func letGo()
    /// Call `onFrame` on the source's queue each time a picture is presented, until `forget`.
    func observe(_ onFrame: @escaping @Sendable () -> Void) -> UUID
    func forget(_ id: UUID)
    /// Run on the source's queue, where its pictures arrive.
    func async(_ body: @escaping @Sendable () -> Void)
    func async(after seconds: Double, _ body: @escaping @Sendable () -> Void)
    func close()
}

/// The queue a source's pictures arrive on and those waiting for them: what every `PixelSource` has alike.
final class FrameSignal: @unchecked Sendable {
    let queue: DispatchQueue
    private let lock = NSLock()
    private var observers: [UUID: @Sendable () -> Void] = [:]

    init(label: String) {
        queue = DispatchQueue(label: label, qos: .userInteractive)
    }

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

    func async(_ body: @escaping @Sendable () -> Void) {
        queue.async(execute: body)
    }

    func async(after seconds: Double, _ body: @escaping @Sendable () -> Void) {
        queue.asyncAfter(deadline: .now() + seconds, execute: body)
    }

    /// Tell everyone waiting that a picture was presented; called on the queue.
    func presented() {
        lock.lock()
        let calls = Array(observers.values)
        lock.unlock()
        for call in calls { call() }
    }

    func clear() {
        lock.lock()
        observers = [:]
        lock.unlock()
    }

    /// Poll `read` until it answers or `seconds` pass.
    static func waiting<T>(_ seconds: Double, for read: () -> T?, otherwise failure: HelperFailure) throws -> T {
        let deadline = Date().addingTimeInterval(seconds)
        while true {
            if let found = read() { return found }
            guard Date() < deadline else { throw failure }
            Thread.sleep(forTimeInterval: 0.02)
        }
    }
}
