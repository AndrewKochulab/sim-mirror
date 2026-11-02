// SPDX-License-Identifier: Apache-2.0
import AppKit
import SimMirrorKit
import SwiftUI

/// Both window trees deliberately include controls that cannot be identified from accessibility labels alone.
@MainActor
final class SampleDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource,
    NSTableViewDelegate
{
    var windows: [NSWindow] = []
    var counter = Counter()
    let status = NSTextField(labelWithString: "Ready")
    let popover = NSPopover()

    func applicationDidFinishLaunching(_ notification: Notification) {
        SimMirror.start(.init(swiftUIDebugData: true))
        let menu = NSMenu()
        let root = NSMenuItem(title: "Sample", action: nil, keyEquivalent: "")
        let actions = NSMenu(title: "Sample")
        let reset = NSMenuItem(title: "Reset", action: #selector(reset), keyEquivalent: "r")
        reset.target = self
        actions.addItem(reset)
        root.submenu = actions
        menu.addItem(root)
        NSApp.mainMenu = menu
        let window = makeWindow("AppKit sample", x: 70)
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        for (title, action) in [
            ("Increment", #selector(increment)), ("Show sheet", #selector(sheet)),
            ("Show popover", #selector(showPopover)),
        ] {
            let button = NSButton(title: title, target: self, action: action)
            button.identifier = NSUserInterfaceItemIdentifier(title)
            stack.addArrangedSubview(button)
        }
        let field = NSTextField(string: "")
        field.placeholderString = "Name"
        field.setAccessibilityIdentifier("name")
        let secure = NSSecureTextField(string: "")
        secure.placeholderString = "Secret"
        secure.setAccessibilityIdentifier("secret")
        let toggle = NSSwitch()
        toggle.setAccessibilityLabel("Notifications")
        stack.addArrangedSubview(field)
        stack.addArrangedSubview(secure)
        stack.addArrangedSubview(toggle)
        stack.addArrangedSubview(status)
        let painted = PaintedView(frame: NSRect(x: 0, y: 0, width: 300, height: 50))
        painted.heightAnchor.constraint(equalToConstant: 50).isActive = true
        stack.addArrangedSubview(painted)
        let list = NSTableView()
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("items"))
        column.width = 390
        list.addTableColumn(column)
        list.headerView = nil
        list.rowHeight = 24
        list.dataSource = self
        list.delegate = self
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.documentView = list
        scroll.heightAnchor.constraint(equalToConstant: 220).isActive = true
        stack.addArrangedSubview(scroll)
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(stack)
        if let content = window.contentView {
            NSLayoutConstraint.activate([
                stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
                stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
                stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            ])
        }
        let swiftUI = makeWindow("SwiftUI sample", x: 600)
        swiftUI.contentView = NSHostingView(rootView: SampleView())
        for window in windows { window.orderBack(nil) }
    }

    func makeWindow(_ title: String, x: Double) -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: x, y: 100, width: 480, height: 640),
            styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = title
        window.isReleasedWhenClosed = false
        windows.append(window)
        return window
    }
    func numberOfRows(in tableView: NSTableView) -> Int { 100 }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        NSTextField(labelWithString: "Item \(row + 1)")
    }
    @objc func increment() {
        counter.increment()
        status.stringValue = "Count \(counter.value)"
    }
    @objc func reset() {
        counter.reset()
        status.stringValue = "Ready"
    }
    @objc func sheet() {
        guard let window = windows.first else { return }
        let sheet = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 300, height: 150), styleMask: [.titled],
            backing: .buffered,
            defer: false)
        let dismiss = NSButton(title: "Dismiss", target: self, action: #selector(dismissSheet))
        dismiss.frame = NSRect(x: 90, y: 50, width: 120, height: 35)
        sheet.contentView?.addSubview(dismiss)
        window.beginSheet(sheet)
    }
    @objc func dismissSheet() {
        if let window = windows.first, let sheet = window.attachedSheet { window.endSheet(sheet) }
    }
    @objc func showPopover(_ sender: NSButton) {
        let controller = NSViewController()
        controller.view = NSTextField(labelWithString: "App-owned popover")
        popover.contentViewController = controller
        popover.contentSize = NSSize(width: 220, height: 80)
        popover.show(relativeTo: sender.bounds, of: sender, preferredEdge: .maxX)
    }
    func applicationWillTerminate(_ notification: Notification) { SimMirror.stop() }
}

final class PaintedView: NSView {
    var marked = false
    override func draw(_ dirtyRect: NSRect) {
        (marked ? NSColor.systemGreen : NSColor.systemBlue).setFill()
        bounds.fill()
    }
    override func mouseDown(with event: NSEvent) {
        marked.toggle()
        needsDisplay = true
    }
}

struct SampleView: View {
    @State private var count = Counter()
    @State private var on = false
    @State private var name = ""
    @State private var secret = ""
    var body: some View {
        VStack(alignment: .leading) {
            Button("Increment \(count.value)") { count.increment() }
            Toggle("", isOn: $on)
            TextField("Name", text: $name)
            SecureField("Secret", text: $secret)
            Text("Tap gesture \(count.value)").onTapGesture { count.increment() }
            List(1...100, id: \.self) { Text("Row \($0)") }
            Button("Delete item", role: .destructive) { count.reset() }
        }.padding()
    }
}

@main
struct Main {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = SampleDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
