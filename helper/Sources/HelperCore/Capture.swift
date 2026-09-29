// SPDX-License-Identifier: Apache-2.0
import Foundation

/// What `sim-mirror-helper capture` is asked to serve: a cabled real device's screen, read the way QuickTime reads it.
public struct CaptureOptions: Equatable, Sendable {
    /// The device's hardware UDID, as SimMirror knows it.
    public let udid: String
    /// The device's name, which is what its screen is called among the Mac's capture devices.
    public let name: String
    public let socket: String
    /// The screen as devicectl measures it: its pixels and scale. The capture's own pixels may differ by one.
    public let screen: ScreenGeometry
    /// The capture device SimMirror found this device to be before, tried first.
    public let captureId: String?
    /// A picture of the screen taken another way, to tell apart devices that share a name.
    public let reference: String?
    public let parentPid: Int32?
    public let idleKeyFrames: Bool
    /// How long a picture of the screen is waited for: the first capture after the cable is plugged in switches it
    /// into another mode, measured at 6.4 seconds on an iPhone 14 Pro (2026-09-29).
    public let waitS: Double
    /// How long the capture keeps running after the last picture was asked for, so the next one is quick.
    public let lingerS: Double
    public let logLevel: Log.Level

    public init(
        udid: String, name: String, socket: String, screen: ScreenGeometry, captureId: String? = nil, reference: String? = nil,
        parentPid: Int32? = nil, idleKeyFrames: Bool = true, waitS: Double = CaptureOptions.defaultWaitS,
        lingerS: Double = CaptureOptions.defaultLingerS, logLevel: Log.Level = .info
    ) {
        self.udid = udid
        self.name = name
        self.socket = socket
        self.screen = screen
        self.captureId = captureId
        self.reference = reference
        self.parentPid = parentPid
        self.idleKeyFrames = idleKeyFrames
        self.waitS = waitS
        self.lingerS = lingerS
        self.logLevel = logLevel
    }

    public static let defaultWaitS = 15.0
    public static let waitRange = 1.0...120.0
    public static let defaultLingerS = 30.0
    public static let lingerRange = 0.0...600.0

    static func take(_ options: inout [String: String]) throws -> CaptureOptions {
        func required(_ key: String) throws -> String {
            guard let value = options.removeValue(forKey: key), !value.isEmpty else {
                throw HelperFailure("capture needs --\(key)\n\(Command.usage)", status: 400)
            }
            return value
        }
        func number(_ key: String) throws -> Double {
            let text = try required(key)
            guard let value = Double(text), value.isFinite else { throw HelperFailure("--\(key) is a number, not \(text)", status: 400) }
            return value
        }
        func seconds(_ key: String, _ range: ClosedRange<Double>, otherwise: Double) throws -> Double {
            guard let text = options.removeValue(forKey: key) else { return otherwise }
            guard let value = Double(text), range.contains(value) else {
                throw HelperFailure("--\(key) is \(Int(range.lowerBound)) to \(Int(range.upperBound)) seconds, not \(text)", status: 400)
            }
            return value
        }
        let udid = try required("udid")
        let name = try required("name")
        let socket = try required("socket")
        let screen = try ScreenGeometry.of(pixelWidth: number("width-px"), pixelHeight: number("height-px"), scale: number("scale"))
        let reference = options.removeValue(forKey: "reference")
        if let reference, !reference.hasPrefix("/") {
            throw HelperFailure("--reference is the absolute path of a picture, not \(reference)", status: 400)
        }
        return CaptureOptions(
            udid: udid, name: name, socket: socket, screen: screen, captureId: options.removeValue(forKey: "capture-id"),
            reference: reference, parentPid: try options.removeValue(forKey: "parent-pid").map(Command.pid),
            idleKeyFrames: try options.removeValue(forKey: "idle-key-frames").map(Command.onOff) ?? true,
            waitS: try seconds("wait", waitRange, otherwise: defaultWaitS),
            lingerS: try seconds("linger", lingerRange, otherwise: defaultLingerS),
            logLevel: try options.removeValue(forKey: "log-level").map(Command.logLevel) ?? .info
        )
    }
}


