# Synthetic CEF sample

A small CEF Views app with SimMirror's [Debug CEF host adapter](../../sdk/cef/README.md): a button, a text field, hover
and right-click targets, a scrolling list, a canvas for drags and long presses, and a `Sample` menu. Its page reports
its own state in an `<output aria-label="State">`, so every action can be checked from a snapshot.

```sh
make sdk-cef-sample CEF_ROOT=/path/to/cef_binary_<version>_macosarm64_minimal
open -g -n ".build/sdk/cef/sample/Release/CEF Sample.app"
```

`open -g` keeps it in the background; the sample orders its window front without activating. Then `sim_device list`
shows `CEF Sample`; choose it and drive it like any app. Build with `-DSIM_MIRROR_CEF_BRIDGE=OFF` to see what a
shipped build keeps: the accessibility hook only.
