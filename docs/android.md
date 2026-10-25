# Android

An Android emulator on your Mac, or an Android phone adb reaches, shows up in SimMirror beside the simulators: in the
viewer's device picker under **Android**, in `sim-mirror devices`, and to an agent. The same viewer, agent cursor and
tools drive it -- `sim_snapshot`, `sim_act`, `sim_screenshot`, `sim_app`, `sim_device`, `sim_record`, and the build
tools for a Gradle project.

| | Needs | Gives |
|---|---|---|
| **adb** | The Android SDK (Android Studio installs it), and for a phone USB debugging allowed | The device listed and picked; an emulator started and shut down; a screenshot a few times a second; touches and keys, slowly; the element tree; apps, the log, its look and place |
| **scrcpy** | scrcpy's server, set up once (**Set up touch** in the viewer, or `sim-mirror android setup`) | The live screen as H.264, touches in tens of milliseconds, and any text typed whole |

`sim-mirror android status` -- or `sim-mirror doctor` -- says which of these this Mac has, and what is missing.

## Setting it up

1. Install [Android Studio](https://developer.android.com/studio). It brings the SDK -- adb and the emulator -- and a
   JDK for Gradle. SimMirror finds the SDK in `android.sdk_path`, else `$ANDROID_HOME`, else `$ANDROID_SDK_ROOT`, else
   Android Studio's own, `~/Library/Android/sdk`.
2. Make an emulator in Android Studio's **Device Manager**, or plug in a phone with
   [USB debugging](https://developer.android.com/studio/debug/dev-options) on and allow this Mac when it asks.
3. Pick it in the viewer, or `sim-mirror devices choose <udid>`. The viewer's **Set up touch** badge fetches scrcpy's
   server once, so the screen is live; `sim-mirror android setup` does the same from a terminal.

Android needs no Xcode: on a Mac with the SDK and no Xcode, the doctor warns of Xcode rather than failing, and the
Android devices are what SimMirror offers.

## Emulators and phones

**An emulator** runs on your Mac, like a simulator. Every AVD is listed -- one Android Studio's Device Manager made --
and picking one that is not running starts it, headless (`android.emulator.window` shows its own window too), and
waits for Android to boot: about a minute the first time, seconds from its quick-boot snapshot after. It counts
against `device.max_booted`, and one SimMirror started is shut down when it is let go or idle, as a simulator is; one
you started -- from Android Studio, say -- is used as it is and left running. Which emulators SimMirror started is
written beside its other run files, so a restarted daemon still knows. An AVD whose system image is missing or
incomplete is listed, and says so rather than failing a minute into a boot.

**A phone** is a real device. It is listed while `android.phones` is on, once adb reaches it -- by cable, or over Wi-Fi
once paired -- and one that has not allowed this Mac to debug it says what to do. A person picks it, or an agent while
`android.agents_choose` lets it. SimMirror never starts, restarts or shuts a phone down, never types a passcode, and
puts back what it changed there -- dark mode, the font scale, high-contrast text, animations, the demo status bar,
which way it is turned -- exactly as it was, when it is let go. A phone's location is not adb's to set, so it has none.

SimMirror names an emulator by its AVD and a phone by its serial number (`avd-Pixel-9-3fa2c1`, `adb-R58M12ABC-9b1e04`),
so one keeps its id from one launch to the next and over USB and Wi-Fi alike. A phone that has not allowed this Mac
yet has no serial number to read, so it is named by adb's serial until it does.

## In the viewer

The picker lists Android devices in a group of their own, each saying how it is reached -- **Emulator**, **USB** or
**Wi-Fi** -- and a phone still to be allowed cannot be picked until it is. The chip beside the name says **Android**
and how it is connected. The toolbar has the buttons an Android device has: **Back**, **Home**, **Recent apps**,
**Lock**, and the volume. **Shut down this device** is offered for an emulator, never for a phone. The Display menu
offers a demo status bar, text size, contrast and reduced motion, as on a real iPhone.

A device shown through adb alone has the **Set up touch** badge: it fetches scrcpy's server, and the viewer switches to
the live screen without picking the device again.

## For an agent

The tools work as on a simulator. What differs:

- `sim_act` presses Android's buttons: `{"press": "back"}`, `"home"`, `"app_switch"`, `"lock"`, `"volume_up"`,
  `"volume_down"`. A press a device cannot make is refused with the buttons it can.
- `sim_app` takes a package name as its `bundle_id`. `install` takes an `.apk`, or a folder of an app's split APKs.
  `launch` arguments are `key=value` pairs, each a string extra of the launch intent: an Android app reads its intent,
  not a command line. `open_url` refuses `intent:` and `content:` URLs, as it refuses `file:`.
- `sim_device rotate` turns the display, which is put back when the device is let go. `location` and a route move an
  emulator; a phone has none.
- Typing goes through scrcpy where it is set up, any text; through adb alone, plain ASCII only, and anything else is
  refused with how to set scrcpy up.
- `sim_snapshot` reads the screen with Android's UI Automator, the keyboard and the system bars too, in points as an
  iPhone's are; a screen UI Automator cannot read in time is read from its pixels.

## Building with Gradle

`sim_build_run` and `sim_test` build an Android project with its own Gradle wrapper -- never a Gradle the Mac happens to
have -- when the scope's device is an Android one:

```
./gradlew --console=plain :app:assembleDebug                      sim_build_run, then installed and launched
./gradlew --console=plain :app:connectedDebugAndroidTest           sim_test, on the device
```

- `scheme` names the app module -- `app`, or `:feature:app` -- else the one module that applies the Android application
  plugin. `configuration` names the build variant -- `debug`, `release`, `freeDebug` -- as a person types it (`Debug`
  is `debug`). `project`, `workspace` and `test_plan` mean nothing to Gradle and are refused.
- The APK and its package come from what AGP writes beside it (`output-metadata.json`). A build of another app than
  `build.expected_bundle_id` is not installed, and a test run held to it builds the app first and runs no test, and
  installs nothing, when it is another.
- Tests run on the device with the app left installed: left to itself, AGP uninstalls the app after its tests, and with
  it everything it had on the device. `only_testing` and `skip_testing` name a test class --
  `dev.example.MainActivityTest` -- or one test of it -- `dev.example.MainActivityTest/addsAnItem` -- as a failure names
  it. `retries` runs the tests that failed again; one that passes on a later go is reported as flaky. `destination` may
  name another Android device that is running.
- A failure is placed where it happened in the project, from the test's own stack frame; a compile error -- Kotlin,
  javac, or a resource the linker could not find -- from Gradle's log.
- Gradle runs with `JAVA_HOME` set to the JDK `android.java_home` names, else `$JAVA_HOME`, else Android Studio's, else
  the one macOS picks, and `ANDROID_HOME` set to the device's SDK.
- With `build.stamp_source` on, an emulator keeps the commit an app was built from beside it, and `sim_app info` reads
  it back. The APK itself is never changed or signed again; an install by anything else leaves the stamp unread. A
  phone's app is not stamped: SimMirror writes nothing to a phone but the app it installs.
- Cancelling or timing out ends the Gradle client; the Gradle daemon it talked to drops the build, and idles out as it
  does after any build.

[`examples/android-app`](../examples/android-app/) is a small project to try it with.

## scrcpy

[scrcpy](https://github.com/Genymobile/scrcpy) (Genymobile, Apache-2.0) streams an Android screen and takes its input
from a small server it runs on the device. SimMirror speaks to one release of it, 5.0.1, and fetches it only when a
person sets it up: its size and SHA-256 are checked against the ones written into SimMirror, and a download that is not
that release is thrown away. `android.scrcpy.path` names a copy of your own instead -- used only when it is that
release. The server is pushed to the device while SimMirror shows it, reached through adb's own connection to the
device -- nothing listens on your Mac -- and ended when the device is let go.

Without it, a device is shown a screenshot at a time and touched through adb's `input`, a few hundred milliseconds a
touch, and only plain ASCII text can be typed.

## From a terminal

| Command | What it does |
|---|---|
| `sim-mirror android status` | What is set up: the SDK, adb's server, the AVDs, the devices, scrcpy, the JDK (`--json` too) |
| `sim-mirror android avds` | The AVDs, and what stops one that cannot start |
| `sim-mirror android setup` | Fetch scrcpy's server, check it, and have a running daemon start it on the scope's device |
| `sim-mirror android clean` | Remove what SimMirror left on each device adb lists: this Mac's scrcpy server, and build stamps |
| `sim-mirror devices` | List every device, Android ones among them, and choose one |

None of them starts an emulator, or adb's server.

## What SimMirror leaves on a device

- **scrcpy's server**, at `/data/local/tmp/sim-mirror-scrcpy-<host>.jar`, pushed once and run while the device is shown.
- **Build stamps**, on an emulator, in `/data/local/tmp/sim-mirror/stamps`.
- **Apps** `sim_build_run` and `sim_test` installed -- the app, and its `.test` package -- which stay, as Android
  Studio leaves them.

`sim-mirror android clean` removes the first two. Everything SimMirror changes in a device's settings is put back when
it is let go.

## Measured

On an API 36 emulator (arm64) on an Apple-silicon Mac:

| | adb alone | scrcpy |
|---|---|---|
| Attach | 0.06 s | 0.6 s; the first frame 0.5 s later |
| A screenshot | 0.18 s (`screencap -j`) | the same |
| The screen | a few frames a second | H.264, a key frame each second |
| A swipe | hundreds of milliseconds | 40 ms to send |
| Reading the screen | 2--4 s (UI Automator) | the same |

A Gradle build of `examples/android-app` takes about 11 s once its dependencies are fetched; its two instrumented
tests, about 9 s.

## Settings

Everything is in the viewer's **Android** tab ([configuration](reference/configuration.md) lists each): whether Android
devices are offered, the SDK, phones, whether agents may pick one, how an emulator starts, scrcpy, and the JDK Gradle
builds with.

## Not yet

- A phone SimMirror's own tests have reached: the phone paths are tested against a fake adb server only.
- Audio, and a phone's location.
- Making an AVD: SimMirror lists and starts the ones Android Studio made, and makes none of its own.
