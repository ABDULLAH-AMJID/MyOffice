package com.myoffice.app.pdf

import com.tom_roush.pdfbox.multipdf.PDFMergerUtility
import com.tom_roush.pdfbox.multipdf.Splitter
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.pdmodel.PDPage
import com.tom_roush.pdfbox.pdmodel.PDPageContentStream
import com.tom_roush.pdfbox.pdmodel.common.PDRectangle
import com.tom_roush.pdfbox.pdmodel.encryption.AccessPermission
import com.tom_roush.pdfbox.pdmodel.encryption.StandardProtectionPolicy
import com.tom_roush.pdfbox.pdmodel.font.PDType1Font
import com.tom_roush.pdfbox.pdmodel.graphics.state.PDExtendedGraphicsState
import com.tom_roush.pdfbox.text.PDFTextStripper
import com.tom_roush.pdfbox.util.Matrix
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate

/**
 * Full Commercial-Grade Mobile PDF Engine powered by Apache PDFBox-Android (`com.tom-roush:pdfbox-android`):
 *  - Per-page text extraction (for Search, Read-Aloud TTS & BM25 Chat-with-PDF)
 *  - Merge multiple PDFs
 *  - Split PDF by page ranges (`1-3, 5`) or burst every N pages
 *  - AES-256 Password Protect & Unlock
 *  - Semi-transparent diagonal/center Watermark
 *  - Page Numbers (`Page 1 of N`, `1 / N`, `1`)
 *  - Commercial Vector Stamps (`APPROVED`, `CONFIDENTIAL`, `DRAFT`, `PAID`, `FINAL`, `COPY`, Custom + Date)
 *  - AcroForm Interactive Field Inspector, Form Filler & Flatten
 *  - Native Text / Markdown / Office-to-PDF Generator
 */
object PdfStudioEngine {

    fun inspectPdf(file: File, password: String = ""): JSONObject {
        PDDocument.load(file, password).use { doc ->
            val pagesText = JSONArray()
            val stripper = PDFTextStripper()
            for (p in 1..doc.numberOfPages) {
                stripper.startPage = p
                stripper.endPage = p
                pagesText.put(stripper.getText(doc).trim())
            }
            val formFields = JSONArray()
            doc.documentCatalog?.acroForm?.fields?.forEach { field ->
                formFields.put(
                    JSONObject()
                        .put("name", field.fullyQualifiedName ?: "")
                        .put("type", field.fieldType ?: "Tx")
                        .put("value", field.valueAsString ?: "")
                        .put("readOnly", field.isReadOnly)
                )
            }
            return JSONObject()
                .put("pageCount", doc.numberOfPages)
                .put("title", doc.documentInformation?.title ?: file.nameWithoutExtension)
                .put("author", doc.documentInformation?.author ?: "")
                .put("pagesText", pagesText)
                .put("formFields", formFields)
        }
    }

    fun mergePdfs(inputFiles: List<File>, outputFile: File): JSONObject {
        require(inputFiles.isNotEmpty()) { "Select at least one PDF file to merge." }
        outputFile.parentFile?.mkdirs()
        val merger = PDFMergerUtility()
        merger.destinationFileName = outputFile.absolutePath
        inputFiles.forEach { merger.addSource(it) }
        merger.mergeDocuments(null)
        val totalPages = PDDocument.load(outputFile).use { it.numberOfPages }
        return JSONObject()
            .put("ok", true)
            .put("outputPath", outputFile.absolutePath)
            .put("pages", totalPages)
            .put("size", outputFile.length())
    }

    fun splitPdf(inputFile: File, rangeSpec: String, outDir: File): JSONObject {
        outDir.mkdirs()
        val created = JSONArray()
        PDDocument.load(inputFile).use { src ->
            val total = src.numberOfPages
            val groups = parsePageRanges(rangeSpec, total)
            groups.forEachIndexed { idx, pages ->
                PDDocument().use { part ->
                    for (p1Based in pages) {
                        if (p1Based in 1..total) {
                            part.addPage(src.getPage(p1Based - 1))
                        }
                    }
                    val out = File(outDir, "${inputFile.nameWithoutExtension}_part${idx + 1}.pdf")
                    part.save(out)
                    created.put(out.absolutePath)
                }
            }
        }
        return JSONObject().put("ok", true).put("files", created)
    }

    private fun parsePageRanges(spec: String, totalPages: Int): List<List<Int>> {
        val trimmed = spec.trim()
        if (trimmed.isEmpty() || trimmed.equals("all", true)) {
            return (1..totalPages).map { listOf(it) }
        }
        if (trimmed.startsWith("every:", true)) {
            val step = trimmed.substringAfter(':').trim().toIntOrNull()?.coerceAtLeast(1) ?: 1
            return (1..totalPages).chunked(step)
        }
        return trimmed.split(',').mapNotNull { token ->
            val t = token.trim()
            if ('-' in t) {
                val a = t.substringBefore('-').trim().toIntOrNull() ?: return@mapNotNull null
                val b = t.substringAfter('-').trim().toIntOrNull() ?: return@mapNotNull null
                (minOf(a, b)..maxOf(a, b)).toList()
            } else {
                t.toIntOrNull()?.let { listOf(it) }
            }
        }.filter { it.isNotEmpty() }
    }

