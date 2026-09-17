// SPDX-License-Identifier: Apache-2.0
extension SimMirror {
    /// What a view is, in the words a SimMirror snapshot uses.
    public enum Kind: String, CaseIterable, Sendable {
        /// Something a tap acts on.
        case button
        /// A link.
        case link
        /// Text.
        case text
        /// A heading.
        case heading
        /// An image.
        case image
        /// A text field.
        case field
        /// A secure text field, whose text is never read.
        case secure
        /// A search field.
        case search
        /// A switch.
        case `switch`
        /// A slider.
        case slider
        /// A stepper.
        case stepper
        /// A picker.
        case picker
        /// A segmented control.
        case segments
        /// A tab.
        case tab
        /// A cell of a table or collection.
        case cell
        /// A view that holds others.
        case container
        /// A scroll view.
        case scroll
        /// A list, table or collection.
        case list
        /// A navigation bar.
        case navigationBar = "navigation_bar"
        /// A tab bar.
        case tabBar = "tab_bar"
        /// A toolbar.
        case toolbar
    }
}
