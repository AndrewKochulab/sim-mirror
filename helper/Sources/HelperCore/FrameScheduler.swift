// SPDX-License-Identifier: Apache-2.0
import Foundation

/// When a stream encodes: as soon as the screen changes, never faster than its frame rate, and a key frame now and then.
///
/// The simulator says when it presents a frame, so a change is encoded the moment it happens rather than at the next
/// tick of a clock -- that is the latency a polling stream pays. A burst of changes faster than the frame rate is
/// encoded at the frame rate, the latest picture winning. A stream starts with a key frame, and while the screen stays
/// still a key frame is sent again every key-frame interval when `idleKeyFrames` is on: a viewer that joins then has a
/// picture to start from instead of a blank screen until something moves.
///
/// Times are seconds on any clock that only goes forward.
public struct FrameScheduler: Sendable {
    public enum Decision: Equatable, Sendable {
        /// Encode the latest picture now, as a key frame or not.
        case encode(key: Bool)
        /// A change is waiting; encode it at this time.
        case wait(until: Double)
        /// Nothing to do until the screen changes or the next tick.
        case idle
    }

    public let minimumInterval: Double
    public let keyFrameInterval: Double
    public let idleKeyFrames: Bool
    private var lastEncode: Double?
    private var lastKey: Double?
    private var dirty = false
    private var keyWanted = false

    public init(fps: Int, keyFrameInterval: Double, idleKeyFrames: Bool) {
        minimumInterval = 1 / Double(max(fps, 1))
        self.keyFrameInterval = keyFrameInterval
        self.idleKeyFrames = idleKeyFrames
    }

    /// The simulator presented a new picture.
    public mutating func frameChanged(at now: Double) -> Decision {
        dirty = true
        return decide(at: now)
    }

    /// A clock tick: a change held back by the frame rate, or an idle key frame, may be due.
    public mutating func tick(at now: Double) -> Decision {
        decide(at: now)
    }

    /// The next frame encoded is a key frame, whenever it comes.
    public mutating func wantKeyFrame() {
        keyWanted = true
    }

    /// A frame was encoded at `now`.
    public mutating func encoded(at now: Double, key: Bool) {
        lastEncode = now
        dirty = false
        if key {
            lastKey = now
            keyWanted = false
        }
    }

    private func decide(at now: Double) -> Decision {
        guard let lastEncode, let lastKey else { return .encode(key: true) }
        let keyDue = keyWanted || now - lastKey >= keyFrameInterval
        if dirty {
            let allowed = lastEncode + minimumInterval
            return now >= allowed ? .encode(key: keyDue) : .wait(until: allowed)
        }
        return idleKeyFrames && keyDue ? .encode(key: true) : .idle
    }
}
