// SPDX-License-Identifier: Apache-2.0
import AppKit
import CoreGraphics
import Foundation
import HelperCore
import QuartzCore

/// The agent drawn over one macOS app's window, on this Mac: a transparent window of the helper's own, laid over the
/// app's window and ordered just above it, that the pointer, its label, tap rings and trails are drawn in.
///
/// * **It takes nothing from the app.** The window ignores the mouse, never becomes key or main and belongs to a
///   process that never activates, so clicks, keys and focus stay where they were; the agent's own input is app-scoped
///   (accessibility, or the app's adapter), never posted at the screen.
/// * **It is never in what SimMirror captures.** The app's window is captured on its own
///   (`SCContentFilter(desktopIndependentWindow:)`), which leaves every other window out, this one included.
/// * **It follows the app's window**, ordered just above it -- so another app's window over the app is over the
///   pointer too -- and hides while that window is minimized or off screen.
/// * **It goes when the agent does**: after `OverlayModel.shownUntil`, and with the helper, which ends with the session.
///
/// Main thread only: events arrive through `DispatchQueue.main`, and a timer on the main run loop draws.
final class MacOverlay {
    private static let tickS = 1.0 / 30
    /// How often the window is ordered above the app's again: it comes back in front when someone clicks it.
    private static let reorderS = 0.25
    /// The pointer's box, and where its tip is in it, measured from the top left as the viewer's drawing is.
    private static let size = CGSize(width: 24, height: 24)
    private static let tip = CGPoint(x: 4, y: 4)
    private static let font = NSFont.systemFont(ofSize: 12, weight: .semibold)

    private let windowID: CGWindowID
    private let pid: Int32
    private var model = OverlayModel()
    private var panel: NSPanel?
    private var timer: Timer?
    private var lastOrdered = -Double.infinity
    private var shown: OverlayPoint?
    private var parkedAt: CGPoint?
    private var drawn = Set<Int>()
    private let root = CALayer()
    private let pointer = CALayer()
    /// The arrow -- its white rim under its shaded fill -- which presses down as a tap lands.
    private let shape = CALayer()
    private let fill = CAGradientLayer()
    private let chip = CALayer()
    private let chipFill = CAGradientLayer()
    private let chipText = CATextLayer()
    /// The colour the agent is drawn in now: the scope's, else the Mac's accent; nil until first painted.
    private var painted: NSColor?
    private var paintedFor: OverlayColor??
    private let marks = CALayer()

    init(windowID: CGWindowID, pid: Int32) {
        self.windowID = windowID
        self.pid = pid
    }

    static func now() -> Double { ProcessInfo.processInfo.systemUptime }

