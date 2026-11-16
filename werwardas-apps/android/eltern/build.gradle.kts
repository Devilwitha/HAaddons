plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

apply(from = "../app-common.gradle")

android {
    namespace = "de.werwardas.eltern"
    defaultConfig {
        applicationId = "de.werwardas.eltern"
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(project(":common"))
}
