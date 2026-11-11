// SPDX-License-Identifier: Apache-2.0
import Foundation

/// An agent event SimMirror hands the overlay drawn over a macOS app's window: the screen socket's own agent event
/// (`protocol/v1/agent-event.schema.json`), read for what the overlay draws. Its points are shares of the window.
public struct OverlayEvent: Equatable, Sendable, Decodable {
    public enum Phase: String, Sendable, Decodable { case intent, done, working }

    public let phase: Phase
    public var kind: String = ""
    public var points: [[Double]] = []
    public var activity: String = ""
    /// The scope's colour for the agent (`agent.cursor_color`), or nil for the Mac's accent colour.
    public var color: OverlayColor?
    public var agent: String = ""
    public var leadS: Double = 0
    public var durationS: Double = 0
    public var lingerS: Double = 0
    public var ongoing = false
    public var ok = true
    public var pointer = true

    public init(phase: Phase, kind: String = "", points: [[Double]] = [], activity: String = "", color: OverlayColor? = nil,
                agent: String = "", leadS: Double = 0, durationS: Double = 0, lingerS: Double = 0, ongoing: Bool = false,
                ok: Bool = true, pointer: Bool = true) {
        self.phase = phase; self.kind = kind; self.points = points; self.activity = activity; self.color = color; self.agent = agent
        self.leadS = leadS; self.durationS = durationS; self.lingerS = lingerS; self.ongoing = ongoing; self.ok = ok
        self.pointer = pointer
    }

    enum CodingKeys: String, CodingKey { case phase, gesture, activity, color, agent, leadMs = "lead_ms", lingerMs = "linger_ms", ongoing, ok, pointer }
    enum GestureKeys: String, CodingKey { case kind, points, durationMs = "duration_ms" }
    enum AgentKeys: String, CodingKey { case title }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        phase = try c.decode(Phase.self, forKey: .phase)
        activity = try c.decodeIfPresent(String.self, forKey: .activity) ?? ""
        color = OverlayColor(hex: try c.decodeIfPresent(String.self, forKey: .color) ?? "")
        if c.contains(.agent) {
            agent = try c.nestedContainer(keyedBy: AgentKeys.self, forKey: .agent).decodeIfPresent(String.self, forKey: .title) ?? ""
        }
        leadS = Double(try c.decodeIfPresent(Int.self, forKey: .leadMs) ?? 0) / 1000
        lingerS = Double(try c.decodeIfPresent(Int.self, forKey: .lingerMs) ?? 0) / 1000
        ongoing = try c.decodeIfPresent(Bool.self, forKey: .ongoing) ?? false
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? true
        pointer = try c.decodeIfPresent(Bool.self, forKey: .pointer) ?? true
        if c.contains(.gesture) {
            let g = try c.nestedContainer(keyedBy: GestureKeys.self, forKey: .gesture)
            kind = try g.decodeIfPresent(String.self, forKey: .kind) ?? ""
            durationS = Double(try g.decodeIfPresent(Int.self, forKey: .durationMs) ?? 0) / 1000
            points = try g.decodeIfPresent([[Double]].self, forKey: .points) ?? []
        }
        guard points.allSatisfy({ $0.count == 2 && $0.allSatisfy { $0.isFinite && $0 >= 0 && $0 <= 1 } }) else {
            throw HelperFailure("an overlay event's points are shares of the window, from 0 to 1", status: 400)
        }
    }
}

/// A colour to draw the agent in, from ``#rrggbb``; what is not one is no colour, and the Mac's accent is used.
public struct OverlayColor: Equatable, Sendable {
    /// The least contrast white text keeps on it -- WCAG's for bold text -- before near-black is used instead, as the
    /// viewer decides (`viewer/src/agent-cursor.ts`, `textOn`).
    public static let whiteTextContrast = 3.0

    public let red: Double
    public let green: Double
    public let blue: Double

    public init?(hex: String) {
        let digits = hex.dropFirst()
        guard hex.first == "#", digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
        red = Double((value >> 16) & 0xFF) / 255
        green = Double((value >> 8) & 0xFF) / 255
        blue = Double(value & 0xFF) / 255
    }

    /// Whether its label's text is white -- else near-black.
    public var whiteText: Bool {
        func linear(_ c: Double) -> Double { c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        let light = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
        return 1.05 / (light + 0.05) >= Self.whiteTextContrast
    }
}

/// A point of the window as shares of its width and height.
public struct OverlayPoint: Equatable, Sendable {
    public let x: Double
    public let y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
    init(_ pair: [Double]) { x = pair[0]; y = pair[1] }
}

/// Something drawn for a while at places on the window: a tap's ring, a long press filling, a swipe's trail.
public struct OverlayMark: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case ripple, hold, trail }
    public let id: Int
    public let kind: Kind
    public let points: [OverlayPoint]
    /// When it starts, on the model's clock, and for how long it is drawn.
    public let start: Double
    public let duration: Double
}

/// How the pointer is drawn.
public enum OverlayPose: Equatable, Sendable {
    /// Acting: where a gesture lands.
    case acting
    /// Where it last acted, dimmed, while the agent looks or thinks.
    case resting
    /// Out of the way in the window's corner: the agent has not acted for `OverlayModel.parkAfterS`.
    case parked
}

