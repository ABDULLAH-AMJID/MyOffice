plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val keystoreFile = rootProject.file("keystore/myoffice-release.jks")

android {
    namespace = "com.myoffice.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.myoffice.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 10602
        versionName = "1.6.2"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        create("release") {
            storeFile = keystoreFile
            storePassword = "MyOfficeRelease2026!"
            keyAlias = "myoffice"
            keyPassword = "MyOfficeRelease2026!"
            enableV1Signing = true
            enableV2Signing = true
            enableV3Signing = true
        }
    }

    buildTypes {
        getByName("debug") {
            signingConfig = signingConfigs.getByName("release")
        }
        getByName("release") {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(
                getDefaultProguardFile("proguard-android.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.all {
            it.maxHeapSize = "256m"
        }
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    androidResources {
        ignoreAssetsPattern = "!*UniCNS*:!*UniJIS*:!*UniKS*:!*Adobe-CNS*:!*Adobe-Japan*:!*Adobe-Korea*:!*GBK*"
    }

    packaging {
        resources {
            excludes += setOf(
                "META-INF/DEPENDENCIES",
                "META-INF/LICENSE",
                "META-INF/LICENSE.txt",
                "META-INF/license.txt",
                "META-INF/NOTICE",
                "META-INF/NOTICE.txt",
                "META-INF/notice.txt",
                "META-INF/ASL2.0",
                "META-INF/*.kotlin_module",
                "META-INF/versions/9/OSGI-INF/MANIFEST.MF",
                "org/bouncycastle/pqc/**"
            )
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

val preparePdfBoxHostTestResources by tasks.registering {
    val outDir = layout.buildDirectory.dir("generated/pdfboxTestRes")
    outputs.dir(outDir)
    doLast {
        val target = outDir.get().asFile
        target.mkdirs()
        gradle.gradleUserHomeDir.walkTopDown()
            .filter { it.isFile && it.name.startsWith("pdfbox-android-") && it.name.endsWith(".aar") }
            .forEach { aar ->
                copy {
                    from(zipTree(aar)) {
                        include("assets/com/tom_roush/**")
                        eachFile {
                            path = path.removePrefix("assets/")
                        }
                        includeEmptyDirs = false
                    }
                    into(target)
                }
            }
    }
}
android.sourceSets.getByName("test").resources.srcDir(layout.buildDirectory.dir("generated/pdfboxTestRes"))
tasks.matching { it.name.startsWith("process") && it.name.endsWith("UnitTestJavaRes") }.configureEach {
    dependsOn(preparePdfBoxHostTestResources)
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.webkit:webkit:1.12.1")

    // HTTP/2, REST & RFC-6455 WebSocket client for PC Sync, Cloud Sync & Live Collaboration
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Native PDF manipulation engine (Merge, Split, Compress, AES-256 Protect/Unlock, Stamp, Forms, Extract)
    implementation("com.tom-roush:pdfbox-android:2.0.27.0")

    // QR Code generation & scanning decoding (PC Pairing + Live Collaboration QR)
    implementation("com.google.zxing:core:3.5.3")

    // Wi-Fi Router USB / NAS FTP & FTPS client
    implementation("commons-net:commons-net:3.11.1")

    // Bit-exact MOVAULT1 / MOVAULTK cryptography (Scrypt N=16384,r=8,p=1 + HKDF-SHA256 + AES-256-GCM via pdfbox-android bcprov)
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
