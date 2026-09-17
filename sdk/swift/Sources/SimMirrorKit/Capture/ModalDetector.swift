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
        }

        static func detect(in window: UIWindow) -> Found? {
            guard let root = window.rootViewController else { return nil }
            var top = root
            while let presented = top.presentedViewController, !presented.isBeingDismissed {
                top = presented
            }
            guard top !== root, let view = top.viewIfLoaded, view.window === window else { return nil }
            return Found(modal: Modal(kind: kind(of: top), name: name(of: top)), view: view)
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
