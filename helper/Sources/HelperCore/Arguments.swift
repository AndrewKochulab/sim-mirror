// SPDX-License-Identifier: Apache-2.0
import Foundation

/// What the helper was started to do.
public enum Command: Equatable, Sendable {
    /// Print its versions as JSON and exit.
    case version
    /// Serve one device on a unix socket until SimMirror lets go of it.
    case serve(ServeOptions)
    /// Reach a device the way serving would -- its screen, a picture, input and the element tree -- say what worked as
    /// JSON, and exit 0 only if all of it did.
    case selfCheck(DeviceOptions)

    public static let usage = """
        usage: sim-mirror-helper version
               sim-mirror-helper serve --udid UDID --socket PATH [--parent-pid PID] [--hid auto|dtuhid|indigo]
                                       [--idle-key-frames on|off] [--log-level debug|info|warning|error]
               sim-mirror-helper self-check --udid UDID [--hid auto|dtuhid|indigo]
        """

    public static func parse(_ arguments: [String]) throws -> Command {
        guard let verb = arguments.first else { throw HelperFailure(usage, status: 400) }
        var options = try flags(Array(arguments.dropFirst()))
        switch verb {
        case "version":
            try refuseLeftovers(options)
            return .version
        case "serve":
            let device = try DeviceOptions.take(&options)
            guard let socket = options.removeValue(forKey: "socket"), !socket.isEmpty else {
                throw HelperFailure("serve needs --socket\n\(usage)", status: 400)
            }
            let parent = try options.removeValue(forKey: "parent-pid").map(pid)
            let idle = try options.removeValue(forKey: "idle-key-frames").map(onOff) ?? true
            let level = try options.removeValue(forKey: "log-level").map(logLevel) ?? .info
            try refuseLeftovers(options)
            return .serve(ServeOptions(device: device, socket: socket, parentPid: parent, idleKeyFrames: idle, logLevel: level))
        case "self-check":
            let device = try DeviceOptions.take(&options)
            try refuseLeftovers(options)
            return .selfCheck(device)
        default:
            throw HelperFailure("not a command: \(verb)\n\(usage)", status: 400)
        }
    }

    static func flags(_ arguments: [String]) throws -> [String: String] {
        var found: [String: String] = [:]
        var rest = arguments[...]
        while let flag = rest.popFirst() {
            guard flag.hasPrefix("--"), flag.count > 2 else { throw HelperFailure("not a flag: \(flag)\n\(usage)", status: 400) }
            guard let value = rest.popFirst() else { throw HelperFailure("\(flag) needs a value", status: 400) }
            found[String(flag.dropFirst(2))] = value
        }
        return found
    }

    static func refuseLeftovers(_ options: [String: String]) throws {
        guard let unknown = options.keys.sorted().first else { return }
        throw HelperFailure("not a flag here: --\(unknown)\n\(usage)", status: 400)
    }

    static func pid(_ text: String) throws -> Int32 {
        guard let value = Int32(text), value > 1 else { throw HelperFailure("not a pid: \(text)", status: 400) }
        return value
    }

    static func onOff(_ text: String) throws -> Bool {
        switch text {
        case "on": return true
        case "off": return false
        default: throw HelperFailure("--idle-key-frames is on or off, not \(text)", status: 400)
        }
    }

    static func logLevel(_ text: String) throws -> Log.Level {
        guard let level = Log.Level(name: text) else { throw HelperFailure("not a log level: \(text)", status: 400) }
        return level
    }
}

/// Which device, and how input should reach it.
public struct DeviceOptions: Equatable, Sendable {
    public let udid: String
    public let hid: TransportPolicy.Preference

    public init(udid: String, hid: TransportPolicy.Preference = .auto) {
        self.udid = udid
        self.hid = hid
    }

    static func take(_ options: inout [String: String]) throws -> DeviceOptions {
        guard let udid = options.removeValue(forKey: "udid"), !udid.isEmpty else {
            throw HelperFailure("a device is named with --udid\n\(Command.usage)", status: 400)
        }
        let hid = try options.removeValue(forKey: "hid").map { text in
            guard let preference = TransportPolicy.Preference(rawValue: text) else {
                throw HelperFailure("--hid is auto, dtuhid or indigo, not \(text)", status: 400)
            }
            return preference
        } ?? .auto
        return DeviceOptions(udid: udid, hid: hid)
    }
}

public struct ServeOptions: Equatable, Sendable {
    public let device: DeviceOptions
    public let socket: String
    /// The process to outlive by no more than a moment: when it is gone, so is the helper.
    public let parentPid: Int32?
    public let idleKeyFrames: Bool
    public let logLevel: Log.Level

    public init(device: DeviceOptions, socket: String, parentPid: Int32? = nil, idleKeyFrames: Bool = true, logLevel: Log.Level = .info) {
        self.device = device
        self.socket = socket
        self.parentPid = parentPid
        self.idleKeyFrames = idleKeyFrames
        self.logLevel = logLevel
    }
}

/// What `sim-mirror-helper version` prints.
public struct VersionReport: Equatable, Sendable, Encodable {
    public let version: String
    public let wire: Int
    public let coreSimulator: String?

    public init(coreSimulator: String?) {
        version = HelperVersion.current
        wire = Wire.version
        self.coreSimulator = coreSimulator
    }

    enum CodingKeys: String, CodingKey {
        case version, wire, coreSimulator = "core_simulator"
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(version, forKey: .version)
        try container.encode(wire, forKey: .wire)
        try container.encode(coreSimulator, forKey: .coreSimulator)
    }
}

/// What `sim-mirror-helper self-check` prints: each part of a device and whether it was reached.
public struct SelfCheckReport: Equatable, Sendable, Encodable {
    public struct Part: Equatable, Sendable, Encodable {
        public let name: String
        public let ok: Bool
        public let detail: String

        public init(name: String, ok: Bool, detail: String) {
            self.name = name
            self.ok = ok
            self.detail = detail
        }
    }

    public let parts: [Part]
    public var ok: Bool { parts.allSatisfy(\.ok) }

    public init(parts: [Part]) {
        self.parts = parts
    }

    enum CodingKeys: String, CodingKey { case ok, parts }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(ok, forKey: .ok)
        try container.encode(parts, forKey: .parts)
    }

    /// Check each part of a device in turn: a part that fails does not stop the next from being checked.
    public static func run(_ device: Device) async -> SelfCheckReport {
        var parts: [Part] = []
        func check(_ name: String, _ body: () async throws -> String) async {
            do {
                parts.append(Part(name: name, ok: true, detail: try await body()))
            } catch {
                parts.append(Part(name: name, ok: false, detail: HelperFailure.from(error).message))
            }
        }
        await check("screen") {
            let screen = try device.screen()
            return "\(screen.widthPx)x\(screen.heightPx) pixels, \(screen.widthPt)x\(screen.heightPt) points"
        }
        await check("screenshot") {
            let jpeg = try await device.screenshot(ScreenshotRequest(maxWidth: 160, quality: 40))
            return "\(jpeg.width)x\(jpeg.height), \(jpeg.data.count) bytes"
        }
        await check("input") { "through \(try device.input().transport.name)" }
        await check("element tree") { "\(count(try await device.tree())) elements" }
        return SelfCheckReport(parts: parts)
    }

    static func count(_ node: AXNode) -> Int {
        1 + node.children.reduce(0) { $0 + count($1) }
    }
}
