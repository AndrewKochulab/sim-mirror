# Android

An Android emulator on your Mac, or an Android phone adb reaches, shows up in SimMirror beside the simulators: in the
viewer's device picker under **Android**, in `sim-mirror devices`, and to an agent. The same viewer, agent cursor and
tools drive it -- `sim_snapshot`, `sim_act`, `sim_screenshot`, `sim_app`, `sim_device`, `sim_record`, and the build
tools for a Gradle project.

| | Needs | Gives |
|---|---|---|
| **adb** | The Android SDK (Android Studio installs it), and for a phone USB debugging allowed | The device listed and picked; an emulator started and shut down; a screenshot a few times a second; touches and keys, slowly; the element tree; apps, the log, its look and place |
| **scrcpy** | scrcpy's server, set up once (**Set up touch** in the viewer, or `sim-mirror android setup`) | The live screen as H.264, touches about ten milliseconds each, and any text typed whole |

## Emulators and phones

**An emulator** runs on your Mac, like a simulator. Every AVD is listed -- one Android Studio's Device Manager made --
and picking one that is not running starts it, headless (`android.emulator.window` shows its own window too), and
waits for Android to boot: about a minute the first time, seconds from its quick-boot snapshot after. It counts
against `device.max_booted`, and one SimMirror started is shut down when it is let go or idle, as a simulator is; one
you started -- from Android Studio, say -- is used as it is and left running. An AVD whose system image is missing or
incomplete is listed, and says so rather than failing a minute into a boot.

**A phone** is a real device. It is listed while `android.phones` is on, once adb reaches it -- by cable, or over Wi-Fi
once paired -- and one that has not allowed this Mac to debug it says what to do. A person picks it, or an agent while
`android.agents_choose` lets it. SimMirror never starts, restarts or shuts a phone down, never types a passcode, and
puts back what it changed there -- dark mode, the font scale, high-contrast text, animations, the demo status bar,
which way it is turned -- exactly as it was, when it is let go. A phone's location is not adb's to set, so it has none.

SimMirror names an emulator by its AVD and a phone by its serial number (`avd-Pixel-9-3fa2c1`, `adb-R58M12ABC-9b1e04`),
so one keeps its id from one launch to the next and over USB and Wi-Fi alike.

## scrcpy

[scrcpy](https://github.com/Genymobile/scrcpy) (Genymobile, Apache-2.0) streams an Android screen and takes its input
from a small server it runs on the device. SimMirror speaks to one release of it, 5.0.1, and fetches it only when a
person sets it up: its size and SHA-256 are checked against the ones written into SimMirror, and a download that is not
that release is thrown away. `android.scrcpy.path` names a copy of your own instead -- used only when it is that
release. The server is pushed to the device while SimMirror shows it, reached through adb's own connection to the
device -- nothing listens on your Mac -- and ended when the device is let go.

Without it, a device is shown a screenshot at a time and touched through adb's `input`, a few hundred milliseconds a
touch, and only plain ASCII text can be typed.

## Settings

Everything is in the viewer's **Android** tab (`docs/reference/configuration.md` lists each): whether Android devices
are offered, the SDK, phones, how an emulator starts, scrcpy, and the JDK Gradle builds with.
