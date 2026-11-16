plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

apply(from = "../app-common.gradle")

android {
    namespace = "de.werwardas.kind"
    defaultConfig {
        applicationId = "de.werwardas.kind"
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(project(":common"))
}
