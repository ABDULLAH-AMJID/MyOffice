package com.myoffice.app.library

import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.text.PDFTextStripper
import java.io.File

/**
 * Text for the search index, with PDF added on top of the OOXML extractor.
 *
 * PDFs need the bundled PDF engine, so this half stays out of the pure extractor (which is
 * unit tested). Every page is read until the character budget or the time budget runs out —
 * a 300-page manual is worth more searchable than a 10-page one. Documents whose pages carry
 * no text at all are reported as [Extraction.needsOcr] instead of being silently dropped:
 * that is exactly the scanned paperwork people search for.
 */
object DocumentText {

    /** Stop reading a single PDF after this long; indexing must never stall the library. */
    private const val PDF_TIME_BUDGET_MS = 20_000L

    /** A page with fewer characters than this is a scan, not a text page. */
    private const val MIN_TEXT_PER_PAGE = 12

    data class Extraction(val text: String, val needsOcr: Boolean = false)

    fun extract(file: File): String = extractDetailed(file).text

    fun extractDetailed(file: File): Extraction {
        if (!file.isFile) return Extraction("")
        val ext = file.extension.lowercase()
        if (ext == "pdf") {
            return runCatching { extractPdf(file) }.getOrElse { Extraction("", needsOcr = true) }
        }
        if (ext in OfficeTextExtractor.OCR_CANDIDATES) {
            // images: nothing to parse here, the OCR pass owns them
            return Extraction("", needsOcr = false)
        }
        val text = OfficeTextExtractor.extract(file)
        return Extraction(text, needsOcr = false)
    }

    private fun extractPdf(file: File): Extraction {
        if (file.length() > OfficeTextExtractor.MAX_SOURCE_BYTES) return Extraction("", needsOcr = true)
        PDDocument.load(file).use { doc ->
            val pages = doc.numberOfPages
            if (pages <= 0) return Extraction("")

            val started = System.currentTimeMillis()
            val out = StringBuilder()
            val stripper = PDFTextStripper().apply { sortByPosition = true }
            var textPages = 0

            for (page in 1..pages) {
                if (out.length >= OfficeTextExtractor.MAX_TEXT_CHARS) break
                if (System.currentTimeMillis() - started > PDF_TIME_BUDGET_MS) break
                val pageText = runCatching {
                    stripper.startPage = page
                    stripper.endPage = page
                    stripper.getText(doc)
                }.getOrDefault("")
                if (pageText.count { !it.isWhitespace() } >= MIN_TEXT_PER_PAGE) textPages++
                if (pageText.isNotBlank()) out.append(pageText).append('\n')
            }

            // Not one page with real text: this is a scan, hand it to OCR.
            if (textPages == 0 && pages > 0) return Extraction("", needsOcr = true)
            return Extraction(TextClean.cap(TextClean.normalise(out.toString()), OfficeTextExtractor.MAX_TEXT_CHARS))
        }
    }
}