    private var reducedMotion: Bool { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

    func handle(_ event: OverlayEvent) {
        model.handle(event, at: Self.now())
        if timer == nil {
            let timer = Timer(timeInterval: Self.tickS, repeats: true) { [weak self] _ in self?.tick() }
            RunLoop.main.add(timer, forMode: .common)
            self.timer = timer
        }
        tick()
    }

    func close() {
        timer?.invalidate()
        timer = nil
        panel?.orderOut(nil)
        panel = nil
    }

    private func tick() {
        let now = Self.now()
        let scene = model.scene(at: now)
        guard scene.visible, let frame = targetFrame() else {
            panel?.orderOut(nil)
            shown = nil
            parkedAt = nil
            if !scene.visible {
                timer?.invalidate()
                timer = nil
            }
            return
        }
        let panel = self.panel ?? makePanel()
        if panel.frame != frame { panel.setFrame(frame, display: false) }
        if !panel.isVisible || now - lastOrdered >= Self.reorderS {
            panel.order(.above, relativeTo: Int(windowID))
            lastOrdered = now
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        root.frame = CGRect(origin: .zero, size: frame.size)
        marks.frame = root.bounds
        CATransaction.commit()
        draw(scene, in: frame.size, now: now)
    }

    /// The app's window, in AppKit's screen coordinates; nil while it is minimized, off screen or gone.
    private func targetFrame() -> NSRect? {
        let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, windowID) as? [[String: Any]] ?? []
        guard let row = rows.first(where: { ($0[kCGWindowOwnerPID as String] as? Int) == Int(pid) }),
              (row[kCGWindowIsOnscreen as String] as? Bool) == true,
              let raw = row[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: raw as CFDictionary),
              let primary = NSScreen.screens.first else { return nil }
        // Window bounds are measured down from the top of the primary display; AppKit measures up from its bottom.
        return NSRect(x: bounds.minX, y: primary.frame.maxY - bounds.maxY, width: bounds.width, height: bounds.height)
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.animationBehavior = .none
        panel.collectionBehavior = [.ignoresCycle, .fullScreenAuxiliary, .transient]
        panel.title = "SimMirror agent"
        let view = NSView()
        view.wantsLayer = true
        view.layer = root
        root.addSublayer(marks)
        root.addSublayer(pointer)
        buildPointer()
        panel.contentView = view
        self.panel = panel
        return panel
    }

    /// The viewer's pointer (`viewer/src/agent-cursor.ts`, `POINTER_PATH`): a rounded arrow, in a 24-point box measured
    /// down from its top left, turned into the layers' coordinates, which measure up.
    static func pointerPath() -> CGPath {
        let h = size.height
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint { CGPoint(x: x, y: h - y) }
        let path = CGMutablePath()
        path.move(to: p(5.2, 4.1))
        path.addLine(to: p(20.1, 10.2))
        path.addQuadCurve(to: p(20.1, 11.3), control: p(21.3, 10.8))
        path.addLine(to: p(13.6, 13.6))
        path.addLine(to: p(11.3, 20.1))
        path.addQuadCurve(to: p(10.2, 20.1), control: p(10.8, 21.3))
        path.addLine(to: p(4.1, 5.2))
        path.addQuadCurve(to: p(5.2, 4.1), control: p(3.7, 3.7))
        path.closeSubpath()
        return path
    }

    private static var accent: NSColor { NSColor.controlAccentColor.usingColorSpace(.sRGB) ?? .systemBlue }
    private static func lighter(_ color: NSColor, _ share: CGFloat) -> CGColor {
        (color.blended(withFraction: share, of: .white) ?? color).cgColor
    }
    private var color: NSColor { painted ?? Self.accent }

    /// Draw the agent in the scope's colour -- or the Mac's accent -- with label text that reads on it.
    private func paint(_ wanted: OverlayColor?) {
        if let paintedFor, paintedFor == wanted { return }
        paintedFor = wanted
        let color = wanted.map { NSColor(srgbRed: $0.red, green: $0.green, blue: $0.blue, alpha: 1) } ?? Self.accent
        painted = color
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        fill.colors = [Self.lighter(color, 0.3), color.cgColor]
        chipFill.colors = [Self.lighter(color, 0.14), color.cgColor]
        let white = wanted?.whiteText ?? true
        chipText.foregroundColor = white ? NSColor.white.cgColor : NSColor(srgbRed: 0.067, green: 0.067, blue: 0.075, alpha: 1).cgColor
        CATransaction.commit()
    }

    private func buildPointer() {
        let box = CGRect(origin: .zero, size: Self.size)
        // The tip is where the pointer is placed, and what it presses down and shrinks towards.
        let anchor = CGPoint(x: Self.tip.x / Self.size.width, y: 1 - Self.tip.y / Self.size.height)
        pointer.bounds = box
        pointer.anchorPoint = anchor
        shape.bounds = box
        shape.anchorPoint = anchor
        shape.position = CGPoint(x: Self.tip.x, y: Self.size.height - Self.tip.y)
        let rim = CAShapeLayer()
        rim.frame = box
        rim.path = Self.pointerPath()
        rim.fillColor = NSColor.white.cgColor
        rim.strokeColor = NSColor.white.cgColor
        rim.lineWidth = 2.5
        rim.lineJoin = .round
        rim.shadowColor = NSColor.black.cgColor
        rim.shadowOpacity = 0.3
        rim.shadowRadius = 4
        rim.shadowOffset = CGSize(width: 0, height: -2)
        fill.frame = box
        fill.startPoint = CGPoint(x: 0, y: 1)
        fill.endPoint = CGPoint(x: 0.4, y: 0)
        let mask = CAShapeLayer()
        mask.frame = box
        mask.path = Self.pointerPath()
        fill.mask = mask
        shape.addSublayer(rim)
        shape.addSublayer(fill)
        pointer.addSublayer(shape)

        // The label: a pill shaded like the pointer, with a faint white edge and a soft shadow.
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        let background = chipFill
        background.startPoint = CGPoint(x: 0.5, y: 1)
        background.endPoint = CGPoint(x: 0.5, y: 0)
        background.cornerRadius = 10
        background.borderColor = NSColor.white.withAlphaComponent(0.18).cgColor
        background.borderWidth = 1
        background.masksToBounds = true
        chip.addSublayer(background)
        chip.shadowColor = NSColor.black.cgColor
        chip.shadowOpacity = 0.22
        chip.shadowRadius = 6
        chip.shadowOffset = CGSize(width: 0, height: -2)
        chipText.font = Self.font
        chipText.fontSize = 12
        chipText.alignmentMode = .center
        chipText.truncationMode = .end
        chipText.contentsScale = scale
        chip.addSublayer(chipText)
        pointer.addSublayer(chip)
    }

    private func draw(_ scene: OverlayScene, in size: CGSize, now: Double) {
        let reduced = reducedMotion
        paint(scene.color)
        // Where the pointer goes: where the agent acts, or out of the way in the corner while it is idle.
        let corner = CGPoint(x: max(size.width - 44, 0), y: min(44, size.height))
        let spot = scene.target.map { Self.at($0, in: size) } ?? corner
        let travel: Double
        if scene.target != shown || (scene.target == nil && parkedAt != corner) {
            travel = shown == nil && parkedAt == nil ? 0 : scene.target == nil ? 0.6 : scene.travelS
        } else {
            travel = 0
        }
        shown = scene.target
        parkedAt = scene.target == nil ? corner : nil
        CATransaction.begin()
        CATransaction.setDisableActions(reduced || travel == 0)
        CATransaction.setAnimationDuration(travel)
        CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(controlPoints: 0.2, 0.8, 0.2, 1))
        pointer.position = spot
        CATransaction.commit()

        CATransaction.begin()
        CATransaction.setDisableActions(reduced)
        CATransaction.setAnimationDuration(0.3)
        pointer.opacity = scene.pose == .acting ? 1 : scene.pose == .resting ? 0.55 : 0.45
        pointer.transform = scene.pose == .parked ? CATransform3DMakeScale(0.8, 0.8, 1) : CATransform3DIdentity
        CATransaction.commit()

        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let text = scene.label
        if (chipText.string as? String) != text {
            chipText.string = text
            let width = min(ceil((text as NSString).size(withAttributes: [.font: Self.font]).width) + 18, 240)
            // 17 points right of the tip and 17 below it, as the viewer has it.
            chip.frame = CGRect(x: Self.tip.x + 17, y: Self.size.height - Self.tip.y - 17 - 21, width: width, height: 21)
            chip.sublayers?.first?.frame = chip.bounds
            chipText.frame = chip.bounds.insetBy(dx: 9, dy: 0).offsetBy(dx: 0, dy: -3)
        }
        // A label that would run past the window's right edge, or one in the corner, sits to the pointer's left.
        let flipped = scene.pose == .parked || spot.x + 17 + chip.frame.width > size.width
        chip.frame.origin.x = flipped ? Self.tip.x - chip.frame.width - 2 : Self.tip.x + 17
        CATransaction.commit()

        if scene.failed, !reduced, pointer.animation(forKey: "shake") == nil {
            let shake = CAKeyframeAnimation(keyPath: "position.x")
            shake.values = [0, -3, 3, -3, 3, 0].map { spot.x + $0 }
            shake.duration = 0.45
            pointer.add(shake, forKey: "shake")
        }
        for mark in scene.marks where now >= mark.start && !drawn.contains(mark.id) {
            drawn.insert(mark.id)
            drawMark(mark, in: size, reduced: reduced)
        }
        drawn.formIntersection(scene.marks.map(\.id))
    }

