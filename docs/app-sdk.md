# The app SDK

A screen whose controls say nothing to accessibility reads badly to an agent: an icon-only button has no name, a card
with a tap gesture is only its text, a control drawn by hand is not there at all. **SimMirrorKit** is a small Swift
package an app under development links so SimMirror reads the app's own UIKit and SwiftUI views instead, and snapshots
name and add what accessibility left out.

It is optional and never required: nothing changes for an app without it. It does anything only in a **Debug build on
the iOS Simulator**; in a Release build, on a device, in an Xcode preview or an app extension, every call does nothing
and none of its workings are compiled in.

## Add it

In Xcode, **File → Add Package Dependencies…**, enter `https://github.com/AndrewKochulab/sim-mirror`, and add the
**SimMirrorKit** library to your app target. In a `Package.swift`:

```swift
.package(url: "https://github.com/AndrewKochulab/sim-mirror", from: "1.1.0"),
// and in your app target's dependencies:
.product(name: "SimMirrorKit", package: "sim-mirror"),
```

The package is at the top of this repository; SimMirror's Python daemon and native helper are not part of it. It needs
iOS 16 or later and Swift 6.

## Start it

Call `SimMirror.start()` once, early -- in your `App`'s `init`, or `application(_:didFinishLaunchingWithOptions:)`:

```swift
import SimMirrorKit
import SwiftUI

@main
struct NotesApp: App {
    init() {
        SimMirror.start()
    }

    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
```

That is all SimMirror needs: the next snapshot of the app reads its views. The viewer shows a **`Notes · SDK`** chip
beside the device's name while it does, `sim-mirror doctor` has an **app hierarchy** check, and
`sim-mirror app hierarchy` prints what the app in front shares. [examples/app-sdk](../examples/app-sdk/) is a small app
that uses every part of it.

## What SimMirror reads

The app walks its windows from the front, the key window first, on the main thread when SimMirror asks, and answers
with the views that show:

- **What each view is**, in a snapshot's words: a `UIButton` is a button, a `UISwitch` a switch with its `1` or `0`, a
  `UISegmentedControl` segments with the selected one, a cell a cell. A view of the app's own is read by its
  accessibility traits, then by a tap gesture it has -- which makes it something to tap -- and otherwise holds others.
- **What each says**: its accessibility label, else its title or text, else the text of the labels inside it, its
  image's name (`trash`, an asset's name), its accessibility identifier, and for a control of the app's own its type's
  name (`RatingControl` is "Rating control").
- **Only what can be seen**: hidden and transparent views, views clipped away, and what a presented view controller or
  an `accessibilityViewIsModal` view hides are left out. An alert, sheet, full-screen cover or popover in front is said
  as the modal, and where the keyboard is.
- **No secrets**: a secure field's text is never read, and `redactValues` leaves every value out.

