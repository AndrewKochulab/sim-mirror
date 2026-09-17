// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import UIKit

    /// Works out what a view says when nothing said it: its accessibility label, else its title or text, the text
    /// inside it, its image's name, its identifier, and -- for a control of the app's own -- its type's name.
    @MainActor
    enum LabelDeriver {
        /// The longest label sent; a longer one is cut short.
        static let maxLength = 200
        /// How many texts inside a view name it at most.
        static let maxDescendantTexts = 3

        static func label(of view: UIView, as description: Description) -> (String, LabelSource)? {
            let found = find(view, description)
            return found.map { (String($0.0.prefix(maxLength)), $0.1) }
        }

        private static func find(_ view: UIView, _ description: Description) -> (String, LabelSource)? {
            if let label = nonEmpty(view.accessibilityLabel) { return (label, .accessibility) }
            if let title = title(of: view) { return (title, view is UILabel ? .text : .title) }
            let holder = description.kind == .container || description.readsChildren
            guard !holder || description.interactive else {
                return description.identifier.map { ($0, .identifier) }
            }
            if let texts = descendantTexts(of: view) { return (texts, .descendants) }
            if let image = image(of: view) { return (image, .image) }
            if let identifier = description.identifier { return (identifier, .identifier) }
            guard description.interactive, let name = TypeName.humanized(type(of: view)) else { return nil }
            return (name, .type)
        }

        static func title(of view: UIView) -> String? {
            switch view {
            case let button as UIButton:
                nonEmpty(button.currentTitle) ?? nonEmpty(button.configuration?.title)
            case let label as UILabel:
                nonEmpty(label.text)
            case let field as UITextField:
                nonEmpty(field.placeholder)
            case let segments as UISegmentedControl:
                joined((0..<segments.numberOfSegments).compactMap { BuiltInDescriber.segmentName(segments, at: $0) })
            default:
                nil
            }
        }

        static func image(of view: UIView) -> String? {
            switch view {
            case let button as UIButton:
                (button.currentImage ?? button.configuration?.image).flatMap(ImageName.of)
            case let imageView as UIImageView:
                imageView.image.flatMap(ImageName.of)
            default:
                view.subviews.lazy.compactMap { ($0 as? UIImageView)?.image.flatMap(ImageName.of) }.first
            }
        }

        /// The text of the labels inside a view, the first few, in the order they are drawn.
        static func descendantTexts(of view: UIView) -> String? {
            var texts: [String] = []
            func collect(_ view: UIView) {
                for child in view.subviews where !child.isHidden && child.alpha >= 0.01 {
                    guard texts.count < maxDescendantTexts else { return }
                    if let text = (child as? UILabel).flatMap({ nonEmpty($0.text) })
                        ?? (child as? UIButton).flatMap({ nonEmpty($0.currentTitle) })
                    {
                        texts.append(text)
                    } else {
                        collect(child)
                    }
                }
            }
            collect(view)
            return joined(texts)
        }

        private static func joined(_ parts: [String]) -> String? {
            parts.isEmpty ? nil : parts.joined(separator: ", ")
        }
    }

    /// The name of an image: an SF Symbol's, or an asset's.
    enum ImageName {
        private static let pattern = try? NSRegularExpression(pattern: #"(?:symbol|named)\([A-Za-z]+: ([^)]+)\)"#)

        @MainActor
        static func of(_ image: UIImage) -> String? {
            parse(image.description) ?? nonEmpty(image.accessibilityIdentifier)
        }

        /// The name in an image's description, as UIKit writes it: `symbol(system: star)`, `named(main: photo)`.
        static func parse(_ description: String) -> String? {
            let range = NSRange(description.startIndex..., in: description)
            guard let match = pattern?.firstMatch(in: description, range: range),
                let name = Range(match.range(at: 1), in: description)
            else { return nil }
            return String(description[name])
        }
    }

    /// A type's name as a person would say it: `AppSDK.RatingControl` is "Rating control". SimMirror says nothing of
    /// Apple's own types, whose names mean nothing to a person.
    enum TypeName {
        static func humanized(_ type: Any.Type) -> String? {
            let full = String(reflecting: type)
            let parts = full.split(separator: ".")
            guard parts.count > 1, !full.contains("<"), !["UIKit", "SwiftUI", "SwiftUICore"].contains(parts[0]),
                let name = parts.last, !name.hasPrefix("_")
            else { return nil }
            return words(String(name))
        }

        /// `RatingControl` as "Rating control", `URLField` as "URL field".
        static func words(_ name: String) -> String {
            let characters = Array(name)
            var words: [String] = []
            var current = ""
            for (index, character) in characters.enumerated() {
                let next = index + 1 < characters.count ? characters[index + 1] : nil
                let startsWord =
                    character.isUppercase && !current.isEmpty
                    && (current.last?.isUppercase != true || next?.isLowercase == true)
                if startsWord {
                    words.append(current)
                    current = ""
                }
                current.append(character)
            }
            words.append(current)
            return words.enumerated().map { index, word in
                index == 0 || word.count > 1 && word == word.uppercased() ? word : word.lowercased()
            }.joined(separator: " ")
        }

        /// The type a node was read from, as Swift writes it, cut to what the wire allows.
        static func full(_ type: Any.Type) -> String {
            String(String(reflecting: type).prefix(200))
        }
    }
#endif
