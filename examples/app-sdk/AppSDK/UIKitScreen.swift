// SPDX-License-Identifier: Apache-2.0
import SwiftUI
import UIKit

/// The UIKit tab: a form built in code, inside a navigation controller.
struct UIKitScreen: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> UINavigationController {
        UINavigationController(rootViewController: FormViewController())
    }

    func updateUIViewController(_ controller: UINavigationController, context: Context) {}
}

/// UIKit as it is often written: icon-only buttons and segments, a view with a tap gesture, a custom control.
final class FormViewController: UIViewController {
    private let promoTaps = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Form"
        view.backgroundColor = .systemBackground
        navigationItem.rightBarButtonItem = UIBarButtonItem(
            image: UIImage(systemName: "square.and.arrow.up"), style: .plain, target: nil, action: nil)

        let title = UITextField()
        title.placeholder = "Title"
        title.borderStyle = .roundedRect
        let password = UITextField()
        password.placeholder = "Password"
        password.isSecureTextEntry = true
        password.borderStyle = .roundedRect
        let notifications = UISwitch()
        let layout = UISegmentedControl(items: [
            UIImage(systemName: "list.bullet") as Any, UIImage(systemName: "square.grid.2x2") as Any,
        ])
        layout.selectedSegmentIndex = 0
        let count = UIStepper()
        count.value = 2
        let trash = UIButton(type: .system)
        trash.setImage(UIImage(systemName: "trash"), for: .normal)
        trash.addTarget(self, action: #selector(confirmDelete), for: .touchUpInside)

        let promo = UIView()
        promo.backgroundColor = UIColor.systemTeal.withAlphaComponent(0.25)
        promo.layer.cornerRadius = 12
        promo.addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(promoTapped)))
        let promoTitle = UILabel()
        promoTitle.text = "Weekly promo"
        promoTaps.text = "Opened 0 times"
        promoTaps.font = .preferredFont(forTextStyle: .footnote)
        let promoStack = UIStackView(arrangedSubviews: [promoTitle, promoTaps])
        promoStack.axis = .vertical
        promoStack.translatesAutoresizingMaskIntoConstraints = false
        promo.addSubview(promoStack)

        let controls = UIStackView(arrangedSubviews: [notifications, layout, count, trash])
        controls.spacing = 16
        controls.alignment = .center
        let form = UIStackView(arrangedSubviews: [title, password, controls, RatingControl(), promo])
        form.axis = .vertical
        form.spacing = 16
        form.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(form)
        NSLayoutConstraint.activate([
            form.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 20),
            form.leadingAnchor.constraint(equalTo: view.layoutMarginsGuide.leadingAnchor),
            form.trailingAnchor.constraint(equalTo: view.layoutMarginsGuide.trailingAnchor),
            promo.heightAnchor.constraint(equalToConstant: 88),
            promoStack.leadingAnchor.constraint(equalTo: promo.leadingAnchor, constant: 16),
            promoStack.centerYAnchor.constraint(equalTo: promo.centerYAnchor),
        ])
    }

    @objc private func promoTapped() {
        let opened = Int(promoTaps.text?.split(separator: " ")[1] ?? "0") ?? 0
        promoTaps.text = "Opened \(opened + 1) times"
    }

    @objc private func confirmDelete() {
        let alert = UIAlertController(title: "Delete the form?", message: nil, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Delete", style: .destructive))
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        present(alert, animated: true)
    }
}
