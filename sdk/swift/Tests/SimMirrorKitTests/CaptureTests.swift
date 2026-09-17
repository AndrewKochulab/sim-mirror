// SPDX-License-Identifier: Apache-2.0
import Foundation
import SwiftUI
import Testing
import UIKit

@testable import SimMirrorKit

@MainActor
struct WalkerTests {
    private func walker(budget: Int = 100, redact: Bool = false, hosting: ((UIView) -> Bool)? = nil) -> UIKitWalker {
        let describers = DescriberRegistry()
        let space = UIScreen.main.fixedCoordinateSpace
        guard let hosting else {
            return UIKitWalker(
                describers: describers, redactValues: redact, space: space, budget: NodeBudget(limit: budget))
        }
        return UIKitWalker(
            describers: describers, redactValues: redact, space: space, budget: NodeBudget(limit: budget),
            isHosting: hosting)
    }

    private func label(_ text: String, _ frame: CGRect) -> UILabel {
        let label = UILabel(frame: frame)
        label.text = text
        return label
    }

    @Test func whatShowsIsReadWithScreenFramesAndHollowContainersAreLeftOut() throws {
        let holder = UIView(frame: CGRect(x: 20, y: 100, width: 200, height: 200))
        holder.addSubview(label("Inside", CGRect(x: 10, y: 10, width: 100, height: 20)))
        let hidden = label("Hidden", CGRect(x: 0, y: 0, width: 50, height: 20))
        hidden.isHidden = true
        let faded = label("Faded", CGRect(x: 0, y: 0, width: 50, height: 20))
        faded.alpha = 0
        let window = testWindow(holder, hidden, faded)
        var walker = walker()
        let nodes = walker.walk(window, clip: window.bounds)
        try #require(nodes.count == 1)
        #expect(nodes[0].label == "Inside" && nodes[0].labelSource == .text && nodes[0].kind == .text)
        #expect(nodes[0].frame == Frame(x: 30, y: 110, width: 100, height: 20))
        #expect(nodes[0].typeName == "UILabel" && nodes[0].source == .uikit)
    }

    @Test func aClippedViewIsLeftOutButAnUnclippedOnesChildrenStillShow() {
        let clipping = UIView(frame: CGRect(x: 0, y: 100, width: 100, height: 100))
        clipping.clipsToBounds = true
        clipping.addSubview(label("Clipped", CGRect(x: 200, y: 0, width: 50, height: 20)))
        clipping.addSubview(label("Kept", CGRect(x: 0, y: 0, width: 50, height: 20)))
        let empty = UIView(frame: CGRect(x: 0, y: 300, width: 0, height: 0))
        empty.addSubview(label("Overflowing", CGRect(x: 0, y: 0, width: 80, height: 20)))
        let emptyClipping = UIView(frame: CGRect(x: 0, y: 400, width: 0, height: 0))
        emptyClipping.clipsToBounds = true
        emptyClipping.addSubview(label("Gone", CGRect(x: 0, y: 0, width: 80, height: 20)))
        let offScreen = label("Off", CGRect(x: -500, y: 0, width: 50, height: 20))
        let window = testWindow(clipping, empty, emptyClipping, offScreen)
        var walker = walker()
        #expect(walker.walk(window, clip: window.bounds).map(\.label) == ["Kept", "Overflowing"])
    }

