// SPDX-License-Identifier: Apache-2.0
//
// The DTUHID message shapes and the Indigo builder signatures follow facebook/idb's FBSimulatorControl (MIT) and
// EvanBacon/serve-sim (Apache-2.0); see THIRD_PARTY_LICENSES.md.
import CoreGraphics
import Foundation
import HelperCore
import XPC

/// Input through `dtuhidd`, the daemon CoreSimulator 1155.4 and later run in each guest.
///
/// Events are XPC dictionaries sent to the guest's digitizer service, over a connection made from the service's Mach
/// port and marked as crossing from the Mac into the simulator -- without that mark the daemon sees the connection and
/// never a message.
final class DTUHIDTransport: HIDTransport, @unchecked Sendable {
    static let service = "com.apple.coredevice.feature.remote.hid.digitizer"

    let name = "dtuhid"
    private let connection: xpc_connection_t

    init(device: SimDeviceHandle) throws {
        typealias EndpointFromPort = @convention(c) (mach_port_t, UInt64, UInt64) -> xpc_object_t?
        typealias ConnectionFromEndpoint = @convention(c) (xpc_object_t) -> xpc_connection_t?
        typealias EnableSimToHost = @convention(c) (xpc_connection_t) -> Void
        guard let endpointFromPort = ObjC.symbol("xpc_endpoint_create_mach_port_4sim", as: EndpointFromPort.self),
            let connectionFromEndpoint = ObjC.symbol("xpc_connection_create_from_endpoint", as: ConnectionFromEndpoint.self),
            let enableSimToHost = ObjC.symbol("xpc_connection_enable_sim2host_4sim", as: EnableSimToHost.self)
        else { throw HelperFailure("this macOS has no XPC calls for a simulator's services") }
        guard let port = device.lookup(Self.service) else {
            throw HelperFailure("the simulator publishes no \(Self.service)")
        }
        guard let endpoint = endpointFromPort(port, 0, 0), let connection = connectionFromEndpoint(endpoint) else {
            throw HelperFailure("no XPC connection to \(Self.service)")
        }
        enableSimToHost(connection)
        xpc_connection_set_event_handler(connection) { _ in }
        xpc_connection_resume(connection)
        self.connection = connection
    }

    deinit {
        xpc_connection_cancel(connection)
    }

    func send(_ step: HIDStep) throws {
        let (type, payload) = Self.message(step)
        let message = xpc_dictionary_create(nil, nil, 0)
        xpc_dictionary_set_string(message, "messageType", type)
        xpc_dictionary_set_bool(message, "isBarrier", false)
        xpc_dictionary_set_string(message, "featureIdentifier", Self.service)
        xpc_dictionary_set_value(message, "payload", payload)
        xpc_connection_send_message(connection, message)
        let sent = DispatchSemaphore(value: 0)
        xpc_connection_send_barrier(connection) { sent.signal() }
        guard sent.wait(timeout: .now() + 2) == .success else {
            throw HelperFailure("input to the simulator did not go out within 2 seconds", status: 504)
        }
    }

    static func message(_ step: HIDStep) -> (String, xpc_object_t) {
        let payload = xpc_dictionary_create(nil, nil, 0)
        switch step {
        case .touch(let phase, let x, let y):
            let point = xpc_dictionary_create(nil, nil, 0)
            xpc_dictionary_set_double(point, "x", x)
            xpc_dictionary_set_double(point, "y", y)
            xpc_dictionary_set_value(payload, "pointOne", point)
            xpc_dictionary_set_uint64(payload, "eventType", phase.rawValue)
            xpc_dictionary_set_uint64(payload, "edge", 0)
            xpc_dictionary_set_uint64(payload, "target", 0)
            return ("IndigoDigitizerEvent", payload)
        case .button(let usage, let down):
            xpc_dictionary_set_uint64(payload, "usagePage", UInt64(usage.page))
            xpc_dictionary_set_uint64(payload, "usageCode", UInt64(usage.code))
            xpc_dictionary_set_uint64(payload, "state", down ? 1 : 2)
            return ("IndigoButtonEvent", payload)
        case .key(let code, let down):
            xpc_dictionary_set_uint64(payload, "usageCode", UInt64(code))
            xpc_dictionary_set_uint64(payload, "state", down ? 1 : 2)
            return ("IndigoKeyboardButtonEvent", payload)
        }
    }
}

