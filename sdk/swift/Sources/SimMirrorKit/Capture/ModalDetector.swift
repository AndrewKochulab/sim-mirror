// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import UIKit

    /// What a window presents in front of the rest: the view controller presented last, and how.
    @MainActor
    enum ModalDetector {
        struct Found {
            let modal: Modal
            /// The view to read instead of the window: what the modal hides cannot be reached.
            let view: UIView
            /// The titles of an alert's or an action sheet's destructive actions.
            var destructive: Set<String> = []
        }

        static func detect(in window: UIWindow) -> Found? {
            guard let root = window.rootViewController else { return nil }
            var top = root
            while let presented = top.presentedViewController, !presented.isBeingDismissed {
                top = presented
            }
            guard top !== root, let view = top.viewIfLoaded, view.window === window else { return nil }
            return Found(
                modal: Modal(kind: kind(of: top), name: name(of: top)), view: view, destructive: destructive(of: top))
        }

        /// The titles of the actions an alert or an action sheet styles `.destructive` -- SwiftUI's alerts and
        /// confirmation dialogs are alert controllers too, and a `Button(role: .destructive)` in one is such an action.
        static func destructive(of controller: UIViewController) -> Set<String> {
            let actions = (controller as? UIAlertController)?.actions ?? []
            return Set(actions.filter { $0.style == .destructive }.compactMap { nonEmpty($0.title) })
        }

        /// The nodes, with what an alert shows for one of its destructive actions marked so. UIKit draws an action as a
        /// view of its own, private, class that says nothing of its style: what says a destructive action's title is
        /// that action -- a button while accessibility is in use, and only its title's text while it is not.
        static func markDestructive(_ nodes: [Node], titles: Set<String>) -> [Node] {
            guard !titles.isEmpty else { return nodes }
            return nodes.map { node in
                var node = node
                if let label = node.label, titles.contains(label) { node.traits.append(.destructive) }
                node.children = markDestructive(node.children, titles: titles)
                return node
            }
        }

        static func kind(of controller: UIViewController) -> ModalKind {
            if controller is UIAlertController { return .alert }
            switch controller.modalPresentationStyle {
            case .fullScreen, .overFullScreen, .currentContext, .overCurrentContext:
                return .fullScreen
            case .popover:
                return controller.traitCollection.horizontalSizeClass == .regular ? .popover : .sheet
            default:
                return .sheet
            }
        }

        static func name(of controller: UIViewController) -> String? {
            let navigation = controller as? UINavigationController
            let shown = navigation?.topViewController ?? controller
            return [shown.title, shown.navigationItem.title].lazy.compactMap { nonEmpty($0) }.first
        }
    }
#endif
