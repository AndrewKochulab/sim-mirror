// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import SwiftUI
    import UIKit

    /// What a `.simMirror(...)` modifier says of a view.
    struct Tag: Equatable, Sendable {
        var kind: SimMirror.Kind?
        var name: String?
        var identifier: String?
    }

    /// Put behind a tagged view, where it takes the view's exact place on screen in UIKit's own hierarchy -- in a
    /// sheet, a list row or a cell alike -- without being seen, touched or read by accessibility.
    struct TagAnchor: UIViewRepresentable {
        let tag: Tag

        func makeUIView(context: Context) -> TagAnchorView {
            TagAnchorView(tag: tag)
        }

        func updateUIView(_ view: TagAnchorView, context: Context) {
            view.mirrorTag = tag
        }
    }

    final class TagAnchorView: UIView {
        var mirrorTag: Tag

        init(tag: Tag) {
            mirrorTag = tag
            super.init(frame: .zero)
            isUserInteractionEnabled = false
            isAccessibilityElement = false
            accessibilityElementsHidden = true
            backgroundColor = .clear
        }

        @available(*, unavailable)
        required init?(coder: NSCoder) {
            fatalError("TagAnchorView is made in code only")
        }
    }

    /// Puts what the tags say into a window's nodes: a tag names the view in its place, or is added as a node of its
    /// own where no view is.
    enum TagMerger {
        /// How much a tag and a node must overlap for the tag to be about that node.
        static let overlap = 0.85
        static let typeName = "SimMirrorKit.Tag"

        static func merge(_ tags: [(Tag, CGRect)], into nodes: [Node]) -> [Node] {
            var nodes = nodes
            for (tag, rect) in tags {
                if let path = NodePlacement.match(rect, in: nodes, threshold: overlap) {
                    NodePlacement.update(&nodes, at: path) { apply(tag, to: &$0) }
                } else {
                    let kind = tag.kind ?? .container
                    let node = Node(
                        kind: kind, label: tag.name, labelSource: .tag, identifier: tag.identifier, frame: Frame(rect),
                        interactive: kind.isControl, source: .tag, typeName: typeName
                    )
                    NodePlacement.insert(node, into: &nodes)
                }
            }
            return nodes
        }

        static func apply(_ tag: Tag, to node: inout Node) {
            if let kind = tag.kind {
                node.kind = kind
                node.interactive = node.interactive || kind.isControl
            }
            if let name = tag.name {
                node.label = name
                node.labelSource = .tag
            }
            if let identifier = tag.identifier { node.identifier = identifier }
        }
    }
#endif