    /// The pointer presses down as a tap lands under it, and lets go -- not with reduced motion.
    private func press(reduced: Bool) {
        guard !reduced else { return }
        let squeeze = CAKeyframeAnimation(keyPath: "transform.scale")
        squeeze.values = [1, 0.84, 1]
        squeeze.keyTimes = [0, 0.4, 1]
        squeeze.duration = 0.24
        squeeze.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.8, 0.2, 1)
        shape.add(squeeze, forKey: "press")
    }

    /// A point of the window, as shares measured down from its top left, in the overlay's layers, measured up.
    static func at(_ point: OverlayPoint, in size: CGSize) -> CGPoint {
        CGPoint(x: point.x * size.width, y: (1 - point.y) * size.height)
    }

    private func drawMark(_ mark: OverlayMark, in size: CGSize, reduced: Bool) {
        let accent = color.cgColor
        let layer = CAShapeLayer()
        layer.fillColor = nil
        layer.strokeColor = accent
        let spots = mark.points.map { Self.at($0, in: size) }
        switch mark.kind {
        case .trail:
            let path = CGMutablePath()
            path.addLines(between: spots)
            layer.path = path
            layer.lineWidth = 3
            layer.lineCap = .round
            layer.lineJoin = .round
            layer.opacity = 0.6
        case .ripple, .hold:
            // The ring is laid out around its own centre, at the point: scaling it never moves it.
            layer.bounds = CGRect(x: 0, y: 0, width: 44, height: 44)
            layer.position = spots[0]
            layer.path = CGPath(ellipseIn: layer.bounds.insetBy(dx: 1.5, dy: 1.5), transform: nil)
            layer.lineWidth = mark.kind == .ripple ? 2 : 3
            // A faint fill and a glow, as the viewer's ring has.
            layer.fillColor = color.withAlphaComponent(mark.kind == .ripple ? 0.2 : 0.14).cgColor
            layer.shadowColor = accent
            layer.shadowOpacity = 0.55
            layer.shadowRadius = 7
            layer.shadowOffset = .zero
            if mark.kind == .ripple { press(reduced: reduced) }
            let scale = CABasicAnimation(keyPath: "transform.scale")
            let fade = CABasicAnimation(keyPath: "opacity")
            if mark.kind == .ripple {
                (scale.fromValue, scale.toValue, fade.fromValue, fade.toValue) = (reduced ? 1 : 0.35, reduced ? 1 : 1.5, 0.95, 0)
            } else {
                (scale.fromValue, scale.toValue, fade.fromValue, fade.toValue) = (reduced ? 1 : 1.4, reduced ? 1 : 0.8, 0.3, 1)
            }
            let group = CAAnimationGroup()
            group.animations = [scale, fade]
            group.duration = mark.kind == .ripple ? mark.duration : max(mark.duration - OverlayModel.rippleS, 0.05)
            group.timingFunction = mark.kind == .ripple
                ? CAMediaTimingFunction(controlPoints: 0.2, 0.7, 0.3, 1) : CAMediaTimingFunction(name: .linear)
            group.fillMode = .forwards
            group.isRemovedOnCompletion = false
            layer.add(group, forKey: "mark")
        }
        marks.addSublayer(layer)
        DispatchQueue.main.asyncAfter(deadline: .now() + mark.duration) { layer.removeFromSuperlayer() }
    }
}
