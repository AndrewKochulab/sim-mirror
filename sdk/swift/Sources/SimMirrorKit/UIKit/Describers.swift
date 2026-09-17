// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import UIKit

    /// What a view is, as the walker uses it: everything a node needs but its frame, its children and its label when
    /// the label is still to be worked out.
    struct Description: Equatable {
        var kind: SimMirror.Kind
        var label: String?
        var labelSource: LabelSource?
        var value: String?
        var placeholder: String?
        var identifier: String?
        var interactive: Bool
        /// Whether the views inside are read as nodes of their own.
        var readsChildren: Bool
        var traits: [Trait] = []
        var enabled = true
        var hidden = false

        static let hidden = Description(kind: .container, interactive: false, readsChildren: false, hidden: true)
    }

    extension SimMirror.Kind {
        /// Whether a tap on something of this kind does something.
        var isControl: Bool {
            switch self {
            case .button, .link, .field, .secure, .search, .switch, .slider, .stepper, .picker, .segments, .tab, .cell:
                true
            default:
                false
            }
        }
    }

    /// The describers the app registered, asked first, then SimMirror's own.
    @MainActor
    final class DescriberRegistry {
        private(set) var custom: [any ViewDescribing] = []

        func add(_ describer: some ViewDescribing) {
            custom.append(describer)
        }

        func describe(_ view: UIView) -> Description {
            for describer in custom {
                if let given = describer.describe(view) {
                    return Description(given, of: view)
                }
            }
            return BuiltInDescriber.describe(view)
        }
    }

    extension Description {
        /// What a registered describer said, with what it left out read from the view.
        @MainActor
        init(_ given: ViewDescription, of view: UIView) {
            guard !given.isHidden else {
                self = .hidden
                return
            }
            self.init(
                kind: given.kind,
                label: given.label,
                labelSource: given.label == nil ? nil : .tag,
                value: given.value,
                identifier: given.identifier ?? BuiltInDescriber.identifier(of: view),
                interactive: given.interactive ?? given.kind.isControl,
                readsChildren: given.readsChildren,
                traits: BuiltInDescriber.traits(of: view),
                enabled: BuiltInDescriber.enabled(view)
            )
        }
    }

    /// SimMirror's own reading of a view: its class first, then its accessibility traits, then a tap gesture, and
    /// anything else holds other views.
    @MainActor
    enum BuiltInDescriber {
        static func describe(_ view: UIView) -> Description {
            var description: Description
            if let known = byClass(view) {
                description = known
            } else {
                // A view of the app's own says its value through accessibility; UIKit's controls say theirs above --
                // an empty field's accessibility value is its placeholder, which is no value.
                description = byTraits(view) ?? byGesture(view) ?? container(view)
                description.value = nonEmpty(view.accessibilityValue)
            }
            if description.identifier == nil { description.identifier = identifier(of: view) }
            description.traits = traits(of: view)
            description.enabled = enabled(view)
            return description
        }

        static func identifier(of view: UIView) -> String? {
            nonEmpty(view.accessibilityIdentifier)
        }

        static func traits(of view: UIView) -> [Trait] {
            var traits: [Trait] = []
            if (view as? UIControl)?.isSelected == true || view.accessibilityTraits.contains(.selected) {
                traits.append(.selected)
            }
            if (view as? UITextField)?.isEditing == true || ((view as? UITextView)?.isFirstResponder ?? false) {
                traits.append(.editing)
            }
            return traits
        }

        static func enabled(_ view: UIView) -> Bool {
            (view as? UIControl)?.isEnabled ?? !view.accessibilityTraits.contains(.notEnabled)
        }

        private static func node(_ kind: SimMirror.Kind, value: String? = nil, placeholder: String? = nil)
            -> Description
        {
            Description(
                kind: kind, value: value, placeholder: placeholder, interactive: kind.isControl, readsChildren: false)
        }

        private static func holder(_ kind: SimMirror.Kind, interactive: Bool = false) -> Description {
            Description(kind: kind, interactive: interactive, readsChildren: true)
        }

        static func byClass(_ view: UIView) -> Description? {
            switch view {
            case let field as UITextField:
                let placeholder = nonEmpty(field.placeholder) ?? nonEmpty(field.attributedPlaceholder?.string)
                if field.isSecureTextEntry { return node(.secure, placeholder: placeholder) }
                return node(
                    field is UISearchTextField ? .search : .field, value: nonEmpty(field.text), placeholder: placeholder
                )
            case let textView as UITextView:
                return textView.isEditable ? node(.field, value: nonEmpty(textView.text)) : node(.text)
            case let toggle as UISwitch:
                return node(.switch, value: toggle.isOn ? "1" : "0")
            case let slider as UISlider:
                let span = slider.maximumValue - slider.minimumValue
                let share = span > 0 ? (slider.value - slider.minimumValue) / span : 0
                return node(.slider, value: nonEmpty(slider.accessibilityValue) ?? "\(Int((share * 100).rounded()))%")
            case let stepper as UIStepper:
                return node(.stepper, value: number(stepper.value))
            case let segments as UISegmentedControl:
                let selected = segments.selectedSegmentIndex
                return node(.segments, value: selected >= 0 ? segmentName(segments, at: selected) : nil)
            case is UIDatePicker, is UIPickerView:
                return node(.picker)
            case is UIButton:
                return node(.button)
            case let label as UILabel:
                return node(label.accessibilityTraits.contains(.header) ? .heading : .text)
            case let image as UIImageView:
                return image.image == nil ? .hidden : node(.image)
            case is UITableViewCell, is UICollectionViewCell:
                return holder(.cell, interactive: true)
            case is UITableView, is UICollectionView:
                return holder(.list)
            case is UIScrollView:
                return holder(.scroll)
            case is UINavigationBar:
                return holder(.navigationBar)
            case is UITabBar:
                return holder(.tabBar)
            case is UIToolbar:
                return holder(.toolbar)
            case is UIControl:
                return node(isInTabBar(view) ? .tab : .button, value: nonEmpty(view.accessibilityValue))
            default:
                return nil
            }
        }

        static func byTraits(_ view: UIView) -> Description? {
            let traits = view.accessibilityTraits
            let table: [(UIAccessibilityTraits, SimMirror.Kind)] = [
                (.button, .button), (.link, .link), (.searchField, .search), (.adjustable, .slider),
                (.header, .heading), (.image, .image), (.staticText, .text), (.tabBar, .tabBar),
            ]
            guard let kind = table.first(where: { traits.contains($0.0) })?.1 else { return nil }
            return node(kind)
        }

        /// A view with a tap gesture the app added. The system adds tap recognizers of its own -- to a window, a tab bar
        /// controller's view, a switch -- whose class is private or whose delegate is the system's; they say nothing of
        /// what the app does with a tap.
        static func byGesture(_ view: UIView) -> Description? {
            let tappable = (view.gestureRecognizers ?? []).contains { recognizer in
                recognizer is UITapGestureRecognizer && recognizer.isEnabled
                    && !NSStringFromClass(type(of: recognizer)).hasPrefix("_")
                    && !((recognizer.delegate as AnyObject?).map { isSystem(type(of: $0)) } ?? false)
            }
            return tappable ? holder(.container, interactive: true) : nil
        }

        /// Whether a control is inside a tab bar, however deep: iOS 26 puts a tab bar's buttons in views of their own.
        static func isInTabBar(_ view: UIView) -> Bool {
            sequence(first: view, next: \.superview).dropFirst().contains { $0 is UITabBar }
        }

        /// Whether a class comes with the system rather than the app.
        static func isSystem(_ type: AnyClass) -> Bool {
            Bundle(for: type).bundlePath.contains("/System/Library/")
        }

        static func container(_ view: UIView) -> Description {
            holder(.container)
        }

        static func number(_ value: Double) -> String {
            value == value.rounded() && abs(value) < 1e15 ? String(Int64(value)) : String(value)
        }

        static func segmentName(_ segments: UISegmentedControl, at index: Int) -> String? {
            nonEmpty(segments.titleForSegment(at: index)) ?? segments.imageForSegment(at: index).flatMap(ImageName.of)
        }
    }
#endif