/// Input through SimulatorKit's legacy HID client: Indigo messages built by SimulatorKit's own functions.
final class IndigoTransport: HIDTransport, @unchecked Sendable {
    /// The digitizer: where touches go, and the only target that acts on a HID usage.
    static let digitizer: UInt32 = 0x32

    typealias Mouse = @convention(c) (UnsafePointer<CGPoint>, UnsafePointer<CGPoint>?, UInt32, Int32, CGFloat, CGFloat, UInt32) -> UnsafeMutableRawPointer?
    typealias Arbitrary = @convention(c) (UInt32, UInt32, UInt32, UInt32) -> UnsafeMutableRawPointer?
    typealias Keyboard = @convention(c) (UInt32, UInt32) -> UnsafeMutableRawPointer?
    typealias Send = @convention(c) (AnyObject, Selector, UnsafeMutableRawPointer, ObjCBool, AnyObject?, AnyObject?) -> Void

    let name = "indigo"
    private let client: NSObject
    private let sendSelector = NSSelectorFromString("sendWithMessage:freeWhenDone:completionQueue:completion:")
    private let sendMessage: Send
    private let mouse: Mouse
    private let arbitrary: Arbitrary
    private let keyboard: Keyboard

    init(device: SimDeviceHandle) throws {
        guard let mouse = ObjC.symbol("IndigoHIDMessageForMouseNSEvent", as: Mouse.self),
            let arbitrary = ObjC.symbol("IndigoHIDMessageForHIDArbitrary", as: Arbitrary.self),
            let keyboard = ObjC.symbol("IndigoHIDMessageForKeyboardArbitrary", as: Keyboard.self)
        else { throw HelperFailure("this SimulatorKit has no Indigo message builders") }
        guard let clientClass = NSClassFromString("_TtC12SimulatorKit24SimDeviceLegacyHIDClient") as? NSObject.Type else {
            throw HelperFailure("this SimulatorKit has no legacy HID client")
        }
        typealias Initialize = @convention(c) (AnyObject, Selector, AnyObject, AutoreleasingUnsafeMutablePointer<NSError?>) -> AnyObject?
        let initSelector = NSSelectorFromString("initWithDevice:error:")
        guard let initialize = class_getMethodImplementation(clientClass, initSelector) else {
            throw HelperFailure("the legacy HID client cannot be made")
        }
        var error: NSError?
        let made = try ObjC.guarded("making the legacy HID client") {
            unsafeBitCast(initialize, to: Initialize.self)(clientClass.perform(NSSelectorFromString("alloc"))!.takeUnretainedValue(), initSelector, device.object, &error)
        }
        guard let client = made as? NSObject else {
            throw HelperFailure("the legacy HID client could not be made: \(error?.localizedDescription ?? "no reason")")
        }
        guard let sendImplementation = class_getMethodImplementation(object_getClass(client), sendSelector) else {
            throw HelperFailure("the legacy HID client cannot send")
        }
        self.client = client
        sendMessage = unsafeBitCast(sendImplementation, to: Send.self)
        self.mouse = mouse
        self.arbitrary = arbitrary
        self.keyboard = keyboard
    }

    func send(_ step: HIDStep) throws {
        let message: UnsafeMutableRawPointer?
        switch step {
        case .touch(let phase, let x, let y):
            var point = CGPoint(x: x, y: y)
            message = mouse(&point, nil, Self.digitizer, phase == .end ? 2 : 1, 1, 1, 0)
        case .button(let usage, let down):
            message = arbitrary(Self.digitizer, usage.page, usage.code, down ? 1 : 2)
        case .key(let code, let down):
            message = keyboard(code, down ? 1 : 2)
        }
        guard let message else { throw HelperFailure("SimulatorKit would not build an input message") }
        try ObjC.guarded("sending input") {
            sendMessage(client, sendSelector, message, true, nil, nil)
        }
    }
}
