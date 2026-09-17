// SPDX-License-Identifier: Apache-2.0
#if canImport(UIKit)
    import UIKit

    /// Says what a view of your own is, for views SimMirror cannot work out by itself -- a custom control drawn by hand,
    /// a view that stands for a button. Register one with `SimMirror.register(describer:)`.
    @MainActor
    public protocol ViewDescribing {
        /// What `view` is, or nil when this describer does not know the view, leaving it to the others.
        func describe(_ view: UIView) -> ViewDescription?
    }

    /// What a describer says a view is.
    public struct ViewDescription: Sendable, Equatable {
        /// What the view is.
        public var kind: SimMirror.Kind
        /// What it says; nil works one out as for any other view.
        public var label: String?
        /// Its value, such as a rating's "3 of 5"; nil when it has none.
        public var value: String?
        /// An identifier to find it by; nil keeps the view's accessibility identifier.
        public var identifier: String?
        /// Whether a tap on it does something; nil means it does when its kind is a control.
        public var interactive: Bool?
        /// Whether the views inside it are read too. Off by default: the description stands for all of it.
        public var readsChildren: Bool
        let isHidden: Bool

        /// A description of a view.
        public init(
            kind: SimMirror.Kind,
            label: String? = nil,
            value: String? = nil,
            identifier: String? = nil,
            interactive: Bool? = nil,
            readsChildren: Bool = false
        ) {
            self.kind = kind
            self.label = label
            self.value = value
            self.identifier = identifier
            self.interactive = interactive
            self.readsChildren = readsChildren
            self.isHidden = false
        }

        private init(hidden: Void) {
            self.kind = .container
            self.label = nil
            self.value = nil
            self.identifier = nil
            self.interactive = false
            self.readsChildren = false
            self.isHidden = true
        }

        /// Leaves the view, and everything inside it, out of what SimMirror reads.
        public static let hidden = ViewDescription(hidden: ())
    }

    extension SimMirror {
        /// Adds a describer, asked before SimMirror's own ones and after any added earlier.
        @MainActor
        public static func register(describer: some ViewDescribing) {
            #if DEBUG && targetEnvironment(simulator)
                Runtime.shared.describers.add(describer)
            #endif
        }
    }
#endif
