package com.myoffice.app

import android.app.Application
import com.myoffice.app.ooxml.OoxmlEngine
import com.myoffice.app.pdf.PdfStudioEngine
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.Security

class MyOfficeApplication : Application() {

    lateinit var workspaceDir: File
        private set

    override fun onCreate() {
        super.onCreate()
        PDFBoxResourceLoader.init(applicationContext)
        runCatching {
            if (Security.getProvider(BouncyCastleProvider.PROVIDER_NAME) == null) {
                Security.addProvider(BouncyCastleProvider())
            }
        }
        workspaceDir = File(filesDir, "workspace").apply { mkdirs() }
        seedStarterWorkspaceIfFirstLaunch()
    }

    private fun seedStarterWorkspaceIfFirstLaunch() {
        val marker = File(filesDir, ".starter_v140_seeded")
        if (marker.exists()) return
        runCatching {
            // 1. Starter Writer Document (.docx) with English + Urdu Nastaleeq RTL
            val blocks = JSONArray()
                .put(JSONObject().put("type", "p").put("heading", 1).put("text", "Welcome to MyOffice Mobile 1.4"))
                .put(
                    JSONObject().put("type", "p").put("text",
                        "Work 100% offline on your Android phone or sync two-way with MyOffice on your Windows PC over Wi-Fi, Mobile Hotspot, Gmail Cloud Sync, or Wi-Fi Router USB Vault.")
                )
                .put(
                    JSONObject().put("type", "p").put("rtl", true).put("align", "right")
                        .put("text", "خوش آمدید! اب آپ اپنے موبائل اور کمپیوٹر پر بغیر انٹرنیٹ یا جی میل کے ذریعے تمام فائلیں سنک اور ایڈٹ کر سکتے ہیں۔")
                )
            File(workspaceDir, "Welcome Guide.docx").writeBytes(OoxmlEngine.writeDocx(blocks, "Welcome to MyOffice"))

            // 2. Starter Budget Spreadsheet (.xlsx) with live formulas
            val cells = JSONObject()
                .put("A1", JSONObject().put("raw", "Item").put("value", "Item").put("bold", true))
                .put("B1", JSONObject().put("raw", "Amount (PKR)").put("value", "Amount (PKR)").put("bold", true))
                .put("A2", JSONObject().put("raw", "Office Equipment").put("value", "Office Equipment"))
                .put("B2", JSONObject().put("raw", "45000").put("value", "45000"))
                .put("A3", JSONObject().put("raw", "Cloud & Router Backup").put("value", "Cloud & Router Backup"))
                .put("B3", JSONObject().put("raw", "12500").put("value", "12500"))
                .put("A4", JSONObject().put("raw", "Team Operations").put("value", "Team Operations"))
                .put("B4", JSONObject().put("raw", "27500").put("value", "27500"))
                .put("A5", JSONObject().put("raw", "Total").put("value", "Total").put("bold", true))
                .put("B5", JSONObject().put("raw", "=SUM(B2:B4)").put("value", "85000").put("bold", true))
            val sheets = JSONArray().put(JSONObject().put("name", "Budget").put("cells", cells))
            File(workspaceDir, "Monthly Budget.xlsx").writeBytes(OoxmlEngine.writeXlsx(sheets))

            // 3. Starter Presentation (.pptx)
            val slides = JSONArray()
                .put(
                    JSONObject()
                        .put("title", "MyOffice PC + Android Ecosystem")
                        .put(
                            "bullets",
                            JSONArray()
                                .put("100% Standalone Offline Office Suite on Android & Windows")
                                .put("Instant Wi-Fi / Hotspot QR Sync + Worldwide Gmail Sync")
                                .put("Hardware-Encrypted .movault Backups & Live Team Chat")
                        )
                        .put("notes", "Swipe left/right in Present Mode or use the Laser Pointer.")
                )
            File(workspaceDir, "Executive Pitch.pptx").writeBytes(OoxmlEngine.writePptx(slides))

            // 4. Starter PDF Guide (.pdf)
            PdfStudioEngine.writePlainDocumentPdf(
                "MyOffice PDF Studio Quick Reference",
                listOf(
                    "Open, read aloud, search, Chat with PDF, merge, split, protect (AES-256),",
                    "watermark, number pages, stamp (APPROVED / CONFIDENTIAL / PAID), and fill forms.",
                    "",
                    "Sync effortlessly with your Windows 11 PC via QR Code or Gmail App Password."
                ),
                File(workspaceDir, "Quick Reference.pdf")
            )
            marker.writeText("ok")
        }
    }
}
