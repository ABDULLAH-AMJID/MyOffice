plugins {
    id("com.android.application") version "8.7.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}

// Keep heavy compiled build intermediates in /var/tmp so workspace stays fast and ultra-clean
subprojects {
    layout.buildDirectory.set(file("/var/tmp/myoffice-android-build/${project.name}"))
}
