// SPDX-License-Identifier: Apache-2.0
import Foundation

/// One input event, as SimMirror sends it: a finger at a point, a hardware button or a keyboard key, down or up.
/// Points are the device's points, portrait.
public struct HIDEvent: Equatable, Sendable, Codable {
    public enum Kind: String, Sendable, Codable { case touch, button, key }
    public enum Phase: String, Sendable, Codable { case down, up }

    public let kind: Kind
    public let phase: Phase
    public let x: Double
    public let y: Double
    public let button: String
    public let code: Int

    public init(kind: Kind, phase: Phase, x: Double = 0, y: Double = 0, button: String = "", code: Int = 0) {
        self.kind = kind
        self.phase = phase
        self.x = x
        self.y = y
        self.button = button
        self.code = code
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decode(Kind.self, forKey: .kind)
        phase = try container.decode(Phase.self, forKey: .phase)
        x = try container.decodeIfPresent(Double.self, forKey: .x) ?? 0
        y = try container.decodeIfPresent(Double.self, forKey: .y) ?? 0
        button = try container.decodeIfPresent(String.self, forKey: .button) ?? ""
        code = try container.decodeIfPresent(Int.self, forKey: .code) ?? 0
    }
}

/// A HID usage: its page and its code.
public struct HIDUsage: Equatable, Sendable {
    public let page: UInt32
    public let code: UInt32

    public init(page: UInt32, code: UInt32) {
        self.page = page
        self.code = code
    }

    /// The consumer page's power usage: the lock and side button.
    public static let power = HIDUsage(page: 0x0C, code: 0x30)
    /// The consumer page's menu usage: the home button.
    public static let menu = HIDUsage(page: 0x0C, code: 0x40)
    /// The consumer page's voice command usage: Siri.
    public static let voiceCommand = HIDUsage(page: 0x0C, code: 0xCF)
}

/// Where a finger is in one contact: touching down, moving, or lifting.
public enum DigitizerPhase: UInt64, Equatable, Sendable {
    case start = 0
    case position = 1
    case end = 2
}

/// One step a transport sends.
public enum HIDStep: Equatable, Sendable {
    /// A finger at a fraction of the screen's width and height.
    case touch(DigitizerPhase, x: Double, y: Double)
    case button(HIDUsage, down: Bool)
    case key(UInt32, down: Bool)
}

/// How HID reaches a device. Each is the only one that sends its kind of message.
public protocol HIDTransport: AnyObject, Sendable {
    /// ``dtuhid`` or ``indigo``, as settings and reports name it.
    var name: String { get }
    func send(_ step: HIDStep) throws
}

/// The transports a device is tried with, in order, for a preference and the CoreSimulator in use.
public enum TransportPolicy {
    public enum Preference: String, Sendable, CaseIterable {
        case auto, dtuhid, indigo
    }

    /// The first CoreSimulator whose guests take HID from `dtuhidd`; before it there is no such service.
    public static let firstDTUHIDCoreSimulator = "1155.4"

    public static func order(_ preference: Preference, coreSimulator: String?) -> [String] {
        switch preference {
        case .dtuhid: return ["dtuhid"]
        case .indigo: return ["indigo"]
        case .auto:
            guard let coreSimulator,
                coreSimulator.compare(firstDTUHIDCoreSimulator, options: .numeric) != .orderedAscending
            else { return ["indigo"] }
            return ["dtuhid", "indigo"]
        }
    }
}

/// Turns SimMirror's events into a transport's steps: points into fractions of the screen, a contact's downs into its
/// start and moves, and buttons into HID usages.
public final class InputDriver: @unchecked Sendable {
    public let transport: HIDTransport
    private let screen: ScreenGeometry
    private let lock = NSLock()
    private var touching = false

    public init(transport: HIDTransport, screen: ScreenGeometry) {
        self.transport = transport
        self.screen = screen
    }

    /// The steps these events are, in order, keeping track of whether a finger is down between calls.
    public func steps(for events: [HIDEvent]) throws -> [HIDStep] {
        lock.lock()
        defer { lock.unlock() }
        return try events.flatMap { try steps(for: $0) }
    }

    /// Send events in order.
    public func play(_ events: [HIDEvent]) throws {
        for step in try steps(for: events) {
            try transport.send(step)
        }
    }

    private func steps(for event: HIDEvent) throws -> [HIDStep] {
        switch event.kind {
        case .touch:
            return [touch(event)]
        case .key:
            guard (1...0xFFFF).contains(event.code) else {
                throw HelperFailure("not a keyboard usage: \(event.code)", status: 400)
            }
            return [.key(UInt32(event.code), down: event.phase == .down)]
        case .button:
            return try Self.button(event.button, down: event.phase == .down)
        }
    }

    private func touch(_ event: HIDEvent) -> HIDStep {
        let x = Self.fraction(event.x, of: screen.widthPt)
        let y = Self.fraction(event.y, of: screen.heightPt)
        switch event.phase {
        case .down:
            defer { touching = true }
            return .touch(touching ? .position : .start, x: x, y: y)
        case .up:
            touching = false
            return .touch(.end, x: x, y: y)
        }
    }

    /// A button's steps. Apple Pay has no usage of its own: it is the side button pressed twice.
    static func button(_ name: String, down: Bool) throws -> [HIDStep] {
        switch name {
        case "home": return [.button(.menu, down: down)]
        case "lock", "side": return [.button(.power, down: down)]
        case "siri": return [.button(.voiceCommand, down: down)]
        case "apple_pay":
            return down
                ? [.button(.power, down: true), .button(.power, down: false), .button(.power, down: true)]
                : [.button(.power, down: false)]
        default: throw HelperFailure("not a button: \(name)", status: 400)
        }
    }

    static func fraction(_ value: Double, of extent: Int) -> Double {
        guard extent > 0, value.isFinite else { return 0 }
        return min(1, max(0, value / Double(extent)))
    }
}
