// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import SimMirrorKit

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
        #expect(
            NodePlacement.match(CGRect(x: 0, y: 0, width: 100, height: 100), in: nodes, threshold: 0.9) == [0, 0])
        #expect(NodePlacement.match(CGRect(x: 0, y: 0, width: 100, height: 100), in: nodes, threshold: 0.99) == [0])
        #expect(NodePlacement.match(CGRect(x: 500, y: 0, width: 1, height: 1), in: nodes, threshold: 0.5) == nil)
        var changed = nodes
        NodePlacement.update(&changed, at: [0, 0]) { $0.label = "changed" }
        NodePlacement.update(&changed, at: []) { $0.label = "never" }
        #expect(changed[0].children[0].label == "changed" && changed[0].label == "outer")
    }
}

@MainActor
struct CommonTagTests {
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

}
