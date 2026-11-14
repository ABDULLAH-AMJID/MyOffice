package com.myoffice.app.library

import java.io.File
import java.io.InputStream
import java.util.zip.ZipInputStream

/**
 * Pulls plain text out of the document formats MyOffice opens, for the search index.
 *
 * Pure JVM on purpose (only `java.util.zip` + regex), so it can be unit tested with small
 * generated OOXML packages. PDF is handled by [PdfText] in the Android layer.
 *
 * The goal is *findability*, not fidelity: text from the whole document body, capped so a
 * 300-page report cannot fill the phone's storage with index data.
 */
object OfficeTextExtractor {

    /** Nothing larger than this is indexed — extraction cost and cache pressure grow fast. */
    const val MAX_SOURCE_BYTES = 25L * 1024 * 1024

    /** Per document ceiling for the text kept in the index. */
    const val MAX_TEXT_CHARS = 32_000

    /** Refuse absurd zip entries (a 4 KB file cannot legitimately expand to 8 MB of XML). */
    private const val MAX_ENTRY_BYTES = 8 * 1024 * 1024

    /** Every extension the indexer can pull text out of. */
    val SUPPORTED: Set<String> = setOf(
        "docx", "docm", "xlsx", "xlsm", "pptx", "pptm", "csv", "tsv", "txt", "md", "markdown", "html", "htm", "pdf",
    )

    /** Formats that hold text only as pixels: scanned PDFs and images. */
    val OCR_CANDIDATES: Set<String> = setOf(
        "pdf", "png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif",
    )

    fun canExtract(ext: String): Boolean = ext.lowercase() in SUPPORTED

    /**
     * Extract searchable text from [file], or "" when the format has none / it is too big
     * / it cannot be parsed. Never throws: one broken file must not stop the indexer.
     */
    fun extract(file: File): String {
        if (!file.isFile) return ""
        if (file.length() > MAX_SOURCE_BYTES) return ""
        val ext = file.extension.lowercase()
        val raw = runCatching { extractByExtension(file, ext) }.getOrElse { "" }
        return TextClean.cap(TextClean.normalise(raw), MAX_TEXT_CHARS)
    }

    private fun extractByExtension(file: File, ext: String): String {
        return when (ext) {
            "docx", "docm" -> docx(file)
            "xlsx", "xlsm" -> xlsx(file)
            "pptx", "pptm" -> pptx(file)
            "csv", "tsv", "txt", "md", "markdown" -> file.readText(Charsets.UTF_8)
            "html", "htm" -> TextClean.stripXml(file.readText(Charsets.UTF_8))
            else -> ""
        }
    }

    // ------------------------------------------------------------------ OOXML

    private fun docx(file: File): String {
        val body = readEntry(file, "word/document.xml") ?: return ""
        return TextClean.stripXml(body)
    }

    private fun xlsx(file: File): String {
        val out = StringBuilder()
        val shared = readEntry(file, "xl/sharedStrings.xml")
        if (shared != null) out.append(TextClean.stripXml(shared)).append('\n')
        // Sheet XML carries numbers, dates and inline strings — all worth finding.
        for (i in 1..64) {
            val sheet = readEntry(file, "xl/worksheets/sheet$i.xml") ?: continue
            val text = TextClean.stripXml(sheet)
            if (text.isNotBlank()) out.append(text).append('\n')
            if (out.length > MAX_TEXT_CHARS * 2) break
        }
        return out.toString()
    }

    private fun pptx(file: File): String {
        val out = StringBuilder()
        for (i in 1..500) {
            val slide = readEntry(file, "ppt/slides/slide$i.xml") ?: continue
            out.append(TextClean.stripXml(slide)).append('\n')
            if (out.length > MAX_TEXT_CHARS * 2) break
        }
        return out.toString()
    }

    /** Read one zip entry by name, streamed with a size cap. */
    fun readEntry(file: File, name: String): String? = runCatching {
        file.inputStream().use { input -> readEntry(input, name) }
    }.getOrNull()

    fun readEntry(input: InputStream, name: String): String? {
        ZipInputStream(input.buffered()).use { zip ->
            while (true) {
                val entry = runCatching { zip.nextEntry }.getOrNull() ?: return null
                if (entry.isDirectory || entry.name.replace('\\', '/') != name) {
                    runCatching { zip.closeEntry() }
                    continue
                }
                return zip.readCapped(MAX_ENTRY_BYTES).toString(Charsets.UTF_8)
            }
        }
    }

    private fun ZipInputStream.readCapped(limit: Int) = java.io.ByteArrayOutputStream().also { out ->
        val buffer = ByteArray(16 * 1024)
        var total = 0
        while (total < limit) {
            val read = runCatching { read(buffer) }.getOrDefault(-1)
            if (read <= 0) break
            out.write(buffer, 0, read)
            total += read
        }
    }.toByteArray()
}
