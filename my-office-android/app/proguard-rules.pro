# Keep 100% of all MyOffice native Kotlin engines, WebView @JavascriptInterface bridge, & models
-keep class com.myoffice.app.** { *; }
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Keep PDFBox, BouncyCastle symmetric/KDF/JCE crypto, ZXing QR, Commons-Net FTP, & OkHttp intact
-keep class com.tom_roush.** { *; }
-keep class org.bouncycastle.crypto.** { *; }
-keep class org.bouncycastle.jcajce.** { *; }
-keep class org.bouncycastle.jce.** { *; }
-keep class com.google.zxing.** { *; }
-keep class org.apache.commons.net.** { *; }
-keep class okhttp3.** { *; }
-keep class okio.** { *; }

-dontwarn org.bouncycastle.**
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.conscrypt.**
-dontwarn org.openjsse.**
-dontwarn org.codehaus.mojo.**
-dontwarn com.gemalto.jp2.**
-dontoptimize
-dontobfuscate