/// What the overlay shows at a moment.
public struct OverlayScene: Equatable, Sendable {
    /// Whether anything is shown at all.
    public let visible: Bool
    /// Where the pointer is going, and how long it takes to get there; nil while it is parked or not shown.
    public let target: OverlayPoint?
    public let travelS: Double
    public let pose: OverlayPose
    public let failed: Bool
    /// What the pointer's label says: what the agent is doing, else its name, else that it is idle.
    public let label: String
    /// The agent's name, said beside its label when there is room.
    public let agent: String
    /// The scope's colour for the agent; nil for the Mac's accent.
    public let color: OverlayColor?
    public let marks: [OverlayMark]
}

/// What the overlay over a macOS app draws, decided from the agent's events and a clock, as the browser viewer's
/// pointer is (`viewer/src/agent-cursor.ts`): the same timings, the same poses.
///
/// * An **intent** puts the pointer where its gesture lands -- gliding there for at most `glideMaxS` of its lead -- and,
///   once the lead is spent, rings a tap, fills a long press or trails a swipe.
/// * **done** dims it where it landed; a failure shakes it.
/// * **working** says what the agent is doing now; an ended call clears that.
/// * With no gesture for `parkAfterS` it goes to the window's corner, dimmed and smaller, saying "Idle" unless the
///   agent says what it is doing; the next gesture brings it back.
/// * Everything goes `activeS` -- or the event's linger, or while a call is still running -- after the last event.
public struct OverlayModel: Sendable {
    public static let activeS = 5.0
    public static let rippleS = 0.6
    public static let failedS = 0.9
    public static let glideMaxS = 0.4
    public static let parkAfterS = 5.0
    /// How long a running call holds everything: past its next beat (every 10 s), with room for one to be late.
    public static let ongoingS = 15.0
    public static let idle = "Idle"

    private var target: OverlayPoint?
    private var travelS = 0.0
    /// A swipe's or drag's end, which the pointer follows once its lead is spent.
    private var follow: (to: OverlayPoint, at: Double, travelS: Double)?
    private var lastEvent = -Double.infinity
    private var lastGesture = -Double.infinity
    private var lingerS = 0.0
    private var heldUntil = -Double.infinity
    private var resting = false
    private var failedUntil = -Double.infinity
    private var activity = ""
    private var color: OverlayColor?
    private var agent = ""
    private var marks: [OverlayMark] = []
    private var nextMark = 1

    public init() {}

    public mutating func handle(_ event: OverlayEvent, at now: Double) {
        switch event.phase {
        case .intent:
            guard event.pointer else { return }
            touched(event, now)
            if !event.activity.isEmpty { activity = event.activity }
            intent(event, now)
        case .done:
            lastEvent = now
            resting = true
            if !event.ok { failedUntil = now + Self.failedS }
        case .working:
            touched(event, now)
            heldUntil = event.ongoing ? now + Self.ongoingS : -Double.infinity
            if !event.activity.isEmpty { activity = event.activity } else if !event.ongoing { activity = "" }
        }
    }

    private mutating func touched(_ event: OverlayEvent, _ now: Double) {
        lastEvent = now
        lingerS = event.lingerS
        color = event.color
        if !event.agent.isEmpty { agent = event.agent }
    }

    private mutating func intent(_ event: OverlayEvent, _ now: Double) {
        guard let first = event.points.first.map(OverlayPoint.init) else { return }
        lastGesture = now
        resting = false
        target = first
        travelS = min(event.leadS, Self.glideMaxS)
        follow = nil
        let start = now + event.leadS
        switch event.kind {
        case "tap", "right_click":
            mark(.ripple, [first], start, Self.rippleS)
        case "long_press":
            mark(.hold, [first], start, event.durationS + Self.rippleS)
        case "swipe", "drag":
            guard event.points.count > 1 else { break }
            let points = event.points.map(OverlayPoint.init)
            mark(.trail, points, now, event.leadS + event.durationS + Self.rippleS)
            follow = (points[points.count - 1], start, event.durationS)
        default:
            break
        }
    }

    private mutating func mark(_ kind: OverlayMark.Kind, _ points: [OverlayPoint], _ start: Double, _ duration: Double) {
        marks.append(OverlayMark(id: nextMark, kind: kind, points: points, start: start, duration: duration))
        nextMark += 1
    }

    /// Until when anything is shown: the agent's last event and its linger, or a running call's next beat.
    public var shownUntil: Double { max(lastEvent + max(Self.activeS, lingerS), heldUntil) }

    public mutating func scene(at now: Double) -> OverlayScene {
        marks.removeAll { now >= $0.start + $0.duration }
        if let follow, now >= follow.at {
            target = follow.to
            travelS = follow.travelS
            self.follow = nil
        }
        let visible = now < shownUntil && (target != nil || !activity.isEmpty)
        let parked = target == nil || now - lastGesture >= Self.parkAfterS
        let pose: OverlayPose = parked ? .parked : resting ? .resting : .acting
        let label = !activity.isEmpty ? activity : parked ? Self.idle : agent
        return OverlayScene(
            visible: visible, target: parked ? nil : target, travelS: travelS, pose: pose, failed: now < failedUntil,
            label: label, agent: agent, color: color, marks: visible ? marks : []
        )
    }
}
