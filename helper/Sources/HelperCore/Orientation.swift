// SPDX-License-Identifier: Apache-2.0
import Foundation

/// How an app's interface lies on the device's screen, as UIKit names the four ways.
///
/// A simulator's screen never turns: its framebuffer, its touches and `ScreenGeometry` are the device's portrait points
/// whichever way the interface is drawn. Accessibility answers in the interface's own points, so a tree read from an app
/// turned to landscape is 874 wide on a screen 402 wide. The orientation is what turns one into the other. The device
/// turned in Simulator.app is no different: measured on iOS 26.5, its framebuffer stays portrait too.
public enum InterfaceOrientation: String, Sendable, CaseIterable {
    case portrait
    case portraitUpsideDown = "portrait_upside_down"
    case landscapeLeft = "landscape_left"
    case landscapeRight = "landscape_right"

    /// Whether the interface is the screen turned a quarter, so its width is the screen's height.
    public var sideways: Bool {
        self == .landscapeLeft || self == .landscapeRight
    }

    /// Where a point on the screen, in the screen's portrait points, is in the interface's points.
    ///
    /// Measured on an iPhone 17 Pro simulator: an app turned to landscape right is drawn with its top along the
    /// screen's right edge, so the screen's top right corner is the interface's top left.
    public func interfacePoint(x: Double, y: Double, on screen: ScreenGeometry) -> (x: Double, y: Double) {
        let width = Double(screen.widthPt)
        let height = Double(screen.heightPt)
        switch self {
        case .portrait: return (x, y)
        case .portraitUpsideDown: return (width - x, height - y)
        case .landscapeRight: return (y, width - x)
        case .landscapeLeft: return (height - y, x)
        }
    }

    /// Where a point of the interface, in its points, is on the screen, in the screen's portrait points.
    public func screenPoint(x: Double, y: Double, on screen: ScreenGeometry) -> (x: Double, y: Double) {
        let width = Double(screen.widthPt)
        let height = Double(screen.heightPt)
        switch self {
        case .portrait: return (x, y)
        case .portraitUpsideDown: return (width - x, height - y)
        case .landscapeRight: return (width - y, x)
        case .landscapeLeft: return (y, height - x)
        }
    }

    /// How many of a tree's elements are looked for on the screen before points of it are.
    static let elementsAsked = 4

    /// The share of the screen probed for which way the interface is turned, across and down: inside the edges, and
    /// nowhere two orientations put the same interface point.
    static let probes: [(Double, Double)] = [
        (0.3, 0.25), (0.7, 0.25), (0.3, 0.75), (0.7, 0.75), (0.5, 0.15), (0.5, 0.85), (0.2, 0.5), (0.8, 0.5),
    ]

    /// Which way a tree's interface is turned, from the size of its root -- the frontmost application -- and what is
    /// found at points of the screen.
    ///
    /// An interface as wide as the screen is portrait or upside down, and one as wide as the screen is tall is one of the
    /// landscapes. Which of the two is told by asking what is at a point of the screen (`hit`, answering the frame of
    /// the element there in the interface's points, as the tree's are). The middle of one of the tree's small elements
    /// is asked for where each of the two would put it on the screen: only the right one finds an element there. Then
    /// points spread over the screen are asked: only the right one puts the point inside what is found. A point that
    /// finds nothing, or an element both would put it in, tells nothing, and the next is asked. When none tells, the
    /// first of the two: portrait, or landscape right -- where UIKit puts an app that takes either landscape.
    public static func find(
        _ root: AXNode, screen: ScreenGeometry, hit: (_ x: Double, _ y: Double) -> Rect?
    ) -> InterfaceOrientation {
        let interface = root.frame
        let wideScreen = screen.widthPt > screen.heightPt
        let quarter = interface.width > 0 && interface.height > 0 && (interface.width > interface.height) != wideScreen
        let candidates: [InterfaceOrientation] = quarter ? [.landscapeRight, .landscapeLeft] : [.portrait, .portraitUpsideDown]
        let most = Double(screen.widthPt * screen.heightPt) / 4
        let small = leaves(root).filter { $0.width >= 4 && $0.height >= 4 && $0.width * $0.height <= most }
        for frame in small.prefix(elementsAsked) {
            let middle = (x: frame.x + frame.width / 2, y: frame.y + frame.height / 2)
            let fitting = candidates.filter { candidate in
                let point = candidate.screenPoint(x: middle.x, y: middle.y, on: screen)
                return hit(point.x, point.y)?.contains(point: middle) ?? false
            }
            if fitting.count == 1 { return fitting[0] }
        }
        for (across, down) in probes {
            let x = across * Double(screen.widthPt)
            let y = down * Double(screen.heightPt)
            guard let found = hit(x, y), found.width > 0, found.height > 0 else { continue }
            let fitting = candidates.filter { found.contains(point: $0.interfacePoint(x: x, y: y, on: screen)) }
            if fitting.count == 1 { return fitting[0] }
        }
        return candidates[0]
    }

    /// The frames of the elements with nothing inside them, in the tree's order.
    static func leaves(_ node: AXNode) -> [Rect] {
        node.children.isEmpty ? [node.frame] : node.children.flatMap(leaves)
    }
}

extension Rect {
    /// Whether a point is inside, edges included, give or take half a point.
    func contains(point: (x: Double, y: Double)) -> Bool {
        let slack = 0.5
        return point.x >= x - slack && point.x <= x + width + slack && point.y >= y - slack && point.y <= y + height + slack
    }
}

/// A screen's accessibility tree and the way its interface is turned, which its frames are measured in.
public struct AXTree: Equatable, Sendable {
    public let root: AXNode
    public let orientation: InterfaceOrientation

    public init(root: AXNode, orientation: InterfaceOrientation = .portrait) {
        self.root = root
        self.orientation = orientation
    }
}
