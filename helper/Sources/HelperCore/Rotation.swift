// SPDX-License-Identifier: Apache-2.0
import Foundation

/// The message that turns a simulated device, as Simulator.app's Device › Rotate does.
///
/// Simulator.app sends it through `-[SimDevice(GSEvents) gsEventsSendOrientation:]`: a GraphicsServices event, sent
/// with `mach_msg_send` to the device's `PurpleWorkspacePort`. It is a Mach message header followed by a GSEvent record
/// whose payload is a `UIDeviceOrientation`. Measured on iOS 26.5, iPhone and iPad: the guest's `UIDevice.orientation`
/// follows it every time, and an app that takes that orientation turns its interface to match.
///
/// Every number is the Mac's own byte order, as the message is sent in this process:
///
///     0x00 u32 bits        0x13, MACH_MSG_TYPE_COPY_SEND for the remote port
///     0x04 u32 size        (info size + 0x6b) rounded down to 4: 108
///     0x08 u32 remote      the device's PurpleWorkspacePort
///     0x0c u32 local       0
///     0x10 u32 voucher     0
///     0x14 u32 id          0x7b
///     0x18 u32 type        50 (device orientation changed) | 0x20000
///     0x48 u32 info size   4
///     0x4c u32 payload     the UIDeviceOrientation
public enum DeviceRotation {
    static let bits: UInt32 = 0x13
    static let messageID: UInt32 = 0x7b
    static let eventType: UInt32 = 50 | 0x20000
    static let infoSize: UInt32 = 4
    /// The size the header says, and the bytes sent.
    public static let size = Int((infoSize + 0x6b) & ~3)

    /// The `UIDeviceOrientation` that turns an app's interface to `interface`.
    ///
    /// UIKit names the device and the interface the other way round when they lie on their side: a device turned to
    /// landscape left (3, Home button on the right) shows its interface landscape right, as measured on iOS 26.5.
    public static func deviceOrientation(for interface: InterfaceOrientation) -> UInt32 {
        switch interface {
        case .portrait: return 1
        case .portraitUpsideDown: return 2
        case .landscapeRight: return 3
        case .landscapeLeft: return 4
        }
    }

    /// The message's bytes, to be sent to `port`.
    public static func message(port: UInt32, turningTo interface: InterfaceOrientation) -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: size)
        func put(_ value: UInt32, at offset: Int) {
            withUnsafeBytes(of: value) { bytes.replaceSubrange(offset..<offset + 4, with: $0) }
        }
        put(bits, at: 0x00)
        put(UInt32(size), at: 0x04)
        put(port, at: 0x08)
        put(messageID, at: 0x14)
        put(eventType, at: 0x18)
        put(infoSize, at: 0x48)
        put(deviceOrientation(for: interface), at: 0x4c)
        return bytes
    }
}
