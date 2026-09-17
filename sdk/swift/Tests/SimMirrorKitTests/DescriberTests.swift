// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing
import UIKit

@testable import SimMirrorKit

@MainActor
struct DescriberTests {
    private func describe(_ view: UIView) -> Description {
        BuiltInDescriber.describe(view)
    }

    @Test func fieldsSaySecureSearchOrPlainAndNeverReadASecureFieldsText() {
        let field = UITextField()
        field.text = "Groceries"
        field.placeholder = "Title"
        #expect(describe(field).kind == .field && describe(field).value == "Groceries")
        #expect(describe(field).placeholder == "Title" && describe(field).interactive)
        field.text = ""
        #expect(describe(field).value == nil, "an empty field's placeholder is not its value")
        let secure = UITextField()
        secure.isSecureTextEntry = true
        secure.text = "hunter2"
        secure.attributedPlaceholder = NSAttributedString(string: "Password")
        secure.accessibilityValue = "hunter2"
        #expect(
            describe(secure)
                == Description(kind: .secure, placeholder: "Password", interactive: true, readsChildren: false))
        let search = UISearchTextField()
        #expect(describe(search).kind == .search && describe(search).value == nil)
        let editable = UITextView()
        editable.text = "Notes"
        #expect(describe(editable).kind == .field && describe(editable).value == "Notes")
        editable.isEditable = false
        #expect(describe(editable).kind == .text)
    }

    @Test func controlsSayTheirValues() {
        let toggle = UISwitch()
        #expect(describe(toggle).value == "0")
        toggle.isOn = true
        #expect(describe(toggle).kind == .switch && describe(toggle).value == "1")
        let slider = UISlider()
        slider.minimumValue = 10
        slider.maximumValue = 20
        slider.value = 15
        #expect(describe(slider).kind == .slider && describe(slider).value == "50%")
        slider.minimumValue = 20
        slider.maximumValue = 20
        #expect(describe(slider).value == "0%")
        slider.accessibilityValue = "Loud"
        #expect(describe(slider).value == "Loud")
        let stepper = UIStepper()
        stepper.value = 3
        #expect(describe(stepper).kind == .stepper && describe(stepper).value == "3")
        stepper.stepValue = 0.5
        stepper.value = 3.5
        #expect(describe(stepper).value == "3.5")
        let segments = UISegmentedControl(items: ["Day", UIImage(systemName: "star") as Any])
        #expect(describe(segments).kind == .segments && describe(segments).value == nil)
        segments.selectedSegmentIndex = 1
        #expect(describe(segments).value == "star")
        segments.selectedSegmentIndex = 0
        #expect(describe(segments).value == "Day")
        #expect(describe(UIDatePicker()).kind == .picker && describe(UIPickerView()).kind == .picker)
    }

    @Test func buttonsTextImagesAndHeadings() {
        let button = UIButton(type: .system)
        button.isEnabled = false
        button.isSelected = true
        let described = describe(button)
        #expect(described.kind == .button && !described.enabled && described.traits == [.selected])
        let label = UILabel()
        #expect(describe(label).kind == .text && !describe(label).interactive)
        label.accessibilityTraits = .header
        #expect(describe(label).kind == .heading)
        #expect(describe(UIImageView()).hidden)
        #expect(describe(UIImageView(image: UIImage(systemName: "star"))).kind == .image)
    }

    @Test func holdersReadWhatIsInsideThem() {
        let holders: [(UIView, SimMirror.Kind, Bool)] = [
            (UITableViewCell(), .cell, true), (UICollectionViewCell(), .cell, true), (UITableView(), .list, false),
            (UICollectionView(frame: .zero, collectionViewLayout: UICollectionViewFlowLayout()), .list, false),
            (UIScrollView(), .scroll, false), (UINavigationBar(), .navigationBar, false), (UITabBar(), .tabBar, false),
            (UIToolbar(), .toolbar, false), (UIView(), .container, false), (UIStackView(), .container, false),
        ]
        for (view, kind, interactive) in holders {
            let described = describe(view)
            #expect(described.kind == kind && described.readsChildren && described.interactive == interactive)
        }
    }

    @Test func aControlOfItsOwnIsAButtonOrATabInATabBar() {
        final class Rating: UIControl {}
        #expect(describe(Rating()).kind == .button)
        let bar = UITabBar()
        let tab = Rating()
        bar.addSubview(tab)
        #expect(describe(tab).kind == .tab)
        let platter = UIView()
        let nested = Rating()
        platter.addSubview(nested)
        bar.addSubview(platter)
        #expect(describe(nested).kind == .tab)
    }

    @Test func aViewOfItsOwnIsReadByItsTraitsThenATapThenAsAContainer() {
        let traits: [(UIAccessibilityTraits, SimMirror.Kind)] = [
            (.button, .button), (.link, .link), (.searchField, .search), (.adjustable, .slider), (.header, .heading),
            (.image, .image), (.staticText, .text), (.tabBar, .tabBar),
        ]
        for (trait, kind) in traits {
            let view = UIView()
            view.accessibilityTraits = trait
            #expect(describe(view).kind == kind)
        }
        let tappable = UIView()
        let tap = UITapGestureRecognizer()
        tappable.addGestureRecognizer(tap)
        #expect(describe(tappable) == Description(kind: .container, interactive: true, readsChildren: true))
        tap.isEnabled = false
        #expect(!describe(tappable).interactive)
        let quiet = UIView()
        let custom = UIControl()
        custom.accessibilityValue = "4 of 5"
        #expect(describe(custom).value == "4 of 5")
        quiet.accessibilityTraits = .notEnabled
        quiet.accessibilityIdentifier = "promo"
        quiet.accessibilityValue = "3 of 5"
        let described = describe(quiet)
        #expect(!described.enabled && described.identifier == "promo" && described.value == "3 of 5")
    }

    @Test func aFieldBeingEditedSaysSo() {
        let field = UITextField()
        let window = testWindow(field)
        field.becomeFirstResponder()
        #expect(describe(field).traits == [.editing])
        field.resignFirstResponder()
        let textView = UITextView()
        window.addSubview(textView)
        textView.becomeFirstResponder()
        #expect(describe(textView).traits.contains(.editing))
        textView.resignFirstResponder()
    }

    /// A gesture delegate of the app's own.
    final class AppDelegate: NSObject, UIGestureRecognizerDelegate {}

    @Test func aTapTheSystemAddedIsNotOneTheAppActsOn() {
        let tappable = UIView()
        let systemTaps = (UISwitch().gestureRecognizers ?? []) + (testWindow().gestureRecognizers ?? [])
        for tap in systemTaps where tap is UITapGestureRecognizer && tap.delegate != nil {
            tappable.addGestureRecognizer(tap)
        }
        #expect(!describe(tappable).interactive)
        let tap = UITapGestureRecognizer()
        tappable.gestureRecognizers = [tap]
        let appDelegate = AppDelegate()
        tap.delegate = appDelegate
        #expect(describe(tappable).interactive)
        #expect(BuiltInDescriber.isSystem(UIView.self) && !BuiltInDescriber.isSystem(AppDelegate.self))
        #expect(!describe(testWindow()).interactive, "a window's own tap recognizers are the system's")
    }

    struct Rater: ViewDescribing {
        func describe(_ view: UIView) -> ViewDescription? {
            switch view.tag {
            case 1: ViewDescription(kind: .slider, label: "Rating", value: "3 of 5")
            case 2: .hidden
            case 3: ViewDescription(kind: .container, identifier: "own", interactive: true, readsChildren: true)
            default: nil
            }
        }
    }

    @Test func aRegisteredDescriberIsAskedFirstAndWhatItLeavesOutIsReadFromTheView() {
        let registry = DescriberRegistry()
        registry.add(Rater())
        #expect(registry.custom.count == 1)
        let rating = UIView()
        rating.tag = 1
        rating.accessibilityIdentifier = "rating"
        #expect(
            registry.describe(rating)
                == Description(
                    kind: .slider, label: "Rating", labelSource: .tag, value: "3 of 5", identifier: "rating",
                    interactive: true, readsChildren: false))
        let secret = UIView()
        secret.tag = 2
        #expect(registry.describe(secret).hidden)
        let own = UIControl()
        own.tag = 3
        own.accessibilityIdentifier = "ignored"
        let described = registry.describe(own)
        #expect(described.identifier == "own" && described.labelSource == nil && described.interactive)
        #expect(registry.describe(UISwitch()).kind == .switch)
        #expect(ViewDescription(kind: .button).interactive == nil && !ViewDescription(kind: .button).readsChildren)
    }

    @Test func whichKindsATapActsOn() {
        let controls = SimMirror.Kind.allCases.filter(\.isControl)
        #expect(
            controls == [
                .button, .link, .field, .secure, .search, .switch, .slider, .stepper, .picker, .segments, .tab, .cell,
            ])
        #expect(BuiltInDescriber.number(1e20) == "1e+20")
    }
}

