# Android app

A grocery list in one Kotlin activity, with two instrumented tests: the smallest Android project `sim_build_run` and
`sim_test` build, install and test on an Android emulator or phone.

```
examples/android-app/
├── settings.gradle.kts         the Gradle build SimMirror finds
├── gradlew, gradle/wrapper/    its own wrapper: Gradle 9.3.1, checked by its SHA-256
└── app/                        the one app module (AGP 9.1, Kotlin built in)
    ├── src/main/               MainActivity: type an item, press Add
    └── src/androidTest/        MainActivityTest: startsEmpty, addsAnItem
```

## Try it

With an Android emulator chosen (`sim_device list`, then `choose`) and `build.tools` on, point the build at this
folder and ask the agent to

- build and run it: `sim_build_run` with `folder: "examples/android-app"` builds `:app:assembleDebug`, installs
  `app-debug.apk` and launches `dev.simmirror.example`;
- run its tests: `sim_test` runs `:app:connectedDebugAndroidTest` on the device and answers with what failed and where;
  `only_testing: ["dev.simmirror.example.MainActivityTest/addsAnItem"]` runs one;
- launch it with an item already on the list: `sim_app` `launch` with `args: ["item=Milk"]` -- each launch argument is
  a string extra of the launch intent.

Gradle needs a JDK (17 or later): Android Studio's own is found, or name one with `android.java_home`. The first build
fetches Gradle and the Android Gradle plugin, which takes a minute; later builds take seconds.

From a shell, the same build is

```sh
cd examples/android-app
./gradlew :app:assembleDebug
```

SimMirror's own suite never builds this project; `make live-android` does, on an emulator.