/// One of the Mac's capture devices that shows a cabled device's screen.
public struct CaptureCandidate: Equatable, Sendable {
    public let id: String
    public let name: String

    public init(id: String, name: String) {
        self.id = id
        self.name = name
    }
}

/// Which capture device is the one asked for.
///
/// A capture device is named for the device it shows, but its id is its own -- not the device's UDID -- so it is found
/// by the id it had before, else by its name, else, when several devices share the name, by which of them shows the
/// picture of the screen taken another way (`Likeness`).
public enum CaptureChoice: Equatable, Sendable {
    case use(String)
    /// Several are called that: the one whose screen looks like the reference.
    case compare([String])
    case none

    public static func pick(_ candidates: [CaptureCandidate], name: String, captureId: String?) -> CaptureChoice {
        if let captureId, candidates.contains(where: { $0.id == captureId }) { return .use(captureId) }
        let named = candidates.filter { $0.name == name }.map(\.id)
        switch named.count {
        case 0: return .none
        case 1: return .use(named[0])
        default: return .compare(named)
        }
    }
}

/// How alike two pictures of a screen are, each shrunk to the same few grey pixels: the mean difference, 0 to 255.
///
/// A screen captured by cable and one taken by devicectl a moment apart differ in the clock and little else, so the
/// device whose picture is nearest the reference is the one the reference was taken of.
public enum Likeness {
    /// The size pictures are shrunk to before they are compared.
    public static let width = 12
    public static let height = 26

    public static func distance(_ one: [UInt8], _ other: [UInt8]) -> Double? {
        guard one.count == other.count, !one.isEmpty else { return nil }
        let total = zip(one, other).reduce(0) { $0 + abs(Int($1.0) - Int($1.1)) }
        return Double(total) / Double(one.count)
    }

    /// The index of the picture nearest the reference, or nil when none can be compared with it.
    public static func nearest(_ reference: [UInt8], among pictures: [[UInt8]?]) -> Int? {
        let scored = pictures.enumerated().compactMap { index, picture in
            picture.flatMap { distance(reference, $0) }.map { (index, $0) }
        }
        return scored.min { $0.1 < $1.1 }?.0
    }
}

/// Whether a captured picture is turned from the portrait SimMirror serves: a device held on its side sends its
/// screen wider than tall, and it is turned back a quarter so points map onto it as they do in portrait.
public enum CaptureTurn: Equatable, Sendable {
    case none
    case quarter

    public static func of(width: Int, height: Int) -> CaptureTurn {
        width > height ? .quarter : .none
    }
}

/// When the capture runs: while anything holds it -- a stream -- and for a while after the last picture was asked for,
/// so a device's status bar reads 9:41 and its cable carries a picture only while someone is looking.
public struct CaptureDemand: Equatable, Sendable {
    public let lingerS: Double
    public private(set) var holders = 0
    public private(set) var lastWanted: Double?

    public init(lingerS: Double) {
        self.lingerS = lingerS
    }

    /// Something needs the capture from now until it lets go.
    public mutating func hold(at time: Double) {
        holders += 1
        lastWanted = time
    }

    public mutating func release(at time: Double) {
        holders = max(0, holders - 1)
        lastWanted = time
    }

    /// Whether the capture should be running at `time`.
    public func running(at time: Double) -> Bool {
        guard let lastWanted else { return false }
        return holders > 0 || time - lastWanted < lingerS
    }

    /// When nothing will need the capture any more if nothing asks again, or nil while something holds it.
    public func idleAt() -> Double? {
        guard holders == 0, let lastWanted else { return nil }
        return lastWanted + lingerS
    }
}

/// Whether this process is the one that asks macOS for the Camera, or has to start one that is.
///
/// macOS asks permission of a process's responsible process: under a terminal or an agent, that is the terminal or the
/// agent, which has not asked for the Camera and never will, so the capture quietly gets no pictures. Started again
/// with its responsibility disclaimed, the helper is its own responsible process and asks for itself.
public enum Disclaim {
    public static let variable = "SIM_MIRROR_HELPER_DISCLAIMED"

    public static func needed(_ environment: [String: String]) -> Bool {
        environment[variable] != "1"
    }
}
