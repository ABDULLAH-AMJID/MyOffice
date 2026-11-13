package com.myoffice.app.library

/**
 * Text helpers shared by the indexer and the search UI.
 *
 * Everything here is plain Kotlin so the behaviour that decides whether a document is
 * findable — tokenising, the FTS match expression, the snippet shown under a hit — is
 * covered by JVM unit tests instead of needing a phone.
 */
object TextClean {

    /** Marks the matched span inside a snippet. Chosen because they cannot appear in a
     *  normal document, so the UI can escape the text first and only then draw the marks. */
    const val HIT_START = '\u0001'
    const val HIT_END = '\u0002'

    private val TAG = Regex("<[^>]{0,4000}>")
    private val XML_ESCAPES = mapOf(
        "&amp;" to "&", "&lt;" to "<", "&gt;" to ">", "&quot;" to "\"", "&apos;" to "'",
    )

    /**
     * Turn OOXML markup into readable text: paragraphs and table cells become line breaks,
     * everything between `<` and `>` disappears, XML entities are decoded.
     */
    fun stripXml(xml: String): String {
        if (xml.isEmpty()) return ""
        val withBreaks = xml
            .replace("</w:p>", "\n")
            .replace("</a:p>", "\n")
            .replace("</w:tc>", "\t")
            .replace("</w:tr>", "\n")
            .replace("</row>", "\n")
            .replace("<w:br/>", "\n")
            .replace("<w:tab/>", "\t")
        val stripped = TAG.replace(withBreaks, " ")
        var decoded = stripped
        for ((entity, char) in XML_ESCAPES) decoded = decoded.replace(entity, char)
        return decoded
    }

    /** Collapse the runs of whitespace that markup leaves behind, keeping line structure. */
    fun normalise(text: String): String {
        return text
            .replace("\r\n", "\n")
            .replace('\r', '\n')
            .replace(Regex("[ \\t\\u000B\\f]+"), " ")
            .replace(Regex(" ?\\n ?"), "\n")
            .replace(Regex("\\n{3,}"), "\n\n")
            .trim()
    }

    /** Keep at most [maxChars] characters, cutting on a word boundary where possible. */
    fun cap(text: String, maxChars: Int): String {
        if (text.length <= maxChars) return text
        val cut = text.lastIndexOf(' ', maxChars)
        return text.substring(0, if (cut > maxChars / 2) cut else maxChars)
    }
}

object LibrarySearch {

    /** Words that make an FTS query useless because they appear almost everywhere. */
    private val STOP_WORDS = setOf("the", "and", "for", "are", "but", "not", "you", "with", "this", "that", "from")

    private val WORD = Regex("[\\p{L}\\p{N}]+")

    /** Split a user query into lowercase search tokens (letters + digits, any script). */
    fun tokenize(query: String): List<String> {
        return WORD.findAll(query.lowercase())
            .map { it.value }
            .filter { it.length >= 2 }
            .distinct()
            .take(8)
            .toList()
    }

    /**
     * Build a safe FTS4 match expression: every token quoted (so `*`, `"`, `-` typed by the
     * user cannot break the query) and prefixed for "starts with" matching.
     * Returns "" when the query has nothing searchable in it.
     */
    fun toMatchExpression(tokens: List<String>): String {
        if (tokens.isEmpty()) return ""
        return tokens.joinToString(" AND ") { token ->
            val quoted = "\"" + token.replace("\"", "\"\"") + "\""
            if (token.length >= 3) "$quoted*" else quoted
        }
    }

    /** Words too common to be worth searching for on their own. */
    fun isLowValue(token: String): Boolean = token in STOP_WORDS

    /**
     * A one-line excerpt around the first matched token, with the hit wrapped in the
     * [TextClean.HIT_START] / [TextClean.HIT_END] markers.
     */
    fun snippet(body: String, tokens: List<String>, radius: Int = 60): String {
        if (body.isEmpty() || tokens.isEmpty()) return ""
        val flat = body.replace('\n', ' ')
        val lower = flat.lowercase()
        var hitAt = -1
        var hitLen = 0
        for (token in tokens) {
            val at = lower.indexOf(token)
            if (at >= 0 && (hitAt < 0 || at < hitAt)) {
                hitAt = at
                hitLen = token.length
            }
        }
        if (hitAt < 0) return flat.take(radius * 2).trim()
        // widen to the start of the word so the excerpt does not begin mid-syllable
        var start = (hitAt - radius).coerceAtLeast(0)
        while (start > 0 && flat[start - 1].isLetterOrDigit()) start--
        var end = (hitAt + hitLen + radius).coerceAtMost(flat.length)
        while (end < flat.length && flat[end].isLetterOrDigit()) end++
        val prefix = if (start > 0) "…" else ""
        val suffix = if (end < flat.length) "…" else ""
        val head = flat.substring(start, hitAt)
        val hit = flat.substring(hitAt, hitAt + hitLen)
        val tail = flat.substring(hitAt + hitLen, end)
        return (prefix + head + TextClean.HIT_START + hit + TextClean.HIT_END + tail + suffix).trim()
    }

    /**
     * Name hits first, then documents whose body matched; inside each group the newest wins.
     * A document that matches both is reported once, as a name hit.
     */
    fun rank(nameHits: List<Hit>, bodyHits: List<Hit>): List<Hit> {
        val seen = HashSet<String>()
        val out = ArrayList<Hit>(nameHits.size + bodyHits.size)
        for (hit in nameHits) if (seen.add(hit.path)) out += hit
        for (hit in bodyHits) if (seen.add(hit.path)) out += hit
        return out
    }

    /** Where a document was found: its own name/folder, or inside its text. */
    data class Hit(
        val path: String,
        val name: String,
        val ext: String,
        val kind: String,
        val size: Long,
        val modified: Long,
        val folder: String,
        val where: String,      // "name" | "content"
        val snippet: String,
    )
}
