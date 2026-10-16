// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct OrientationTests {
    @Test func aScreenPointIsWhereEachOrientationDrawsIt() {
        // Measured on an iPhone 17 Pro simulator: a touch at the screen's (30, 90) landed at (90, 372) in an app turned
        // right, and at (784, 30) in one turned left.
        #expect(InterfaceOrientation.portrait.interfacePoint(x: 30, y: 90, on: iPhone17) == (30, 90))
        #expect(InterfaceOrientation.landscapeRight.interfacePoint(x: 30, y: 90, on: iPhone17) == (90, 372))
        #expect(InterfaceOrientation.landscapeLeft.interfacePoint(x: 30, y: 90, on: iPhone17) == (784, 30))
        #expect(InterfaceOrientation.portraitUpsideDown.interfacePoint(x: 30, y: 90, on: iPhone17) == (372, 784))
        for orientation in InterfaceOrientation.allCases {
            let there = orientation.interfacePoint(x: 30, y: 90, on: iPhone17)
            #expect(orientation.screenPoint(x: there.x, y: there.y, on: iPhone17) == (30, 90))
        }
        #expect(InterfaceOrientation.landscapeLeft.sideways && InterfaceOrientation.landscapeRight.sideways)
        #expect(!InterfaceOrientation.portrait.sideways && !InterfaceOrientation.portraitUpsideDown.sideways)
    }

    /// A screen showing elements, in the interface's points, found the way the translator finds them: at a screen
    /// point, through the orientation the interface is really in.
    func screen(turned: InterfaceOrientation, showing elements: [Rect]) -> (Double, Double) -> Rect? {
        { x, y in
            let point = turned.interfacePoint(x: x, y: y, on: iPhone17)
            return elements.first { $0.contains(point: point) }
        }
    }

    @Test func eachOrientationIsFoundFromWhatIsAtAScreenPoint() {
        let landscape = Rect(x: 0, y: 0, width: 874, height: 402)
        let upright = Rect(x: 0, y: 0, width: 402, height: 874)
        // A quarter of the interface at each corner.
        let wide = [0.0, 437.0].flatMap { x in [0.0, 201.0].map { Rect(x: x, y: $0, width: 437, height: 201) } }
        let tall = [0.0, 201.0].flatMap { x in [0.0, 437.0].map { Rect(x: x, y: $0, width: 201, height: 437) } }
        for turned in [InterfaceOrientation.landscapeLeft, .landscapeRight] {
            let hit = screen(turned: turned, showing: wide)
            #expect(InterfaceOrientation.find(AXNode(frame: landscape), screen: iPhone17, hit: hit) == turned)
        }
        for turned in [InterfaceOrientation.portrait, .portraitUpsideDown] {
            let hit = screen(turned: turned, showing: tall)
            #expect(InterfaceOrientation.find(AXNode(frame: upright), screen: iPhone17, hit: hit) == turned)
        }
    }

    @Test func aSparseScreenIsToldByItsOwnElements() {
        // The probe app: four 60-point squares in the interface's corners, and nothing anywhere else.
        let squares = [Rect(x: 0, y: 60, width: 60, height: 60), Rect(x: 814, y: 60, width: 60, height: 60),
                       Rect(x: 0, y: 302, width: 60, height: 60), Rect(x: 814, y: 302, width: 60, height: 60)]
        let tiny = Rect(x: 400, y: 200, width: 2, height: 2)
        let app = AXNode(frame: Rect(x: 0, y: 0, width: 874, height: 402), children: [
            AXNode(frame: Rect(x: 0, y: 0, width: 874, height: 402)), AXNode(frame: tiny),
            AXNode(children: squares.map { AXNode(frame: $0) }),
        ])
        for turned in [InterfaceOrientation.landscapeLeft, .landscapeRight] {
            #expect(InterfaceOrientation.find(app, screen: iPhone17, hit: screen(turned: turned, showing: squares)) == turned)
        }
        // Elements the hit test does not find tell nothing either.
        #expect(InterfaceOrientation.find(app, screen: iPhone17) { _, _ in nil } == .landscapeRight)
        #expect(InterfaceOrientation.leaves(app).count == 6)
    }

    @Test func aPointThatTellsNothingIsPassedOver() {
        let landscape = Rect(x: 0, y: 0, width: 874, height: 402)
        let turnedLeft = screen(turned: .landscapeLeft, showing: [Rect(x: 600, y: 0, width: 274, height: 402)])
        var asked = 0
        let found = InterfaceOrientation.find(AXNode(frame: landscape), screen: iPhone17) { x, y in
            asked += 1
            switch asked {
            case 1: return nil
            case 2: return Rect(x: 0, y: 0, width: 0, height: 0)
            // The whole interface: both landscapes put every point in it.
            case 3, 4: return landscape
            default: return turnedLeft(x, y)
            }
        }
        #expect(found == .landscapeLeft)
        #expect(asked > 4)
    }

    @Test func whenNothingTellsTheFirstOfTheTwoIsTaken() {
        let landscape = Rect(x: 0, y: 0, width: 874, height: 402)
        #expect(InterfaceOrientation.find(AXNode(frame: landscape), screen: iPhone17) { _, _ in nil } == .landscapeRight)
        #expect(InterfaceOrientation.find(AXNode(), screen: iPhone17) { _, _ in nil } == .portrait)
        let wideScreen = ScreenGeometry(widthPx: 2622, heightPx: 1206, widthPt: 874, heightPt: 402, scale: 3)
        #expect(InterfaceOrientation.find(AXNode(frame: landscape), screen: wideScreen) { _, _ in nil } == .portrait)
    }

    @Test func aTreeIsUprightUnlessItSays() {
        #expect(AXTree(root: AXNode()).orientation == .portrait)
        #expect(Rect(x: 0, y: 0, width: 10, height: 10).contains(point: (10.4, -0.4)))
        #expect(!Rect(x: 0, y: 0, width: 10, height: 10).contains(point: (10.6, 5)))
        #expect(!Rect(x: 0, y: 0, width: 10, height: 10).contains(point: (5, 10.6)))
    }
}