@MainActor
struct LabelDeriverTests {
    /// What a view is labeled and where the label came from, as "label | source".
    private func label(_ view: UIView) -> String? {
        LabelDeriver.label(of: view, as: BuiltInDescriber.describe(view)).map { "\($0.0) | \($0.1.rawValue)" }
    }

    @Test func accessibilityComesFirstThenTitleOrText() {
        let button = UIButton(type: .system)
        button.setTitle("Save", for: .normal)
        #expect(label(button) == "Save | accessibility" || label(button) == "Save | title")
        button.accessibilityLabel = "Save the list"
        #expect(label(button) == "Save the list | accessibility")
        let plain = UIButton(configuration: .plain())
        plain.configuration?.title = "Configured"
        plain.accessibilityLabel = nil
        #expect(LabelDeriver.title(of: plain) == "Configured")
        let text = UILabel()
        text.text = "Weekly promo"
        #expect(LabelDeriver.title(of: text) == "Weekly promo")
        let field = UITextField()
        field.placeholder = "Title"
        #expect(label(field) == "Title | title")
        let segments = UISegmentedControl(items: ["List", UIImage(systemName: "square.grid.2x2") as Any])
        #expect(LabelDeriver.title(of: segments) == "List, square.grid.2x2")
        #expect(LabelDeriver.title(of: UIView()) == nil)
    }