A snapshot merges it with the accessibility tree the connector reads, as it does [Xcode's
hierarchy](connectors.md#merging-xcodes-hierarchy): accessibility's tree is kept whole, and the app's **adds** what it
does not say in the same place, and **names** what it found with no label -- `connectors.app.name_unlabeled`. A named
element keeps its place and its source, so a tap lands where it did; its ref changes once, as a renamed element's does.
Measured on the sample app's UIKit form (iPhone 17 Pro, iOS 26.5, idb): the fields read `field "Title"` rather than
`field ="Title"`, and the snapshot gained the icon segments with their value, the stepper, the hand-drawn rating control
as `slider "Rating" ="3 of 5"`, the card with a tap gesture as a button an agent then tapped, and the tab bar.

## Name views yourself

SwiftUI views that are not UIKit underneath -- most of them -- are not in the walk, and some views are best named by
the person who wrote them. Tag a SwiftUI view with **`.simMirror`**:

```swift
Button { showSettings() } label: { Image(systemName: "gearshape") }
    .simMirror("Settings")

Toggle("", isOn: $notifications).labelsHidden()
    .simMirror(kind: .switch, name: "Notifications", identifier: "notifications")

PromoCard()
    .onTapGesture { open() }
    .simMirror(kind: .button, name: "Play daily mix")
```

A tag puts an invisible view behind the tagged one, in its exact place -- in a sheet, a list row or a cell alike. The
name a tag gives is the developer's own, so it names what is in its place even when accessibility already calls it
something else: the button above reads `button "Settings"`, not `button "gearshape"`. A tag with a kind is added where
nothing is. Outside a Debug build on the simulator `.simMirror` returns the view unchanged.

For a UIKit view of your own, **register a describer**:

```swift
struct RatingControlDescriber: ViewDescribing {
    func describe(_ view: UIView) -> ViewDescription? {
        guard let rating = view as? RatingControl else { return nil }
        return ViewDescription(kind: .slider, label: "Rating", value: "\(rating.value) of 5")
    }
}

SimMirror.register(describer: RatingControlDescriber())
```

Describers are asked before SimMirror's own reading, in the order registered; `nil` leaves a view to the rest, and
`ViewDescription.hidden` leaves it, and everything inside it, out.

## SwiftUI's debug data

SwiftUI keeps debug data of its own views, which tells which have a tap gesture and which images they show. SimMirrorKit
can read it, **opt-in and on iOS 26 only**:

```swift
SimMirror.start(SimMirror.Options(swiftUIDebugData: true))
```

It is SwiftUI's undocumented debugging aid, and it costs. On iOS 26.5 the sample app's first read held the main thread
for about five seconds, so the SDK reads it once a second after starting; later reads took 135 to 165 ms each, against
1 to 30 ms for the walk alone, so raise `connectors.app.timeout_ms` to 1000 while it is on. Reading it stops the app on
iOS 27, so the SDK refuses there and the answer says so. `start` must run before any SwiftUI view is built -- the
`App`'s `init` -- or SwiftUI records nothing. SwiftUI lays each view out in its own space, not the screen's; the SDK
places a view only where the UIKit views SwiftUI placed pin that space down, and leaves out the rest rather than guess.
With it on, the sample app's card with an `onTapGesture` read as `button "Daily mix"`, and an agent's tap on it
counted.

## Options

| Option | Default | What it does |
|---|---|---|
| `port` | `0` | The port to listen on, on 127.0.0.1 only; `0` lets the system pick one each launch |
| `maxNodes` | `3000` | The most views an answer holds; SimMirror may ask for fewer, and a larger answer says it was cut short |
| `redactValues` | `false` | Leaves every value out, for screens with data you would rather not hand an agent |
| `swiftUIDebugData` | `false` | Also reads SwiftUI's debug data, on iOS 26 only |

`SimMirror.stop()` stops answering, and `SimMirror.isRunning` says whether it does.

## Settings

In the settings panel's **Connectors** tab, per project or for every project:

| Setting | Default | |
|---|---|---|
| `connectors.app.merge` | on | Whether snapshots read the hierarchy an app in front shares at all |
| `connectors.app.name_unlabeled` | on | Whether it names what accessibility found without a label, and what `.simMirror` named, or only adds |
| `connectors.app.timeout_ms` | 500 | How long a snapshot waits for the app, before going on without it |
| `connectors.app.max_nodes` | 3000 | The most views read |

See [the configuration reference](reference/configuration.md#connectorsappmerge).

## How SimMirror finds the app, and who else can

When it starts, the app listens on `127.0.0.1` on a port of its own, makes a new random 32-byte secret, and writes a
**listing** -- its bundle identifier, process, port and secret -- to the simulator's data folder, at
`Library/Caches/SimMirror/apps/<bundle id>.json`, readable by the Mac user only (0600), or to the app's own caches when
that folder cannot be written. It rewrites the listing as it comes to the front or leaves it, and removes it when it
stops. A snapshot reads the listings of the device's apps, trusting one only when it is a regular file owned by the
Mac user, readable by nobody else, for this device and a process still running, and asks the app in front for its
hierarchy with the secret in an `Authorization` header.

The app answers only a request with the secret, checked before anything else and never logged; it refuses one from a web
page (an `Origin` header) or addressed to another host, answers only a small `GET` of `/v1/hierarchy`, two at a time,
and while the app is not in front answers 409 without reading anything. What it answers is the app's view hierarchy, which an agent
driving the simulator could already see on screen. `make sdk-release-check` builds the sample app for Release and fails
if the build holds any of it. The wire format is [protocol/app-sdk/v1](../protocol/README.md#app-sdk-protocol-version-1),
versioned apart from SimMirror's screen protocol. See [Security](security.md#apps-that-share-their-hierarchy).

## Limits

- A **Debug build**: the SDK compiles its workings only where `DEBUG` is defined, as it is for a package built in
  Xcode's Debug configuration. A configuration of your own that SwiftPM builds for release leaves them out.
- **The iOS Simulator**, iOS 16 or later. A device, Mac Catalyst and app extensions are not read.
- **SwiftUI** is read through its UIKit views, `.simMirror` tags and, opt-in on iOS 26, its debug data. A view none of
  these reach reads as accessibility reads it.
- **One app**, the one in front. An app suspended in the debugger does not answer; the snapshot goes on without it,
  and says so while the app is in front.

## Developing it

The package's tests run on an iOS simulator, and the sample app's hosted tests reach what a unit test cannot -- a real
application's scenes and a presentation:

```sh
make sdk-lint sdk-test sdk-app-test sdk-release-check        # with SDK_DEVELOPER_DIR and SDK_DESTINATION
make sdk-coverage SDK_DESTINATION="platform=iOS Simulator,name=iPhone 17"   # every source file at 98%, on iOS 26
```

`SDK_DEVELOPER_DIR` picks the Xcode -- never `xcode-select` -- and `SDK_DESTINATION` the simulator. CI runs the tests on
its own Xcode; the coverage gate runs on iOS 26, where every path, SwiftUI's debug data's included, can be reached.