    @Test func aHolderThatSaysSomethingIsKeptWithItsChildrenAndAControlReadsNoChildren() throws {
        let cell = UIView(frame: CGRect(x: 0, y: 100, width: 300, height: 60))
        cell.addGestureRecognizer(UITapGestureRecognizer())
        cell.addSubview(label("Weekly promo", CGRect(x: 10, y: 10, width: 200, height: 20)))
        let button = UIButton(type: .system)
        button.frame = CGRect(x: 0, y: 200, width: 100, height: 44)
        button.setTitle("Save", for: .normal)
        let secure = UITextField(frame: CGRect(x: 0, y: 260, width: 100, height: 44))
        secure.isSecureTextEntry = true
        secure.text = "hunter2"
        let field = UITextField(frame: CGRect(x: 0, y: 320, width: 100, height: 44))
        field.text = "Groceries"
        let window = testWindow(cell, button, secure, field)
        var plain = walker()
        let nodes = plain.walk(window, clip: window.bounds)
        try #require(nodes.map(\.kind) == [.container, .button, .secure, .field])
        #expect(
            nodes[0].interactive && nodes[0].label == "Weekly promo"
                && nodes[0].children.map(\.label) == ["Weekly promo"])
        #expect(nodes[1].children.isEmpty && nodes[1].label == "Save")
        #expect(nodes[2].value == nil && nodes[3].value == "Groceries")
        var redacted = walker(redact: true)
        #expect(redacted.walk(window, clip: window.bounds).last?.value == nil)
    }

    @Test func aModalViewHidesItsSiblings() {
        let behind = label("Behind", CGRect(x: 0, y: 100, width: 100, height: 20))
        let dialog = UIView(frame: CGRect(x: 0, y: 200, width: 300, height: 300))
        dialog.accessibilityViewIsModal = true
        dialog.addSubview(label("In front", CGRect(x: 0, y: 0, width: 100, height: 20)))
        let window = testWindow(behind, dialog)
        var walker = walker()
        #expect(walker.walk(window, clip: window.bounds).map(\.label) == ["In front"])
    }

    @Test func theBudgetAndTheDepthCutTheWalkShort() {
        let labels = (0..<5).map { label("L\($0)", CGRect(x: 0, y: 100 + $0 * 30, width: 100, height: 20)) }
        let window = testWindow(labels[0], labels[1], labels[2], labels[3], labels[4])
        var small = walker(budget: 3)
        #expect(small.walk(window, clip: window.bounds).count == 3 && small.budget.exhausted && !small.cut)
        var root = UIView(frame: CGRect(x: 0, y: 0, width: 300, height: 300))
        let top = root
        for _ in 0..<(UIKitWalker.maxDepth + 2) {
            let child = UIView(frame: root.bounds)
            root.addSubview(child)
            root = child
        }
        root.addSubview(label("Deep", CGRect(x: 0, y: 0, width: 100, height: 20)))
        let deepWindow = testWindow(top)
        var deep = walker()
        #expect(deep.walk(deepWindow, clip: deepWindow.bounds).isEmpty && deep.cut)
    }

    @Test func tagsAndPlatformViewsInsideHostingViewsAreNoted() throws {
        let host = UIView(frame: CGRect(x: 0, y: 0, width: 400, height: 800))
        let anchor = TagAnchorView(tag: Tag(kind: .button, name: "Delete"))
        anchor.frame = CGRect(x: 10, y: 100, width: 44, height: 44)
        let hiddenAnchor = TagAnchorView(tag: Tag(name: "Offscreen"))
        hiddenAnchor.frame = CGRect(x: -100, y: 0, width: 10, height: 10)
        host.addSubview(anchor)
        host.addSubview(hiddenAnchor)
        let outsideAnchor = TagAnchorView(tag: Tag(name: "Outside"))
        outsideAnchor.frame = CGRect(x: 10, y: 850, width: 20, height: 20)
        let window = testWindow(host, outsideAnchor)
        var walker = walker(hosting: { $0 === host })
        #expect(walker.walk(window, clip: window.bounds).isEmpty)
        try #require(walker.tags.map(\.0) == [Tag(kind: .button, name: "Delete"), Tag(name: "Outside")])
        #expect(walker.tags[0].1 == CGRect(x: 10, y: 100, width: 44, height: 44))
        try #require(walker.hostings.count == 1)
        #expect(walker.hostings[0].view === host)
        #expect(
            walker.hostings[0].platformViews == [
                CGRect(x: 10, y: 100, width: 44, height: 44), CGRect(x: -100, y: 0, width: 10, height: 10),
            ])
    }
}

@MainActor
struct TagTests {
    @Test func aTagNamesTheNodeInItsPlaceOrIsAddedWhereNoneIs() throws {
        let icon = testNode(.button, frame: CGRect(x: 0, y: 0, width: 44, height: 44))
        let card = testNode(frame: CGRect(x: 0, y: 100, width: 300, height: 200), children: [])
        let merged = TagMerger.merge(
            [
                (Tag(name: "Delete", identifier: "delete"), CGRect(x: 1, y: 1, width: 43, height: 43)),
                (Tag(kind: .button, name: "Open"), CGRect(x: 10, y: 120, width: 100, height: 40)),
                (Tag(kind: .text), CGRect(x: 500, y: 500, width: 10, height: 10)),
                (Tag(name: "Plain"), CGRect(x: 500, y: 700, width: 10, height: 10)),
            ], into: [icon, card])
        try #require(merged.count == 4 && merged[1].children.count == 1)
        #expect(merged[3].kind == .container && merged[3].label == "Plain" && !merged[3].interactive)
        #expect(merged[0].label == "Delete" && merged[0].labelSource == .tag && merged[0].identifier == "delete")
        let added = merged[1].children[0]
        #expect(added.kind == .button && added.label == "Open" && added.interactive && added.source == .tag)
        #expect(added.typeName == TagMerger.typeName)
        #expect(merged[2].kind == .text && merged[2].label == nil && !merged[2].interactive)
    }

