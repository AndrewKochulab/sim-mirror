// SPDX-License-Identifier: Apache-2.0
import Foundation
import SwiftUI
import Testing
import UIKit

@testable import SimMirrorKit

@MainActor
struct CapturerTests {
    static let app = AppInfo(
        bundleID: "io.github.andrewkochulab.simmirror.tests", name: "Tests", pid: 42, active: false)

    private func capturer(debugData: DebugDataPolicy = .off, redact: Bool = false) -> HierarchyCapturer {
        var ticks = [0.5, 0.5042]
        return HierarchyCapturer(
            app: Self.app, describers: DescriberRegistry(), redactValues: redact, debugData: debugData,
            clock: { ticks.isEmpty ? 0 : ticks.removeFirst() })
    }

    private func label(_ text: String, y: CGFloat) -> UILabel {
        let label = UILabel(frame: CGRect(x: 20, y: y, width: 200, height: 20))
        label.text = text
        return label
    }

    @Test func aCaptureSaysTheAppTheScreenAndTheWindowsFromTheFront() throws {
        let back = testWindow(label("Back", y: 100))
        let front = testWindow(label("Front", y: 200))
        front.windowLevel = .alert
        let keyboard = CGRect(x: 0, y: 500, width: 402, height: 374)
        let hierarchy = capturer().capture(windows: [back, front], keyboard: keyboard, maxNodes: 50)
        #expect(hierarchy.protocolVersion == 1 && hierarchy.sdkVersion == SimMirror.sdkVersion)
        #expect(hierarchy.app == AppInfo(bundleID: Self.app.bundleID, name: "Tests", pid: 42, active: true))
        let bounds = UIScreen.main.fixedCoordinateSpace.bounds
        #expect(hierarchy.screen.widthPt == Double(bounds.width) && hierarchy.screen.heightPt == Double(bounds.height))
        #expect(hierarchy.screen.scale == Double(UIScreen.main.scale) && hierarchy.screen.orientation == .unknown)
        try #require(hierarchy.windows.map { $0.nodes.map(\.label) } == [["Front"], ["Back"]])
        #expect(hierarchy.windows[0].level == Double(UIWindow.Level.alert.rawValue) && !hierarchy.windows[0].key)
        #expect(hierarchy.keyboard == Keyboard(frame: Frame(keyboard)) && hierarchy.modal == nil)
        #expect(
            hierarchy.nodeCount == 2 && !hierarchy.truncated && hierarchy.captureMs == 4.2 && hierarchy.notes.isEmpty)
        let none = capturer().capture(windows: [], keyboard: nil, maxNodes: 1)
        #expect(none.windows.isEmpty && none.keyboard == nil)
    }

    @Test func aBudgetTooSmallSaysTheAnswerWasCutShort() {
        let window = testWindow(label("One", y: 100), label("Two", y: 140))
        let hierarchy = capturer().capture(windows: [window], keyboard: nil, maxNodes: 1)
        #expect(hierarchy.nodeCount == 1 && hierarchy.truncated)
    }

    final class KeyboardWindow: UIWindow {}
    final class TextEffectsWindow: UIWindow {}

    @Test func windowsAreReadKeyFirstThenFromTheFrontLeavingTheKeyboardsOwnOut() {
        let low = UIWindow()
        low.isHidden = false
        let high = UIWindow()
        high.isHidden = false
        high.windowLevel = .statusBar
        let later = UIWindow()
        later.isHidden = false
        let hidden = UIWindow()
        let faded = UIWindow()
        faded.isHidden = false
        faded.alpha = 0
        let keyboard = KeyboardWindow()
        keyboard.isHidden = false
        let effects = TextEffectsWindow()
        effects.isHidden = false
        let ordered = HierarchyCapturer.ordered([low, high, hidden, faded, keyboard, effects, later])
        #expect(ordered.map(ObjectIdentifier.init) == [high, later, low].map(ObjectIdentifier.init))
    }

    @Test func orientationsAreSaidInTheProtocolsWords() {
        #expect(HierarchyCapturer.orientation(.portrait) == .portrait)
        #expect(HierarchyCapturer.orientation(.portraitUpsideDown) == .portraitUpsideDown)
        #expect(HierarchyCapturer.orientation(.landscapeLeft) == .landscapeLeft)
        #expect(HierarchyCapturer.orientation(.landscapeRight) == .landscapeRight)
        #expect(HierarchyCapturer.orientation(.unknown) == .unknown)
    }

    @Test func modalsAreNamedAndKindedByHowTheyArePresented() {
        let alert = UIAlertController(title: "Delete?", message: nil, preferredStyle: .alert)
        #expect(ModalDetector.kind(of: alert) == .alert && ModalDetector.name(of: alert) == "Delete?")
        let styles: [(UIModalPresentationStyle, ModalKind)] = [
            (.fullScreen, .fullScreen), (.overFullScreen, .fullScreen), (.currentContext, .fullScreen),
            (.overCurrentContext, .fullScreen), (.pageSheet, .sheet), (.formSheet, .sheet), (.automatic, .sheet),
            (.popover, .sheet),
        ]
        for (style, kind) in styles {
            let controller = UIViewController()
            controller.modalPresentationStyle = style
            #expect(ModalDetector.kind(of: controller) == kind)
        }
        let listed = UIViewController()
        listed.navigationItem.title = "Settings"
        #expect(ModalDetector.name(of: UINavigationController(rootViewController: listed)) == "Settings")
        #expect(ModalDetector.name(of: UIViewController()) == nil)
        #expect(ModalDetector.detect(in: UIWindow()) == nil)
        let plain = UIWindow()
        plain.rootViewController = UIViewController()
        #expect(ModalDetector.detect(in: plain) == nil)
    }

    /// A view controller that says what it presents, so a test needs no real presentation.
    final class Presenting: UIViewController {
        var shown: UIViewController?
        var leaving = false
        override var presentedViewController: UIViewController? { shown }
        override var isBeingDismissed: Bool { leaving }
    }

    @Test func aModalIsReadAloneAndWindowsBehindItAreLeftOut() throws {
        let root = Presenting()
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = root
        window.isHidden = false
        root.view.addSubview(label("Behind", y: 100))
        let navigation = Presenting()
        let sheet = Presenting()
        sheet.title = "Filters"
        sheet.view.frame = window.bounds
        sheet.view.addSubview(label("In the sheet", y: 400))
        window.addSubview(sheet.view)
        window.insertSubview(navigation.view, belowSubview: sheet.view)
        root.shown = navigation
        navigation.shown = sheet
        let behind = testWindow(label("Other window", y: 300))
        behind.windowLevel = UIWindow.Level(rawValue: -1)
        let front = testWindow(label("Front", y: 500))
        front.windowLevel = .alert
        let hierarchy = capturer().capture(windows: [behind, window, front], keyboard: nil, maxNodes: 50)
        #expect(hierarchy.modal == Modal(kind: .sheet, name: "Filters"))
        try #require(hierarchy.windows.count == 2)
        #expect(hierarchy.windows.map { $0.nodes.map(\.label) } == [["Front"], ["In the sheet"]])
        sheet.leaving = true
        #expect(ModalDetector.detect(in: window)?.modal == Modal(kind: .sheet))
        navigation.leaving = true
        #expect(ModalDetector.detect(in: window) == nil)
        navigation.leaving = false
        sheet.leaving = false
        sheet.view.removeFromSuperview()
        #expect(ModalDetector.detect(in: window) == nil)
    }

    @Test(.enabled(if: ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 17))
    func aPopoverIsOneWhereThereIsRoomForIt() throws {
        guard #available(iOS 17, *) else { return }
        let popover = UIViewController()
        popover.modalPresentationStyle = .popover
        popover.traitOverrides.horizontalSizeClass = .regular
        popover.updateTraitsIfNeeded()
        #expect(ModalDetector.kind(of: popover) == .popover)
    }

    /// A hosting view whose debug data a test chooses.
    final class FakeHosting: UIView, DebugDataHosting {
        var data: Data?
        func serializedDebugData() -> Data? { data }
    }

    @Test func hostingViewsWhoseDebugDataIsEmptyOrUnknownAreSaidOnceEach() {
        let empty = FakeHosting(frame: CGRect(x: 0, y: 100, width: 100, height: 100))
        empty.data = Data("[]".utf8)
        let again = FakeHosting(frame: CGRect(x: 0, y: 300, width: 100, height: 100))
        again.data = Data("[]".utf8)
        let unknown = FakeHosting(frame: CGRect(x: 0, y: 500, width: 100, height: 100))
        let window = testWindow(empty, again, unknown)
        let other = FakeHosting(frame: CGRect(x: 0, y: 100, width: 100, height: 100))
        let hierarchy = capturer(debugData: .on).capture(
            windows: [window, testWindow(other)], keyboard: nil, maxNodes: 50)
        #expect(hierarchy.notes == [DebugDataReader.unsupported, DebugDataReader.empty])
        #expect(DebugDataReader.read([HostingFound(view: UIView(), frame: .zero)]).notes.isEmpty)
        #expect(DebugDataReader.isHosting(empty) && !DebugDataReader.isHosting(UIView()))
    }

    @Test func refusedDebugDataIsSaidAsANote() {
        let hierarchy = capturer(debugData: .refused("not here")).capture(windows: [], keyboard: nil, maxNodes: 1)
        #expect(hierarchy.notes == ["not here"])
    }

    struct Probe: View {
        var body: some View {
            VStack(spacing: 24) {
                Toggle("", isOn: .constant(true)).labelsHidden()
                VStack {
                    Image(systemName: "sun.max")
                    Text("Daily mix")
                }
                .frame(width: 200, height: 80)
                .onTapGesture {}
            }
        }
    }

    /// SwiftUI's own debug data, read from a real hosting view: the shape this SDK knows must still be SwiftUI's.
    @Test(.enabled(if: ProcessInfo.processInfo.operatingSystemVersion.majorVersion == DebugDataPolicy.supportedMajor))
    func onIOS26SwiftUIsDebugDataFindsATappedViewAndItsName() throws {
        #expect(ProcessInfo.processInfo.environment[DebugDataPolicy.variable] == DebugDataPolicy.properties)
        let controller = UIHostingController(rootView: Probe())
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = controller
        window.isHidden = false
        controller.view.layoutIfNeeded()
        let hierarchy = capturer(debugData: .on).capture(windows: [window], keyboard: nil, maxNodes: 500)
        let nodes = hierarchy.windows.flatMap(\.nodes)
        func all(_ nodes: [Node]) -> [Node] { nodes.flatMap { [$0] + all($0.children) } }
        let tapped = try #require(all(nodes).first { $0.source == .swiftui })
        #expect(tapped.kind == .button && tapped.interactive && tapped.label == "Daily mix")
        #expect(tapped.frame.width == 200 && tapped.frame.height == 80)
        #expect(all(nodes).contains { $0.kind == .switch })
        #expect(hierarchy.notes.isEmpty)
    }
}
