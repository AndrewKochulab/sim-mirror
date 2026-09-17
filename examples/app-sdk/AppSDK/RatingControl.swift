// SPDX-License-Identifier: Apache-2.0
import SimMirrorKit
import UIKit

/// A hand-drawn control: stars a person taps to rate something. It says nothing to accessibility, as custom controls
/// often do not.
final class RatingControl: UIControl {
    static let stars = 5
    private(set) var rating = 3

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapped(_:))))
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("RatingControl is made in code only")
    }

    override var intrinsicContentSize: CGSize { CGSize(width: 180, height: 36) }

    override func draw(_ rect: CGRect) {
        let size = bounds.width / CGFloat(Self.stars)
        for index in 0..<Self.stars {
            let name = index < rating ? "star.fill" : "star"
            let star = UIImage(systemName: name)?.withTintColor(.systemOrange, renderingMode: .alwaysOriginal)
            star?.draw(in: CGRect(x: CGFloat(index) * size + 4, y: 4, width: size - 8, height: bounds.height - 8))
        }
    }

    @objc private func tapped(_ recognizer: UITapGestureRecognizer) {
        let size = bounds.width / CGFloat(Self.stars)
        rating = min(Self.stars, Int(recognizer.location(in: self).x / size) + 1)
        setNeedsDisplay()
        sendActions(for: .valueChanged)
    }
}

/// Tells SimMirror what a `RatingControl` is: registered once, it describes every rating control in the app.
struct RatingControlDescriber: ViewDescribing {
    func describe(_ view: UIView) -> ViewDescription? {
        guard let control = view as? RatingControl else { return nil }
        return ViewDescription(kind: .slider, label: "Rating", value: "\(control.rating) of \(RatingControl.stars)")
    }
}
