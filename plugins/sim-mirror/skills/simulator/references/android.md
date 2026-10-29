# Android

Choose an Android entry returned by `sim_device list`. `boot` starts an emulator headless; on a phone it opens a
session. If `android.agents_choose` refuses, the person selects the phone in the viewer. Never silently substitute a
target, power off a phone or enter its PIN.

Use the same snapshot/action loop. Android supports `back`, `home`, `app_switch`, `lock` and volume keys; follow the
connector's supported keys. `sim_app` takes the package name as `bundle_id`, and an `.apk` or split-APK folder.

Without scrcpy, adb gives slower screen/input and ASCII-only typing. Follow the note; **Set up touch** sets up
scrcpy. Do not retry unsupported Unicode through the same path. A disconnected/unauthorized phone needs its cable
or the person's debugging approval; do not restart the machine's adb server.

A missing emulator image needs installation, not repeated boot calls. Preserve the chosen AVD and existing sessions.
Display/location/recording capabilities vary; read refusals before changing approach and restore task-owned changes.

For Gradle, `scheme` is the module, `configuration` the variant, and test ids are `package.Class` or
`package.Class/method`. `sim_test` runs instrumented tests; host JVM tests use the project's normal commands.
Poll a returned `build_id` instead of starting another build.
