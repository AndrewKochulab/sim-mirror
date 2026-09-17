// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import CoreGraphics
    import Foundation

    /// Whether SwiftUI's debug data is read. It is asked for, and on iOS 26 only: it is SwiftUI's own undocumented
    /// debugging aid, slow on its first read, and reading it stops the app on iOS 27.
    enum DebugDataPolicy: Equatable {
        case off
        case on
        case refused(String)

        /// The environment variable SwiftUI records its debug data under, and the properties it records: a view's
        /// type, value, position and size.
        static let variable = "SWIFTUI_VIEW_DEBUG"
        static let properties = "27"
        static let supportedMajor = 26

        static func decide(requested: Bool, os: OperatingSystemVersion) -> DebugDataPolicy {
            guard requested else { return .off }
            guard os.majorVersion == supportedMajor else {
                return .refused(
                    "SwiftUI's debug data is read on iOS \(supportedMajor) only, not iOS \(os.majorVersion).\(os.minorVersion)"
                )
            }
            return .on
        }
    }

    /// A SwiftUI view as its debug data describes it.
    struct DebugNode: Equatable {
        var type: String
        var position: CGPoint?
        var size: CGSize?
        /// A `Text`'s string, when it is a plain one rather than a format.
        var text: String?
        /// An `Image`'s asset or SF Symbol name.
        var imageName: String?
        var children: [DebugNode] = []

        var frame: CGRect? {
            guard let position, let size else { return nil }
            return CGRect(origin: position, size: size)
        }
    }

    /// Reads SwiftUI's serialized debug data, and says so when its shape is not one it knows.
    enum DebugDataDecoder {
        enum Decoded: Equatable {
            case nodes([DebugNode])
            /// SwiftUI recorded nothing: the variable was set too late, or not at all.
            case empty
            case unsupported
        }

        static func decode(_ data: Data?) -> Decoded {
            guard let data, let roots = try? JSONSerialization.jsonObject(with: data) as? [Any] else {
                return .unsupported
            }
            guard !roots.isEmpty else { return .empty }
            let nodes = roots.compactMap { node($0) }
            return nodes.count == roots.count ? .nodes(nodes) : .unsupported
        }

        private static func node(_ raw: Any) -> DebugNode? {
            guard let object = raw as? [String: Any], let properties = object["properties"] as? [[String: Any]] else {
                return nil
            }
            var attributes: [Int: [String: Any]] = [:]
            for property in properties {
                if let id = property["id"] as? Int, let attribute = property["attribute"] as? [String: Any] {
                    attributes[id] = attribute
                }
            }
            guard let type = attributes[0]?["readableType"] as? String else { return nil }
            var node = DebugNode(type: type)
            node.position = pair(attributes[3]?["value"]).map { CGPoint(x: $0.0, y: $0.1) }
            node.size = pair(attributes[4]?["value"]).map { CGSize(width: $0.0, height: $0.1) }
            let value = attributes[1]?["subattributes"] as? [[String: Any]] ?? []
            if type == "Text" { node.text = plainText(in: value) }
            if type == "Image" { node.imageName = imageName(in: value) }
            let children = object["children"] as? [Any] ?? []
            node.children = children.compactMap { self.node($0) }
            return node.children.count == children.count ? node : nil
        }

        private static func pair(_ value: Any?) -> (Double, Double)? {
            guard let numbers = value as? [NSNumber], numbers.count == 2 else { return nil }
            return (numbers[0].doubleValue, numbers[1].doubleValue)
        }

        /// The first group of attributes, however deep, that answers `read`.
        private static func search(_ attributes: [[String: Any]], _ read: ([String: [String: Any]]) -> String?)
            -> String?
        {
            var named: [String: [String: Any]] = [:]
            for attribute in attributes {
                if let name = attribute["name"] as? String { named[name] = attribute }
            }
            if let found = read(named) { return found }
            for attribute in attributes {
                if let inner = attribute["subattributes"] as? [[String: Any]], let found = search(inner, read) {
                    return found
                }
            }
            return nil
        }

        static func plainText(in attributes: [[String: Any]]) -> String? {
            search(attributes) { named in
                guard named["hasFormatting"]?["value"] as? Bool == false else { return nil }
                return nonEmpty(named["key"]?["value"] as? String)
            }
        }

        static func imageName(in attributes: [[String: Any]]) -> String? {
            search(attributes) { named in
                guard named["location"] != nil else { return nil }
                return nonEmpty(named["name"]?["value"] as? String)
            }
        }
    }

    /// Something SwiftUI's debug data showed that UIKit could not: a view with a tap gesture, or an image's name.
    struct DebugFinding: Equatable {
        enum Kind: Equatable {
            case tap
            case image
        }

        var kind: Kind
        var label: String?
        var labelSource: LabelSource?
        var frame: CGRect
    }

    /// Places what the debug data found on the screen.
    ///
    /// SwiftUI records each view's position in the space of the layout it belongs to, not the screen's -- the content
    /// of a scroll view starts again at zero. The platform views SwiftUI placed, whose screen frames UIKit knows
    /// exactly, pin each space down: a view in the debug data the size of exactly one of them gives the space's offset.
    /// A space with no such view, or whose views disagree, is left out: its frames would be guesses.
    enum DebugDataPlacer {
        /// How far, in points, sizes and offsets may differ and still be the same.
        static let slack: CGFloat = 1

        static let tapTypes: Set<String> = ["TapGestureModifier"]

        private struct Placed {
            let node: DebugNode
            let frame: CGRect
            let space: Int
        }

        static func findings(in roots: [DebugNode], platformViews: [CGRect], within bounds: CGRect) -> [DebugFinding] {
            var placed: [Placed] = []
            var spaces = 0
            func visit(_ node: DebugNode, parent: CGRect?, space: Int) {
                var space = space
                var parent = parent
                if let frame = node.frame {
                    if let outer = parent, !outer.insetBy(dx: -slack, dy: -slack).contains(frame) {
                        spaces += 1
                        space = spaces
                    }
                    placed.append(Placed(node: node, frame: frame, space: space))
                    parent = frame
                }
                for child in node.children { visit(child, parent: parent, space: space) }
            }
            for root in roots { visit(root, parent: nil, space: 0) }
            let offsets = spaceOffsets(placed, platformViews: platformViews)
            var found: [DebugFinding] = []
            for item in placed {
                guard let offset = offsets[item.space], item.frame.width > 0, item.frame.height > 0 else { continue }
                let frame = item.frame.offsetBy(dx: offset.x, dy: offset.y)
                guard bounds.insetBy(dx: -slack, dy: -slack).contains(frame) else { continue }
                if tapTypes.contains(item.node.type) {
                    let named = name(inside: item.node)
                    found.append(DebugFinding(kind: .tap, label: named?.0, labelSource: named?.1, frame: frame))
                } else if let image = item.node.imageName {
                    found.append(DebugFinding(kind: .image, label: image, labelSource: .image, frame: frame))
                }
            }
            return found
        }

        private static func spaceOffsets(_ placed: [Placed], platformViews: [CGRect]) -> [Int: CGPoint] {
            var candidates: [Int: [CGPoint]] = [:]
            for item in placed {
                let matches = platformViews.filter {
                    abs($0.width - item.frame.width) <= slack / 2 && abs($0.height - item.frame.height) <= slack / 2
                }
                guard matches.count == 1, item.frame.width > 0, item.frame.height > 0 else { continue }
                let offset = CGPoint(x: matches[0].minX - item.frame.minX, y: matches[0].minY - item.frame.minY)
                candidates[item.space, default: []].append(offset)
            }
            return candidates.compactMapValues { offsets in
                let first = offsets[0]
                let agree = offsets.allSatisfy { abs($0.x - first.x) <= slack && abs($0.y - first.y) <= slack }
                return agree ? first : nil
            }
        }

        /// What a tapped view says: the first plain text inside it, else the first image's name.
        static func name(inside node: DebugNode) -> (String, LabelSource)? {
            var texts: [String] = []
            var images: [String] = []
            func visit(_ node: DebugNode) {
                if let text = node.text { texts.append(text) }
                if let image = node.imageName { images.append(image) }
                node.children.forEach(visit)
            }
            visit(node)
            if let text = texts.first { return (text, .descendants) }
            return images.first.map { ($0, .image) }
        }
    }

    /// Puts what the debug data found into a window's nodes.
    enum DebugFindingMerger {
        /// How much a finding and a node must overlap to be the same view.
        static let overlap = 0.9
        static let tapTypeName = "SwiftUI.TapGesture"

        static func merge(_ findings: [DebugFinding], into nodes: [Node]) -> [Node] {
            var nodes = nodes
            var added: [CGRect] = []
            for finding in findings {
                if let path = NodePlacement.match(finding.frame, in: nodes, threshold: overlap) {
                    NodePlacement.update(&nodes, at: path) { node in
                        if finding.kind == .tap { node.interactive = true }
                        if node.label == nil, let label = finding.label {
                            node.label = label
                            node.labelSource = finding.labelSource
                        }
                    }
                } else if finding.kind == .tap,
                    !added.contains(where: { NodePlacement.overlap($0, finding.frame) >= overlap })
                {
                    added.append(finding.frame)
                    let node = Node(
                        kind: .button, label: finding.label, labelSource: finding.labelSource,
                        frame: Frame(finding.frame), interactive: true, source: .swiftui, typeName: tapTypeName
                    )
                    NodePlacement.insert(node, into: &nodes)
                }
            }
            return nodes
        }
    }
#endif
