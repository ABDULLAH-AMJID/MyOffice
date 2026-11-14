pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // Tesseract4Android is published only through JitPack (Apache-2.0 engine, offline OCR)
        maven { url = uri("https://jitpack.io") }
    }
}

rootProject.name = "MyOfficeAndroid"
include(":app")
