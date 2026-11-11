// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

/// A device that answers nothing but draws an overlay when it has one.
final class OverlayDevice: Device, @unchecked Sendable {
    let coreSimulator: String? = nil
    let source: String? = nil
    var events: [OverlayEvent] = []
    let draws: Bool
    init(draws: Bool) { self.draws = draws }
    func screen() throws -> ScreenGeometry { throw HelperFailure("none") }
    func screenshot(_ request: ScreenshotRequest) async throws -> JPEG { throw HelperFailure("none") }
    func tree() async throws -> AXTree { throw HelperFailure("none") }
    func elements(at points: [ScreenPoint]) async throws -> [AXNode?] { [] }
    func input() throws -> InputDriver { throw HelperFailure("none") }
    func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> { throw HelperFailure("none") }
    func warmed() async {}
    func rotate(_ orientation: InterfaceOrientation) throws { throw HelperFailure("none") }
    func overlay(_ event: OverlayEvent) throws {
        guard draws else { throw HelperFailure("off", status: 409) }
        events.append(event)
    }
}

/// A device with no overlay of its own: the protocol's default.
final class PlainDevice: Device, @unchecked Sendable {
    let coreSimulator: String? = nil
    let source: String? = nil
    func screen() throws -> ScreenGeometry { throw HelperFailure("none") }
    func screenshot(_ request: ScreenshotRequest) async throws -> JPEG { throw HelperFailure("none") }
    func tree() async throws -> AXTree { throw HelperFailure("none") }
    func elements(at points: [ScreenPoint]) async throws -> [AXNode?] { [] }
    func input() throws -> InputDriver { throw HelperFailure("none") }
    func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> { throw HelperFailure("none") }
    func warmed() async {}
    func rotate(_ orientation: InterfaceOrientation) throws { throw HelperFailure("none") }
}

private func decode(_ json: String) throws -> OverlayEvent {
    try JSONDecoder().decode(OverlayEvent.self, from: Data(json.utf8))
}

private let tap = OverlayEvent(phase: .intent, kind: "tap", points: [[0.5, 0.25]], activity: "Clicking “Add”", agent: "agent-a",
                               leadS: 0.25, durationS: 0.05, lingerS: 60)

