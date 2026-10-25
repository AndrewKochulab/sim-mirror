// SPDX-License-Identifier: Apache-2.0
plugins {
    id("com.android.application")
}

android {
    namespace = "dev.simmirror.example"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.simmirror.example"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
}

dependencies {
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
}