    fun protectPdf(inputFile: File, userPassword: String, ownerPassword: String, outputFile: File): JSONObject {
        require(userPassword.isNotEmpty()) { "Password cannot be empty." }
        outputFile.parentFile?.mkdirs()
        PDDocument.load(inputFile).use { doc ->
            val ap = AccessPermission()
            val policy = StandardProtectionPolicy(
                ownerPassword.ifEmpty { userPassword },
                userPassword,
                ap
            ).apply {
                encryptionKeyLength = 256
            }
            doc.protect(policy)
            doc.save(outputFile)
        }
        return JSONObject().put("ok", true).put("outputPath", outputFile.absolutePath).put("size", outputFile.length())
    }

    fun unlockPdf(inputFile: File, password: String, outputFile: File): JSONObject {
        outputFile.parentFile?.mkdirs()
        PDDocument.load(inputFile, password).use { doc ->
            doc.isAllSecurityToBeRemoved = true
            doc.save(outputFile)
        }
        return JSONObject().put("ok", true).put("outputPath", outputFile.absolutePath).put("size", outputFile.length())
    }

    fun watermarkPdf(
        inputFile: File,
        watermarkText: String,
        opacity: Float = 0.22f,
        fontSize: Float = 44f,
        outputFile: File
    ): JSONObject {
        outputFile.parentFile?.mkdirs()
        val safeText = asciiPdfSafe(watermarkText.ifBlank { "CONFIDENTIAL" })
        PDDocument.load(inputFile).use { doc ->
            val gs = PDExtendedGraphicsState().apply { nonStrokingAlphaConstant = opacity.coerceIn(0.05f, 1.0f) }
            for (page in doc.pages) {
                val box = page.mediaBox
                PDPageContentStream(doc, page, PDPageContentStream.AppendMode.APPEND, true, true).use { cs ->
                    cs.setGraphicsStateParameters(gs)
                    cs.setNonStrokingColor(140, 150, 165)
                    cs.beginText()
                    cs.setFont(PDType1Font.HELVETICA_BOLD, fontSize)
                    cs.setTextMatrix(
                        Matrix.getRotateInstance(
                            Math.toRadians(38.0),
                            box.width * 0.20f,
                            box.height * 0.35f
                        )
                    )
                    cs.showText(safeText)
                    cs.endText()
                }
            }
            doc.save(outputFile)
        }
        return JSONObject().put("ok", true).put("outputPath", outputFile.absolutePath)
    }

    fun addPageNumbersPdf(
        inputFile: File,
        position: String = "bottom-center",
        formatPattern: String = "Page {n} of {total}",
        startNumber: Int = 1,
        outputFile: File
    ): JSONObject {
        outputFile.parentFile?.mkdirs()
        PDDocument.load(inputFile).use { doc ->
            val total = doc.numberOfPages
            doc.pages.forEachIndexed { idx, page ->
                val num = startNumber + idx
                val label = asciiPdfSafe(
                    formatPattern
                        .replace("{n}", num.toString())
                        .replace("{total}", total.toString())
                )
                val box = page.mediaBox
                val font = PDType1Font.HELVETICA
                val fontSize = 10f
                val tw = font.getStringWidth(label) / 1000f * fontSize
                val y = if (position.startsWith("top")) box.height - 28f else 22f
                val x = when {
                    position.endsWith("left") -> 40f
                    position.endsWith("right") -> (box.width - 40f - tw).coerceAtLeast(20f)
                    else -> ((box.width - tw) / 2f).coerceAtLeast(20f)
                }
                PDPageContentStream(doc, page, PDPageContentStream.AppendMode.APPEND, true, true).use { cs ->
                    cs.setNonStrokingColor(71, 85, 105)
                    cs.beginText()
                    cs.setFont(font, fontSize)
                    cs.newLineAtOffset(x, y)
                    cs.showText(label)
                    cs.endText()
                }
            }
            doc.save(outputFile)
        }
        return JSONObject().put("ok", true).put("outputPath", outputFile.absolutePath)
    }

