// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import UIKit

    /// A SwiftUI hosting view met on the walk, with the platform views inside it: UIKit views SwiftUI placed, whose
    /// frames are known exactly.
    struct HostingFound {
        let view: UIView
        let frame: CGRect
        var platformViews: [CGRect] = []
    }

    /// Walks a window's views into nodes: what can be seen, described, labeled, within a budget.
    @MainActor
    struct UIKitWalker {
        /// How deep the walk goes; views deeper are left out and the answer says it was cut short.
        static let maxDepth = 128

        let describers: DescriberRegistry
        let redactValues: Bool
        /// The screen held upright, which frames are measured in.
        let space: UICoordinateSpace
        /// Whether a view is a SwiftUI hosting view whose debug data may be read.
        let isHosting: (UIView) -> Bool
        var budget: NodeBudget
        private(set) var tags: [(Tag, CGRect)] = []
        private(set) var hostings: [HostingFound] = []
        private(set) var cut = false
        private var hostingStack: [Int] = []

        init(
            describers: DescriberRegistry,
            redactValues: Bool,
            space: UICoordinateSpace,
            budget: NodeBudget,
            isHosting: @escaping (UIView) -> Bool = { _ in false }
        ) {
            self.describers = describers
            self.redactValues = redactValues
            self.space = space
            self.budget = budget
            self.isHosting = isHosting
        }

        /// The nodes for a view and everything inside it that shows within `clip`.
        mutating func walk(_ view: UIView, clip: CGRect, depth: Int = 0) -> [Node] {
            guard !view.isHidden, view.alpha >= 0.01 else { return [] }
            guard depth < Self.maxDepth else {
                cut = true
                return []
            }
            let frame = view.convert(view.bounds, to: space)
            let visible = frame.intersection(clip)
            let shows = !visible.isNull && visible.width > 0 && visible.height > 0
            if let anchor = view as? TagAnchorView {
                if shows { tags.append((anchor.mirrorTag, frame)) }
                recordPlatformView(frame)
                return []
            }
            if NSStringFromClass(type(of: view)).contains("PlatformViewHost") { recordPlatformView(frame) }
            let childClip = view.clipsToBounds ? visible : clip
            guard shows || !view.clipsToBounds else { return [] }
            var description = describers.describe(view)
            guard !description.hidden else { return [] }
            if description.label == nil, let (label, source) = LabelDeriver.label(of: view, as: description) {
                description.label = label
                description.labelSource = source
            }
            let hollow =
                description.kind == .container && !description.interactive && description.label == nil
                && description.identifier == nil
            let emitted = shows && !hollow
            if emitted, !budget.take() { return [] }
            let hosting = isHosting(view)
            if hosting {
                hostings.append(HostingFound(view: view, frame: frame))
                hostingStack.append(hostings.count - 1)
            }
            let children = description.readsChildren ? walkChildren(of: view, clip: childClip, depth: depth + 1) : []
            if hosting { hostingStack.removeLast() }
            guard emitted else { return children }
            return [node(for: view, description, frame: frame, children: children)]
        }

        private mutating func walkChildren(of view: UIView, clip: CGRect, depth: Int) -> [Node] {
            var subviews = view.subviews
            if let modal = subviews.last(where: { $0.accessibilityViewIsModal && !$0.isHidden }) {
                subviews = [modal]
            }
            return subviews.flatMap { walk($0, clip: clip, depth: depth) }
        }

        private mutating func recordPlatformView(_ frame: CGRect) {
            guard let index = hostingStack.last else { return }
            hostings[index].platformViews.append(frame)
        }

        private func node(for view: UIView, _ description: Description, frame: CGRect, children: [Node]) -> Node {
            Node(
                kind: description.kind,
                label: description.label,
                labelSource: description.labelSource,
                identifier: description.identifier,
                value: redactValues || description.kind == .secure ? nil : description.value,
                placeholder: description.placeholder,
                frame: Frame(frame),
                traits: description.traits,
                enabled: description.enabled,
                interactive: description.interactive,
                source: .uikit,
                typeName: TypeName.full(type(of: view)),
                children: children
            )
        }
    }
#endif