    @Test func aTappableViewIsNamedByTheTextInsideItThenItsImageIdentifierOrType() {
        final class PromoCard: UIView {}
        let card = PromoCard()
        card.addGestureRecognizer(UITapGestureRecognizer())
        let hidden = UILabel()
        hidden.text = "Hidden"
        hidden.isHidden = true
        let nested = UIView()
        let title = UILabel()
        title.text = "Weekly promo"
        nested.addSubview(title)
        let action = UIButton(type: .system)
        action.setTitle("Open", for: .normal)
        [hidden, nested, action].forEach(card.addSubview)
        #expect(label(card) == "Weekly promo, Open | descendants")
        [nested, action].forEach { $0.removeFromSuperview() }
        let icon = UIImageView(image: UIImage(systemName: "sun.max"))
        card.addSubview(icon)
        #expect(label(card) == "sun.max | image")
        icon.removeFromSuperview()
        card.accessibilityIdentifier = "promo"
        #expect(label(card) == "promo | identifier")
        card.accessibilityIdentifier = nil
        #expect(label(card) == "Promo card | type")
        let anonymous = UIView()
        anonymous.addGestureRecognizer(UITapGestureRecognizer())
        #expect(label(anonymous) == nil)
    }

    @Test func aContainerNobodyTapsSaysOnlyItsAccessibilityLabelOrIdentifier() {
        let holder = UIView()
        let inside = UILabel()
        inside.text = "Inside"
        holder.addSubview(inside)
        #expect(label(holder) == nil)
        holder.accessibilityIdentifier = "holder"
        #expect(label(holder) == "holder | identifier")
        holder.accessibilityLabel = "Settings"
        #expect(label(holder) == "Settings | accessibility")
    }

    @Test func iconButtonsAndImagesAreNamedByTheirImage() {
        let icon = UIButton(type: .system)
        icon.setImage(UIImage(systemName: "trash"), for: .normal)
        #expect(LabelDeriver.image(of: icon) == "trash")
        let configured = UIButton(configuration: .plain())
        configured.configuration?.image = UIImage(systemName: "gearshape")
        #expect(LabelDeriver.image(of: configured) == "gearshape")
        #expect(LabelDeriver.image(of: UIImageView(image: UIImage(systemName: "star"))) == "star")
        let texts = (0..<5).map { index -> UILabel in
            let label = UILabel()
            label.text = "t\(index)"
            return label
        }
        let many = UIView()
        texts.forEach(many.addSubview)
        #expect(LabelDeriver.descendantTexts(of: many) == "t0, t1, t2")
        let long = UILabel()
        long.text = String(repeating: "a", count: 300)
        #expect(LabelDeriver.label(of: long, as: BuiltInDescriber.describe(long))?.0.count == LabelDeriver.maxLength)
    }

    @Test func imageNamesAreReadFromHowUIKitDescribesThem() {
        #expect(ImageName.parse("<UIImage:0x1 symbol(system: star.fill) {20, 19} baseline=0>") == "star.fill")
        #expect(ImageName.parse("<UIImage:0x1 named(main: photo) {100, 100}>") == "photo")
        #expect(ImageName.parse("<UIImage:0x1 anonymous {1, 1}>") == nil)
        let drawn = UIGraphicsImageRenderer(size: CGSize(width: 2, height: 2)).image { _ in }
        #expect(ImageName.of(drawn) == nil)
        drawn.accessibilityIdentifier = "avatar"
        #expect(ImageName.of(drawn) == "avatar")
    }

    @Test func typeNamesAreSaidAsWordsAndAppleTypesNotAtAll() {
        #expect(TypeName.words("RatingControl") == "Rating control")
        #expect(TypeName.words("URLField") == "URL field")
        #expect(TypeName.words("A") == "A")
        #expect(TypeName.humanized(UIButton.self) == nil)
        #expect(TypeName.humanized(Array<Int>.self) == nil)
        #expect(TypeName.humanized(_Hidden.self) == nil)
        #expect(TypeName.full(UIButton.self) == "UIButton")
        #expect(TypeName.full(LabelDeriverTests.self) == "SimMirrorKitTests.LabelDeriverTests")
    }

    struct _Hidden {}
}
