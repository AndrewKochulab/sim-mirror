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
