package com.myoffice.app.library

import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.text.PDFTextStripper
import java.io.File

/**
 * Text for the search index, with PDF added on top of the OOXML extractor.
 *
 * PDFs are the one format that needs the Android PDF engine, so this half stays out of the
 * pure extractor (which is unit tested). Page range is capped: a searchable index of the
 * first 30 pages is worth more than a five-minute index pass over a 400-page manual.
 */
object DocumentText {

    private const val MAX_PDF_PAGES = 30

    fun extract(file: File): String {
        if (!file.isFile) return ""
        val ext = file.extension.lowercase()
        if (ext == "pdf") {
            val pdf = runCatching { extractPdf(file) }.getOrElse { "" }
            return TextClean.cap(TextClean.normalise(pdf), OfficeTextExtractor.MAX_TEXT_CHARS)
        }
        return OfficeTextExtractor.extract(file)
    }

    private fun extractPdf(file: File): String {
        if (file.length() > OfficeTextExtractor.MAX_SOURCE_BYTES) return ""
        return PDDocument.load(file).use { doc ->
            val pages = doc.numberOfPages
            if (pages <= 0) return@use ""
            val stripper = PDFTextStripper().apply {
                startPage = 1
                endPage = minOf(pages, MAX_PDF_PAGES)
                sortByPosition = true
            }
            stripper.getText(doc)
        }
    }
}