@Suite struct OverlayEventTests {
    @Test func anAgentEventIsReadForWhatTheOverlayDraws() throws {
        let intent = try decode(#"""
            {"type":"agent","id":"a1","phase":"intent","agent":{"key":"k","title":"agent-a"},"pointer":true,"label":"Add",
             "caption":"","activity":"Clicking “Add”","lead_ms":250,"linger_ms":60000,
             "gesture":{"kind":"tap","duration_ms":50,"points":[[0.5,0.25]]}}
            """#)
        #expect(intent == tap)
        #expect(try decode(#"{"type":"agent","id":"a1","phase":"done","ok":false}"#) == OverlayEvent(phase: .done, ok: false))
        #expect(try decode(#"{"phase":"working","agent":{"key":"k"},"ongoing":true,"linger_ms":0,"activity":"Building"}"#)
            == OverlayEvent(phase: .working, activity: "Building", ongoing: true))
        #expect(try decode(#"{"phase":"intent","pointer":false,"gesture":{}}"#) == OverlayEvent(phase: .intent, pointer: false))
        for bad in [#"{"phase":"intent","gesture":{"kind":"tap","duration_ms":0,"points":[[2,0]]}}"#,
                    #"{"phase":"intent","gesture":{"kind":"tap","duration_ms":0,"points":[[0.5]]}}"#,
                    #"{"phase":"later"}"#] {
            #expect(throws: (any Error).self) { try decode(bad) }
        }
    }

    @Test func anOverlayRequestCarriesItsEventAndIsAnsweredInOrder() throws {
        let request = try Request.decode(Data(#"{"op":"overlay","event":{"phase":"done","ok":true}}"#.utf8))
        #expect(request == .overlay(OverlayEvent(phase: .done)))
        #expect(request.inOrder)
        #expect(throws: HelperFailure.self) { try Request.decode(Data(#"{"op":"overlay"}"#.utf8)) }
    }

    @Test func theRouterHandsTheEventToTheDeviceOrSaysItDrawsNone() async {
        let device = OverlayDevice(draws: true)
        let sent = Sent()
        await Router(device: device).answer(3, .overlay(tap)) { await sent.add($0) }
        #expect(await sent.frames.map(\.kind) == [.reply])
        #expect(device.events == [tap])
        let refused = Sent()
        await Router(device: PlainDevice()).answer(4, .overlay(tap)) { await refused.add($0) }
        let frames = await refused.frames
        #expect(frames.map(\.kind) == [.failure])
        #expect(text(frames[0]).contains("--overlay on"))
    }

    @Test func aWindowHelperDrawsTheOverlayOnlyWhenAsked() throws {
        #expect(try Command.parse(["window", "--pid", "42", "--socket", "/tmp/app", "--overlay", "on"])
            == .window(MacWindowOptions(pid: 42, socket: "/tmp/app", windowID: nil, parentPid: nil, overlay: true)))
        #expect(try Command.parse(["window", "--pid", "42", "--socket", "/tmp/app", "--overlay", "off"])
            == .window(MacWindowOptions(pid: 42, socket: "/tmp/app", windowID: nil, parentPid: nil)))
        #expect(throws: HelperFailure.self) { try Command.parse(["window", "--pid", "42", "--socket", "/tmp/app", "--overlay", "maybe"]) }
        #expect(VersionReport.features.contains("mac-overlay"))
    }
}

@Suite struct OverlayModelTests {
    @Test func nothingIsShownBeforeAnAgentActs() {
        var model = OverlayModel()
        let scene = model.scene(at: 0)
        #expect(!scene.visible && scene.marks.isEmpty && scene.target == nil)
    }

    @Test func aTapGlidesThereThenRingsWhereItLands() {
        var model = OverlayModel()
        model.handle(tap, at: 10)
        let first = model.scene(at: 10)
        #expect(first.visible && first.target == OverlayPoint(x: 0.5, y: 0.25) && first.pose == .acting)
        #expect(first.travelS == 0.25 && first.label == "Clicking “Add”" && first.agent == "agent-a")
        // The ring is at the point, from when the lead is spent, for its own time.
        #expect(first.marks == [OverlayMark(id: 1, kind: .ripple, points: [OverlayPoint(x: 0.5, y: 0.25)], start: 10.25, duration: 0.6)])
        #expect(model.scene(at: 10.84).marks.count == 1)
        #expect(model.scene(at: 10.85).marks.isEmpty)
        model.handle(OverlayEvent(phase: .done), at: 10.3)
        #expect(model.scene(at: 10.4).pose == .resting)
    }

    @Test func aLongLeadGlidesNoLongerThanTheLongestGlide() {
        var model = OverlayModel()
        var slow = tap
        slow.leadS = 1
        model.handle(slow, at: 0)
        #expect(model.scene(at: 0).travelS == OverlayModel.glideMaxS)
    }

    @Test func aLongPressFillsForItsHoldAndASwipeIsFollowedAlongItsTrail() {
        var model = OverlayModel()
        model.handle(OverlayEvent(phase: .intent, kind: "long_press", points: [[0.1, 0.1]], leadS: 0.2, durationS: 1), at: 0)
        #expect(model.scene(at: 0).marks.map(\.kind) == [.hold])
        #expect(model.scene(at: 0).marks[0].duration == 1 + OverlayModel.rippleS)
        model.handle(OverlayEvent(phase: .intent, kind: "swipe", points: [[0.5, 0.8], [0.5, 0.2]], leadS: 0.2, durationS: 0.3), at: 5)
        let start = model.scene(at: 5)
        #expect(start.target == OverlayPoint(x: 0.5, y: 0.8) && start.marks.map(\.kind) == [.trail])
        let along = model.scene(at: 5.2)
        #expect(along.target == OverlayPoint(x: 0.5, y: 0.2) && along.travelS == 0.3)
        // A swipe with one point leaves no trail; a hover or a scroll none either.
        for kind in ["drag", "hover", "scroll", "type"] {
            model.handle(OverlayEvent(phase: .intent, kind: kind, points: [[0.3, 0.3]]), at: 6)
        }
        #expect(model.scene(at: 6).marks.map(\.kind) == [.trail])
    }

    @Test func betweenActionsItStaysAndThinksAndParksOnlyAfterARealBreak() {
        var model = OverlayModel()
        model.handle(OverlayEvent(phase: .working, agent: "agent-a", lingerS: 60, ongoing: true), at: 0)
        model.handle(tap, at: 0)
        _ = model.scene(at: 0)
        model.handle(OverlayEvent(phase: .done), at: 0.3)
        model.handle(OverlayEvent(phase: .working, agent: "agent-a", lingerS: 60, ongoing: false), at: 0.5)
        let between = model.scene(at: 3.4)
        // Its last action's label stays until thinking takes over.
        #expect(between.pose == .resting && between.target == OverlayPoint(x: 0.5, y: 0.25) && between.label == "Clicking “Add”")
        // Three seconds after the call ended: thinking, still where it acted.
        let thinking = model.scene(at: 3.5)
        #expect(thinking.label == OverlayModel.thinking && thinking.pose == .resting && thinking.target != nil)
        // A call that has not said what it does yet leaves thinking in place.
        model.handle(OverlayEvent(phase: .working, agent: "agent-a", lingerS: 60, ongoing: true), at: 4)
        #expect(model.scene(at: 4).label == OverlayModel.thinking)
        model.handle(OverlayEvent(phase: .working, agent: "agent-a", lingerS: 60, ongoing: false), at: 4.5)
        // Agents think 5 to 15 seconds between actions: that is not idle.
        #expect(model.scene(at: 15.5).pose == .resting)
        #expect(model.scene(at: 4.5 + OverlayModel.parkAfterS - 0.1).pose == .resting)
        let parked = model.scene(at: 4.5 + OverlayModel.parkAfterS)
        #expect(parked.visible && parked.pose == .parked && parked.target == nil && parked.label == OverlayModel.idle)
        model.handle(tap, at: 40)
        let back = model.scene(at: 40)
        #expect(back.pose == .acting && back.target == OverlayPoint(x: 0.5, y: 0.25) && back.label == "Clicking “Add”")
    }

    @Test func aCallStillRunningIsNeverIdleAndAScopeSaysWhenItParks() throws {
        var model = OverlayModel()
        model.handle(tap, at: 0)
        model.handle(OverlayEvent(phase: .done), at: 0.3)
        // A build beating every ten seconds for two minutes: neither thinking nor parked.
        for beat in stride(from: 1.0, through: 120, by: 10) {
            model.handle(OverlayEvent(phase: .working, activity: "Building", lingerS: 60, ongoing: true), at: beat)
            let scene = model.scene(at: beat + 9.9)
            #expect(scene.pose != .parked && scene.label == "Building")
        }
        // A done while a call runs is not the end of it: no thinking.
        model.handle(OverlayEvent(phase: .done), at: 112)
        #expect(model.scene(at: 116).label == "Building")
        // A scope that parks after ten seconds, and a call that outlasts that: held until its beats stop.
        var short = OverlayModel()
        short.handle(OverlayEvent(phase: .intent, kind: "tap", points: [[0.5, 0.5]], lingerS: 60, parkAfterS: 10), at: 0)
        short.handle(OverlayEvent(phase: .working, lingerS: 60, ongoing: true, parkAfterS: 10), at: 0)
        #expect(short.scene(at: 12).pose != .parked)
        #expect(short.scene(at: 15).pose == .parked)
        let event = try JSONDecoder().decode(OverlayEvent.self, from: Data(#"{"phase":"working","park_after_ms":45000}"#.utf8))
        #expect(event.parkAfterS == 45)
        let older = try JSONDecoder().decode(OverlayEvent.self, from: Data(#"{"phase":"working"}"#.utf8))
        #expect(older.parkAfterS == OverlayModel.parkAfterS)
    }

    @Test func anActionsLabelIsShownLongEnoughToReadAndTheLatestWins() {
        var model = OverlayModel()
        func tapping(_ name: String) -> OverlayEvent {
            OverlayEvent(phase: .intent, kind: "tap", points: [[0.5, 0.5]], activity: "Tapping “\(name)”", lingerS: 60)
        }
        model.handle(tapping("One"), at: 0)
        #expect(model.scene(at: 0).label == "Tapping “One”")
        model.handle(tapping("Two"), at: 0.1)
        model.handle(tapping("Three"), at: 0.2)
        #expect(model.scene(at: 0.3).label == "Tapping “One”")
        #expect(model.scene(at: 0.4).label == "Tapping “Three”")
        // Thinking gives way to the next action at once.
        model.handle(OverlayEvent(phase: .working, lingerS: 60, ongoing: false), at: 1)
        #expect(model.scene(at: 4).label == OverlayModel.thinking)
        model.handle(tapping("Four"), at: 4.1)
        #expect(model.scene(at: 4.1).label == "Tapping “Four”")
    }

    @Test func whatTheAgentDoesIsSaidThenClearedWhenItsCallEnds() {
        var model = OverlayModel()
        model.handle(OverlayEvent(phase: .working, activity: "Building", agent: "agent-a", ongoing: true), at: 0)
        // No gesture yet: shown parked, saying what it does.
        let building = model.scene(at: 0)
        #expect(building.visible && building.pose == .parked && building.label == "Building")
        // A running call's beats hold everything past the least time.
        #expect(model.scene(at: OverlayModel.activeS + 1).visible)
        model.handle(OverlayEvent(phase: .working, agent: "agent-a", ongoing: true), at: 10)
        #expect(model.scene(at: 10).label == "Building")
        model.handle(OverlayEvent(phase: .working, ongoing: false), at: 11)
        // Said until thinking takes over; with nothing placed on the window, nothing is shown then.
        #expect(model.scene(at: 11).label == "Building")
        #expect(!model.scene(at: 11 + OverlayModel.thinkAfterS).visible)
        model.handle(tap, at: 12)
        model.handle(OverlayEvent(phase: .working, ongoing: true), at: 12.5)
        #expect(model.scene(at: 12.5).label == "Clicking “Add”")
        // Once nothing is said, the pointer's label is the agent's name.
        var named = OverlayModel()
        var silent = tap
        silent.activity = ""
        named.handle(silent, at: 0)
        #expect(named.scene(at: 0).label == "agent-a")
    }

    @Test func aFailureShakesAndEverythingGoesOnceTheAgentIsQuiet() {
        var model = OverlayModel()
        var brief = tap
        brief.lingerS = 0
        model.handle(brief, at: 0)
        model.handle(OverlayEvent(phase: .done, ok: false), at: 0.3)
        #expect(model.scene(at: 0.4).failed)
        #expect(!model.scene(at: 1.3).failed)
        #expect(model.shownUntil == 0.3 + OverlayModel.activeS)
        #expect(!model.scene(at: 0.3 + OverlayModel.activeS).visible)
        #expect(model.scene(at: 0.3 + OverlayModel.activeS).marks.isEmpty)
    }

    @Test func anIntentWithThePointerOffIsNotDrawn() {
        var model = OverlayModel()
        model.handle(OverlayEvent(phase: .intent, kind: "tap", points: [[0.5, 0.5]], activity: "Tapping", pointer: false), at: 0)
        #expect(!model.scene(at: 0).visible)
    }
}

@Suite struct OverlayColorTests {
    @Test func aColourIsSixHexDigitsAndAnythingElseIsTheMacsAccent() throws {
        let violet = try #require(OverlayColor(hex: "#7C5CFF"))
        #expect(violet == OverlayColor(hex: "#7c5cff"))
        #expect(abs(violet.red - 124.0 / 255) < 1e-9 && abs(violet.green - 92.0 / 255) < 1e-9 && violet.blue == 1)
        for bad in ["", "7c5cff", "#7c5cf", "#7c5cfg", "#7c5cff0", "red"] {
            #expect(OverlayColor(hex: bad) == nil)
        }
    }

    @Test func itsLabelIsWhiteUnlessThatWouldNotReadThenNearBlack() {
        #expect(["#0a84ff", "#7c5cff", "#000000", "#1c1c1e"].allSatisfy { OverlayColor(hex: $0)!.whiteText })
        #expect(!["#ffffff", "#ffd60a", "#30d158", "#64d2ff"].contains { OverlayColor(hex: $0)!.whiteText })
    }

    @Test func theScopesColourComesWithEveryEventAndNoneMeansTheAccent() throws {
        let event = try JSONDecoder().decode(OverlayEvent.self, from: Data(##"{"phase":"working","color":"#7c5cff"}"##.utf8))
        #expect(event.color == OverlayColor(hex: "#7c5cff"))
        var model = OverlayModel()
        model.handle(OverlayEvent(phase: .working, activity: "Building", color: OverlayColor(hex: "#7c5cff"), ongoing: true), at: 0)
        #expect(model.scene(at: 0).color == OverlayColor(hex: "#7c5cff"))
        model.handle(OverlayEvent(phase: .working, activity: "Building", ongoing: true), at: 1)
        #expect(model.scene(at: 1).color == nil)
    }
}
