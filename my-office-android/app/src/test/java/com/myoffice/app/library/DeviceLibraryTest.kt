package com.myoffice.app.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The home screen's promise: every readable document, in the order the user asked for. */
class DeviceLibraryTest {

    private fun f(
        name: String,
        kind: String = DocumentKinds.kindOf(name.substringAfterLast('.', "")),
        size: Long = 1_000,
        modified: Long = 1_000,
        folder: String = "Download",
    ) = DeviceFile("/sdcard/$folder/$name", name, name.substringAfterLast('.', ""), kind, size, modified, folder)

    // ------------------------------------------------------------------ kinds

    @Test
    fun `classifies office documents the same way the editors do`() {
        assertEquals("writer", DocumentKinds.kindOf("docx"))
        assertEquals("sheet", DocumentKinds.kindOf("XLSX"))
        assertEquals("slide", DocumentKinds.kindOf("ppt"))
        assertEquals("pdf", DocumentKinds.kindOf("PDF"))
        assertEquals("photos", DocumentKinds.kindOf("jpeg"))
        assertEquals("vault", DocumentKinds.kindOf("movault"))
        assertEquals("file", DocumentKinds.kindOf("zip"))
    }

    @Test
    fun `only openable documents enter the library`() {
        assertTrue(DocumentKinds.isSupported("Budget 2026.xlsx"))
        assertTrue(DocumentKinds.isSupported("scan.JPG"))
        assertTrue(DocumentKinds.isSupported("notes.md"))
        assertFalse(DocumentKinds.isSupported("app.apk"))
        assertFalse(DocumentKinds.isSupported("movie.mp4"))
        assertFalse(DocumentKinds.isSupported("noextension"))
    }

    // ------------------------------------------------------------------ sorting

    @Test
    fun `newest is the default and sorts by modification date`() {
        val a = f("a.pdf", modified = 100)
        val b = f("b.pdf", modified = 300)
        val c = f("c.pdf", modified = 200)
        assertEquals(listOf("b.pdf", "c.pdf", "a.pdf"), LibrarySorting.sort(listOf(a, b, c), LibrarySorting.NEWEST).map { it.name })
        assertEquals(listOf("b.pdf", "c.pdf", "a.pdf"), LibrarySorting.sort(listOf(a, b, c), "garbage-key").map { it.name })
    }

    @Test
    fun `oldest reverses the date order`() {
        val files = listOf(f("a.pdf", modified = 100), f("b.pdf", modified = 300), f("c.pdf", modified = 200))
        assertEquals(listOf("a.pdf", "c.pdf", "b.pdf"), LibrarySorting.sort(files, LibrarySorting.OLDEST).map { it.name })
    }

    @Test
    fun `name sorting ignores case in both directions`() {
        val files = listOf(f("beta.docx"), f("Alpha.docx"), f("gamma.docx"))
        assertEquals(listOf("Alpha.docx", "beta.docx", "gamma.docx"), LibrarySorting.sort(files, LibrarySorting.NAME_ASC).map { it.name })
        assertEquals(listOf("gamma.docx", "beta.docx", "Alpha.docx"), LibrarySorting.sort(files, LibrarySorting.NAME_DESC).map { it.name })
    }

    @Test
    fun `size sorting handles equal sizes by name`() {
        val small = f("s.pdf", size = 10)
        val big = f("b.pdf", size = 9_000)
        val alsoBig = f("a.pdf", size = 9_000)
        assertEquals(listOf("a.pdf", "b.pdf", "s.pdf"), LibrarySorting.sort(listOf(small, big, alsoBig), LibrarySorting.LARGEST).map { it.name })
        assertEquals(listOf("s.pdf", "a.pdf", "b.pdf"), LibrarySorting.sort(listOf(small, big, alsoBig), LibrarySorting.SMALLEST).map { it.name })
    }

    @Test
    fun `type sorting groups pdf word excel slides photos then the rest`() {
        val files = listOf(
            f("x.zip", kind = "file"),
            f("p.jpg", kind = "photos"),
            f("d.pptx", kind = "slide"),
            f("c.xlsx", kind = "sheet"),
            f("b.docx", kind = "writer"),
            f("a.pdf", kind = "pdf"),
        )
        assertEquals(
            listOf("a.pdf", "b.docx", "c.xlsx", "d.pptx", "p.jpg", "x.zip"),
            LibrarySorting.sort(files, LibrarySorting.TYPE).map { it.name }
        )
    }

    @Test
    fun `last used puts opened files first and never opened ones after`() {
        val a = f("a.pdf", modified = 100)
        val b = f("b.docx", modified = 200)
        val c = f("c.xlsx", modified = 300)
        val used = mapOf(a.path to 5_000L, c.path to 9_000L)
        assertEquals(
            listOf("c.xlsx", "a.pdf", "b.docx"),
            LibrarySorting.sort(listOf(a, b, c), LibrarySorting.LAST_USED, used).map { it.name }
        )
    }

    @Test
    fun `last used falls back to modification date when nothing was opened`() {
        val a = f("a.pdf", modified = 100)
        val b = f("b.pdf", modified = 300)
        assertEquals(listOf("b.pdf", "a.pdf"), LibrarySorting.sort(listOf(a, b), LibrarySorting.LAST_USED).map { it.name })
    }

    // ------------------------------------------------------------------ filtering

    @Test
    fun `filter chips map onto document kinds`() {
        assertEquals(listOf("a.pdf"), LibrarySorting.apply(listOf(f("a.pdf"), f("b.docx")), "pdf", "").map { it.name })
        assertEquals(listOf("b.docx"), LibrarySorting.apply(listOf(f("a.pdf"), f("b.docx")), "writer", "").map { it.name })
        assertEquals(2, LibrarySorting.apply(listOf(f("a.pdf"), f("b.docx")), "all", "").size)
        assertEquals(2, LibrarySorting.apply(listOf(f("a.pdf"), f("b.docx")), null, "  ").size)
    }

    @Test
    fun `search looks at the name and the folder`() {
        val files = listOf(
            f("salary.pdf", folder = "Documents"),
            f("report.xlsx", folder = "WhatsApp/Media"),
        )
        assertEquals(listOf("salary.pdf"), LibrarySorting.apply(files, "all", "sal").map { it.name })
        assertEquals(listOf("report.xlsx"), LibrarySorting.apply(files, "all", "whatsapp").map { it.name })
        assertTrue(LibrarySorting.apply(files, "all", "nothing-matches").isEmpty())
    }

    @Test
    fun `filter and search combine`() {
        val files = listOf(f("invoice.pdf"), f("invoice.xlsx"))
        val out = LibrarySorting.apply(files, "sheet", "invoice")
        assertEquals(listOf("invoice.xlsx"), out.map { it.name })
    }

    @Test
    fun `sort keys are validated against the supported set`() {
        assertEquals(LibrarySorting.NEWEST, LibrarySorting.normalise("nonsense"))
        assertEquals(LibrarySorting.NEWEST, LibrarySorting.normalise(null))
        assertEquals(LibrarySorting.TYPE, LibrarySorting.normalise(" TYPE "))
        assertEquals(8, LibrarySorting.ALL.size)
    }
}