    @Test func aTagsKindMakesANodeTappableButNeverTakesItAway() {
        var node = testNode(.container, label: "Card")
        TagMerger.apply(Tag(kind: .text), to: &node)
        #expect(node.kind == .text && !node.interactive && node.label == "Card")
        node.interactive = true
        TagMerger.apply(Tag(kind: .text), to: &node)
        #expect(node.interactive)
        TagMerger.apply(Tag(kind: .switch), to: &node)
        #expect(node.kind == .switch && node.interactive)
    }

    struct Tagged: View {
        let name: String

        var body: some View {
            VStack(spacing: 0) {
                Color.clear.frame(width: 100, height: 120)
                Color.red.frame(width: 60, height: 40).simMirror(kind: .button, name: name, identifier: "delete")
                Color.blue.frame(width: 30, height: 30).simMirror("Named")
            }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }

    private func anchors(in view: UIView) -> [TagAnchorView] {
        (view as? TagAnchorView).map { [$0] } ?? view.subviews.flatMap { anchors(in: $0) }
    }

    @Test func theModifierPutsAnAnchorBehindTheViewInItsExactPlace() throws {
        let controller = UIHostingController(rootView: Tagged(name: "Delete"))
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = controller
        window.isHidden = false
        controller.view.layoutIfNeeded()
        let found = anchors(in: window)
        #expect(
            found.map(\.mirrorTag) == [Tag(kind: .button, name: "Delete", identifier: "delete"), Tag(name: "Named")])
        let first = try #require(found.first)
        #expect(first.convert(first.bounds, to: window).size == CGSize(width: 60, height: 40))
        #expect(!first.isUserInteractionEnabled && first.accessibilityElementsHidden && !first.isAccessibilityElement)
        controller.rootView = Tagged(name: "Remove")
        controller.view.layoutIfNeeded()
        #expect(anchors(in: window).first?.mirrorTag.name == "Remove")
    }
}

@MainActor
struct PlacementTests {
    @Test func overlapIsIntersectionOverUnion() {
        let square = CGRect(x: 0, y: 0, width: 10, height: 10)
        #expect(NodePlacement.overlap(square, square) == 1)
        #expect(NodePlacement.overlap(square, CGRect(x: 5, y: 0, width: 10, height: 10)) == 50.0 / 150.0)
        #expect(NodePlacement.overlap(square, CGRect(x: 20, y: 20, width: 5, height: 5)) == 0)
        #expect(NodePlacement.overlap(.zero, .zero) == 0)
    }

    @Test func theDeepestThenClosestMatchWins() {
        let inner = testNode(.button, label: "inner", frame: CGRect(x: 0, y: 0, width: 100, height: 95))
        let outer = testNode(
            .cell, label: "outer", frame: CGRect(x: 0, y: 0, width: 100, height: 100), children: [inner])
        let twin = testNode(.cell, label: "twin", frame: CGRect(x: 0, y: 0, width: 100, height: 100))
        let nodes = [outer, twin]
        #expect(NodePlacement.match(CGRect(x: 0, y: 0, width: 100, height: 100), in: nodes, threshold: 0.9) == [0, 0])
        #expect(NodePlacement.match(CGRect(x: 0, y: 0, width: 100, height: 100), in: nodes, threshold: 0.99) == [0])
        #expect(NodePlacement.match(CGRect(x: 500, y: 0, width: 1, height: 1), in: nodes, threshold: 0.5) == nil)
        var changed = nodes
        NodePlacement.update(&changed, at: [0, 0]) { $0.label = "changed" }
        NodePlacement.update(&changed, at: []) { $0.label = "never" }
        #expect(changed[0].children[0].label == "changed" && changed[0].label == "outer")
    }
}
