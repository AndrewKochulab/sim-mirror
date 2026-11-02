# Native macOS sample

This app has AppKit and SwiftUI windows, ordinary and secure fields, a long list, an unlabelled switch, a drawn view,
a sheet, a popover and a menu. Its data is synthetic.

```sh
make sdk-mac-sample
open -g examples/mac-app-sdk/.build/MacAppSDK.app
```

Choose it with `sim_device list` and `choose`. Use `sim_device windows` to select a window. The `Sample → Reset` menu
is callable without opening a menu on screen. The SDK serves only Debug builds; `make sdk-mac-release-check` proves
its server and input markers are absent from Release.

`MacAppSDK.xcodeproj` also supports `sim_build_run` and the unhosted unit-test scheme `MacAppSDK`. Run its logic tests
with `make sdk-mac-app-test`. These are unit tests, not XCUITest, and do not launch a GUI test host.

See [macOS apps](../../docs/macos.md) for helper permissions and input limits. The sample starts the SDK early enough
for optional macOS 26 SwiftUI debug data; its normal accessibility tree remains available without that data.
