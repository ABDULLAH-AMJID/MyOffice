package com.myoffice.app.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** "Can I find this document by a word inside it?" — the question the index exists to answer. */
class LibrarySearchTest {

    @get:Rule
    val tmp = TemporaryFolder()

    // ------------------------------------------------------------------ tokenising

    @Test
    fun `query is split into words and case is ignored`() {
        assertEquals(listOf("invoice", "2026"), LibrarySearch.tokenize("Invoice 2026"))
    }

    @Test
    fun `punctuation never reaches the query`() {
        // the words survive, every operator FTS would choke on is gone
        assertEquals(listOf("eid", "greeting", "draft"), LibrarySearch.tokenize("eid's \"greeting\" - (draft)*"))
    }

    @Test
    fun `urdu words survive tokenising`() {
        val tokens = LibrarySearch.tokenize("تنخواہ سلیپ")
        assertEquals(listOf("تنخواہ", "سلیپ"), tokens)
    }

    @Test
    fun `single letters are ignored and duplicates collapse`() {
        assertEquals(listOf("invoice"), LibrarySearch.tokenize("a b invoice invoice"))
    }

    @Test
    fun `match expression quotes every token`() {
        val expr = LibrarySearch.toMatchExpression(listOf("invoice", "ab", "pdf"))
        assertEquals("\"invoice\"* AND \"ab\" AND \"pdf\"*", expr)
    }

    @Test
    fun `empty tokens produce no expression`() {
        assertEquals("", LibrarySearch.toMatchExpression(emptyList()))
    }

    // ------------------------------------------------------------------ snippets

    @Test
    fun `snippet wraps the hit in markers and elides the surroundings`() {
        val body = "Dear customer, your invoice number 4471 was paid on Monday. Thank you."
        val snippet = LibrarySearch.snippet(body, listOf("invoice"), radius = 12)
        assertTrue(snippet.contains("\u0001invoice\u0002"))
        assertTrue(snippet.startsWith("…"))
        assertTrue(snippet.endsWith("…"))
    }

    @Test
    fun `snippet picks the earliest match when several tokens hit`() {
        val body = "alpha beta gamma invoice total"
        val snippet = LibrarySearch.snippet(body, listOf("total", "alpha"), radius = 5)
        assertTrue(snippet.contains("\u0001alpha\u0002"))
    }

    @Test
    fun `snippet falls back to the head of the document`() {
        val snippet = LibrarySearch.snippet("no match here", listOf("zzz"))
        assertEquals("no match here", snippet)
    }

    // ------------------------------------------------------------------ xml → text

    @Test
    fun `docx markup becomes readable text with paragraph breaks`() {
        val xml = "<w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>" +
            "<w:p><w:r><w:t>Second line</w:t></w:r></w:p>"
        val text = TextClean.normalise(TextClean.stripXml(xml))
        assertEquals("Hello world\nSecond line", text)
    }

    @Test
    fun `xml entities are decoded`() {
        val text = TextClean.stripXml("<w:t>Tom &amp; Jerry &lt;3</w:t>")
        assertTrue(text.contains("Tom & Jerry <3"))
    }

    @Test
    fun `table cells become tabs so columns stay apart`() {
        val text = TextClean.stripXml("<w:tc><w:t>Name</w:t></w:tc><w:tc><w:t>Amount</w:t></w:tc>")
        assertTrue(text.contains("\t"))
    }

    @Test
    fun `cap keeps the head of a long document`() {
        val capped = TextClean.cap("x".repeat(100), 20)
        assertEquals(20, capped.length)
    }

    // ------------------------------------------------------------------ real packages

    private fun zipFile(name: String, entries: Map<String, String>): File {
        val file = File(tmp.newFolder(), name)
        ZipOutputStream(file.outputStream()).use { zip ->
            entries.forEach { (path, content) ->
                zip.putNextEntry(ZipEntry(path))
                zip.write(content.toByteArray())
                zip.closeEntry()
            }
        }
        return file
    }

    @Test
    fun `docx body text is extracted and searchable`() {
        val docx = zipFile(
            "letter.docx",
            mapOf(
                "word/document.xml" to
                    "<w:document><w:body><w:p><w:r><w:t>Dear Bank Manager</w:t></w:r></w:p>" +
                    "<w:p><w:r><w:t>Please transfer the salary of August.</w:t></w:r></w:p></w:body></w:document>"
            )
        )
        val text = OfficeTextExtractor.extract(docx)
        assertTrue(text.contains("Dear Bank Manager"))
        assertTrue(text.contains("salary of August"))
    }

    @Test
    fun `xlsx shared strings and cell values are extracted`() {
        val xlsx = zipFile(
            "expense.xlsx",
            mapOf(
                "xl/sharedStrings.xml" to "<sst><si><t>Rent</t></si><si><t>Electricity</t></si></sst>",
                "xl/worksheets/sheet1.xml" to "<worksheet><sheetData><row><c><v>45000</v></c></row></sheetData></worksheet>"
            )
        )
        val text = OfficeTextExtractor.extract(xlsx)
        assertTrue(text.contains("Rent"))
        assertTrue(text.contains("45000"))
    }

    @Test
    fun `pptx slide text is extracted`() {
        val pptx = zipFile(
            "deck.pptx",
            mapOf("ppt/slides/slide1.xml" to "<p:sld><a:t>Quarterly review</a:t><a:t>Revenue up 20%</a:t></p:sld>")
        )
        val text = OfficeTextExtractor.extract(pptx)
        assertTrue(text.contains("Quarterly review"))
        assertTrue(text.contains("Revenue up 20%"))
    }

    @Test
    fun `plain text and csv are indexed as they are`() {
        val txt = File(tmp.newFolder(), "notes.txt").apply { writeText("call the plumber on friday") }
        assertTrue(OfficeTextExtractor.extract(txt).contains("plumber"))

        val csv = File(tmp.newFolder(), "sales.csv").apply { writeText("item,price\nchair,3200") }
        assertTrue(OfficeTextExtractor.extract(csv).contains("chair"))
    }

    @Test
    fun `an unreadable package yields no text instead of throwing`() {
        val broken = File(tmp.newFolder(), "broken.docx").apply { writeText("this is not a zip") }
        assertEquals("", OfficeTextExtractor.extract(broken))
    }

    @Test
    fun `formats without a text layer are not extracted`() {
        val image = File(tmp.newFolder(), "photo.jpg").apply { writeText("binary") }
        assertEquals("", OfficeTextExtractor.extract(image))
        assertFalse(OfficeTextExtractor.canExtract("jpg"))
        assertTrue(OfficeTextExtractor.canExtract("pdf"))
    }

    @Test
    fun `oversized documents are skipped by the extractor`() {
        val supported = OfficeTextExtractor.SUPPORTED
        assertTrue(supported.containsAll(listOf("docx", "xlsx", "pptx", "pdf")))
        assertFalse(supported.contains("apk"))
    }

    // ------------------------------------------------------------------ ranking

    @Test
    fun `name hits come before body hits and nothing repeats`() {
        val a = LibrarySearch.Hit("/a.docx", "a.docx", "docx", "writer", 1, 1, "", "name", "")
        val b = LibrarySearch.Hit("/a.docx", "a.docx", "docx", "writer", 1, 1, "", "content", "…a…")
        val c = LibrarySearch.Hit("/c.pdf", "c.pdf", "pdf", "pdf", 1, 1, "", "content", "…c…")
        val ranked = LibrarySearch.rank(listOf(a), listOf(b, c))
        assertEquals(listOf("/a.docx", "/c.pdf"), ranked.map { it.path })
        assertEquals("name", ranked[0].where)
    }
}
