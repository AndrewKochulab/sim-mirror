// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct MacWindowTests {
    @Test func onlyOneProcessAndOptionalWindowAreAccepted() throws {
        #expect(try Command.parse(["mac-apps"]) == .macApps)
        #expect(try Command.parse(["window", "--pid", "42", "--socket", "/tmp/app", "--window-id", "7", "--parent-pid", "99"])
            == .window(MacWindowOptions(pid: 42, socket: "/tmp/app", windowID: 7, parentPid: 99)))
        #expect(try Command.parse(["window", "--pid", "42", "--socket", "/tmp/app"])
            == .window(MacWindowOptions(pid: 42, socket: "/tmp/app", windowID: nil, parentPid: nil)))
        #expect(try Command.parse(["window", "--pid", "42", "--socket", "/tmp/app", "--accessibility", "off"])
            == .window(MacWindowOptions(pid: 42, socket: "/tmp/app", windowID: nil, parentPid: nil, accessibility: false)))
        for args in [["window", "--pid", "42", "--socket", "/tmp/app", "--accessibility", "maybe"], ["window"], ["window", "--pid", "1"], ["window", "--pid", "42"],
                     ["window", "--pid", "42", "--socket", "relative"],
                     ["window", "--pid", "42", "--socket", "/tmp/app", "--window-id", "0"],
                     ["window", "--pid", "42", "--socket", "/tmp/app", "--display", "1"]] {
            #expect(throws: HelperFailure.self) { try Command.parse(args) }
        }
    }
    @Test func menuTitlesMatchTheWordsBeforeAShortcutHint() {
        #expect(MenuTitle.matches("Library", "Library"))
        #expect(MenuTitle.matches("Library     g l", "Library"))
        #expect(MenuTitle.matches("Find\t⌘K", "Find"))
        #expect(MenuTitle.matches("Library     g l", "Library     g l"))
        #expect(MenuTitle.matches("Show sidebar  ⌘B", "Show sidebar"))
        #expect(!MenuTitle.matches("Libraries", "Library"))
        #expect(!MenuTitle.matches("Go to Library", "Library"))
        #expect(!MenuTitle.matches("", "Library"))
        #expect(MenuTitle.shown(" Zoom in ") == "Zoom in")
    }
    @Test func semanticCommandsAreBoundedAndDecodedBeforeRouting() throws {
        let valid: [MacAction] = [.init(kind: .tap, x: 0, y: 4), .init(kind: .rightClick, x: 2, y: 2),
            .init(kind: .hover, x: 1, y: 1), .init(kind: .scroll, x: 1, y: 1, dx: -10000, dy: 10000),
            .init(kind: .type, text: "Hello"), .init(kind: .keys, text: "cmd+s"), .init(kind: .menu, path: ["File", "Save"]),
            .init(kind: .window, windowID: 4), .init(kind: .info)]
        for action in valid {
            try action.validate()
            let data = JSON.encode(action)
            let raw = Data("{\"op\":\"mac\",\"action\":".utf8) + data + Data("}".utf8)
            let decoded = try Request.decode(raw)
            #expect(decoded == .mac(action))
            #expect(decoded.inOrder)
        }
        let invalid: [MacAction] = [.init(kind: .tap), .init(kind: .tap, x: .infinity, y: 0),
            .init(kind: .tap, x: -1, y: 0), .init(kind: .scroll, x: 1, y: 1),
            .init(kind: .scroll, x: 1, y: 1, dx: 0, dy: 10001), .init(kind: .type), .init(kind: .type, text: ""),
            .init(kind: .keys, text: "\0"), .init(kind: .type, text: String(repeating: "x", count: 2001)),
            .init(kind: .menu), .init(kind: .menu, path: []), .init(kind: .menu, path: [""]),
            .init(kind: .window), .init(kind: .window, windowID: 0)]
        for action in invalid { #expect(throws: HelperFailure.self) { try action.validate() } }
        #expect(throws: HelperFailure.self) { try Request.decode(Data(#"{"op":"mac"}"#.utf8)) }
    }
    @Test func webControlsWinOverLaterStructuralSiblingsAndWalksStayBounded() {
        let rect = Rect(x: 0, y: 0, width: 100, height: 100)
        let tree = ["window": ["web", "overlay"], "web": ["button"], "button": ["window"]]
        #expect(MacHitSearch.deepest("window", x: 20, y: 20, bounds: { _ in rect }, children: { tree[$0] ?? [] }) == "button")
        #expect(MacHitSearch.deepest("window", x: 200, y: 20, bounds: { _ in rect }, children: { tree[$0] ?? [] }) == nil)
        #expect(MacHitSearch.deepest("window", x: 20, y: 20, bounds: { _ in nil }, children: { tree[$0] ?? [] }) == nil)
        #expect(MacHitSearch.deepest(0, x: 20, y: 20, bounds: { _ in rect }, children: { [$0 + 1] }) == 39)
        #expect(MacHitSearch.deepest(0, x: 20, y: 20, bounds: { _ in rect }, children: { $0 == 0 ? Array(1...4000) : [] }) == 2999)
    }
    @Test func nonMacDevicesRefuseSemanticInput() async {
        let sent = Sent()
        await Router(device: FakeDevice()).answer(1, .mac(.init(kind: .info))) { await sent.add($0) }
        let frames = await sent.frames
        #expect(frames.count == 1)
        #expect(frames[0].kind == .failure)
        #expect(text(frames[0]).contains("not a macOS app"))
    }
}

@Suite struct MacWindowChoiceTests {
    func window(_ id: UInt32, pid: Int32 = 42, layer: Int = 0, _ w: Double, _ h: Double, on: Bool = true) -> MacWindowCandidate {
        MacWindowCandidate(id: id, pid: pid, layer: layer, width: w, height: h, onScreen: on)
    }
    @Test func menuBarStripsAloneAreNoWindow() {
        // What a CEF app owned while its main window waited on a Keychain prompt.
        let strips = (1...4).map { window($0, 1728, 33, on: false) }
        #expect(MacWindowChoice.choose(strips, pid: 42, wanted: nil) == nil)
        #expect(MacWindowChoice.choose(strips + [window(9, 1728, 33)], pid: 42, wanted: nil) == nil)
    }
    @Test func theLargestRealWindowOfTheProcessIsChosen() {
        let main = window(5, 1200, 800)
        let windows = [window(1, 1728, 33, on: false), window(2, layer: 3, 1600, 1000), window(3, pid: 7, 1700, 1100),
                       window(4, 1800, 1100, on: false), window(6, 400, 300), main, window(7, 63, 900)]
        #expect(MacWindowChoice.choose(windows, pid: 42, wanted: nil) == main)
        #expect(MacWindowChoice.eligible(window(8, 64, 64), pid: 42))
        #expect(!MacWindowChoice.eligible(window(8, 900, 63), pid: 42))
    }
    @Test func aWindowAskedForNeedOnlyBelongToTheProcess() {
        let hidden = window(4, 1800, 1100, on: false)
        let windows = [hidden, window(3, pid: 7, 1700, 1100), window(2, 1, 1)]
        #expect(MacWindowChoice.choose(windows, pid: 42, wanted: 4) == hidden)
        #expect(MacWindowChoice.choose(windows, pid: 42, wanted: 3) == nil)
        #expect(MacWindowChoice.choose(windows, pid: 42, wanted: 2) == nil)
        #expect(MacWindowChoice.choose(windows, pid: 42, wanted: 9) == nil)
    }
}
