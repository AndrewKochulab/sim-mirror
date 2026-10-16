// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct RotationTests {
    func word(_ bytes: [UInt8], at offset: Int) -> UInt32 {
        bytes[offset..<offset + 4].withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }
    }

    @Test func theMessageIsTheEventSimulatorAppSends() {
        let bytes = DeviceRotation.message(port: 0x1503, turningTo: .landscapeLeft)
        #expect(bytes.count == 108 && DeviceRotation.size == 108)
        #expect(word(bytes, at: 0x00) == 0x13)
        #expect(word(bytes, at: 0x04) == 108)
        #expect(word(bytes, at: 0x08) == 0x1503)
        #expect(word(bytes, at: 0x0c) == 0 && word(bytes, at: 0x10) == 0)
        #expect(word(bytes, at: 0x14) == 0x7b)
        #expect(word(bytes, at: 0x18) == 50 | 0x20000)
        #expect(word(bytes, at: 0x48) == 4)
        #expect(word(bytes, at: 0x4c) == 4)
        // Nothing else is set: the event's other fields are zero, as Simulator.app sends them.
        let set: Set<Int> = [0x00, 0x04, 0x08, 0x14, 0x18, 0x48, 0x4c]
        for offset in stride(from: 0, to: 108, by: 4) where !set.contains(offset) {
            #expect(word(bytes, at: offset) == 0, "offset \(offset)")
        }
    }

    @Test func eachInterfaceIsTheDeviceTurnedTheOtherWayOnItsSide() {
        // Measured on iOS 26.5: a device turned to landscape left (3) shows its interface landscape right.
        #expect(DeviceRotation.deviceOrientation(for: .portrait) == 1)
        #expect(DeviceRotation.deviceOrientation(for: .portraitUpsideDown) == 2)
        #expect(DeviceRotation.deviceOrientation(for: .landscapeRight) == 3)
        #expect(DeviceRotation.deviceOrientation(for: .landscapeLeft) == 4)
        for orientation in InterfaceOrientation.allCases {
            let bytes = DeviceRotation.message(port: 7, turningTo: orientation)
            #expect(word(bytes, at: 0x4c) == DeviceRotation.deviceOrientation(for: orientation))
        }
    }
}