    fun stampPdf(
        inputFile: File,
        stampLabel: String = "APPROVED",
        includeDate: Boolean = true,
        colorHex: String = "#DC2626",
        outputFile: File
    ): JSONObject {
        outputFile.parentFile?.mkdirs()
        val (r, g, b) = parseHexRgb(colorHex)
        val mainText = asciiPdfSafe(stampLabel.uppercase().ifBlank { "APPROVED" })
        val subText = if (includeDate) LocalDate.now().toString() else ""
        PDDocument.load(inputFile).use { doc ->
            for (page in doc.pages) {
                val box = page.mediaBox
                val w = 165f
                val h = if (subText.isNotEmpty()) 48f else 34f
                val x = (box.width - w - 36f).coerceAtLeast(24f)
                val y = (box.height - h - 36f).coerceAtLeast(24f)
                PDPageContentStream(doc, page, PDPageContentStream.AppendMode.APPEND, true, true).use { cs ->
                    cs.setStrokingColor(r, g, b)
                    cs.setNonStrokingColor(r, g, b)
                    cs.setLineWidth(2.2f)
                    cs.addRect(x, y, w, h)
                    cs.stroke()
                    cs.beginText()
                    cs.setFont(PDType1Font.HELVETICA_BOLD, 14f)
                    cs.newLineAtOffset(x + 12f, y + if (subText.isNotEmpty()) 26f else 11f)
                    cs.showText(mainText)
                    cs.endText()
                    if (subText.isNotEmpty()) {
                        cs.beginText()
                        cs.setFont(PDType1Font.HELVETICA, 9f)
                        cs.newLineAtOffset(x + 12f, y + 10f)
                        cs.showText(subText)
                        cs.endText()
                    }
                }
            }
            doc.save(outputFile)
        }
        return JSONObject().put("ok", true).put("outputPath", outputFile.absolutePath)
    }

    fun fillAndFlattenPdf(
        inputFile: File,
        fieldValues: Map<String, String>,
        flatten: Boolean,
        outputFile: File
    ): JSONObject {
        outputFile.parentFile?.mkdirs()
        var updated = 0
        PDDocument.load(inputFile).use { doc ->
            val form = doc.documentCatalog?.acroForm
            if (form != null) {
                for ((name, value) in fieldValues) {
                    val f = form.getField(name)
                    if (f != null && !f.isReadOnly) {
                        runCatching {
                            f.setValue(value)
                            updated++
                        }
                    }
                }
                if (flatten) {
                    form.flatten()
                }
            }
            doc.save(outputFile)
        }
        return JSONObject()
            .put("ok", true)
            .put("updatedFields", updated)
            .put("flattened", flatten)
            .put("outputPath", outputFile.absolutePath)
    }

    fun writePlainDocumentPdf(title: String, lines: List<String>, outputFile: File): JSONObject {
        outputFile.parentFile?.mkdirs()
        PDDocument().use { doc ->
            val margin = 52f
            val lineHeight = 16f
            var page = PDPage(PDRectangle.A4)
            doc.addPage(page)
            var y = page.mediaBox.height - margin
            var cs = PDPageContentStream(doc, page)

            if (title.isNotBlank()) {
                cs.beginText()
                cs.setFont(PDType1Font.HELVETICA_BOLD, 18f)
                cs.newLineAtOffset(margin, y)
                cs.showText(asciiPdfSafe(title))
                cs.endText()
                y -= 28f
            }

            val wrapped = mutableListOf<String>()
            for (raw in lines) {
                val clean = asciiPdfSafe(raw)
                if (clean.length <= 82) {
                    wrapped.add(clean)
                } else {
                    var start = 0
                    while (start < clean.length) {
                        val end = minOf(clean.length, start + 82)
                        wrapped.add(clean.substring(start, end))
                        start = end
                    }
                }
            }

            for (ln in wrapped) {
                if (y < margin) {
                    cs.close()
                    page = PDPage(PDRectangle.A4)
                    doc.addPage(page)
                    cs = PDPageContentStream(doc, page)
                    y = page.mediaBox.height - margin
                }
                cs.beginText()
                cs.setFont(PDType1Font.HELVETICA, 11f)
                cs.newLineAtOffset(margin, y)
                cs.showText(ln)
                cs.endText()
                y -= lineHeight
            }
            cs.close()
            doc.save(outputFile)
            return JSONObject()
                .put("ok", true)
                .put("outputPath", outputFile.absolutePath)
                .put("pages", doc.numberOfPages)
                .put("size", outputFile.length())
        }
    }

    private fun parseHexRgb(hex: String): Triple<Int, Int, Int> {
        val c = hex.removePrefix("#")
        if (c.length == 6) {
            return Triple(
                c.substring(0, 2).toIntOrNull(16) ?: 220,
                c.substring(2, 4).toIntOrNull(16) ?: 38,
                c.substring(4, 6).toIntOrNull(16) ?: 38
            )
        }
        return Triple(220, 38, 38)
    }

    private fun asciiPdfSafe(s: String): String =
        s.map { ch -> if (ch.code in 32..126) ch else if (ch == '\t') ' ' else '?' }.joinToString("")
}
