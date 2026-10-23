package com.myoffice.app.sync

import com.myoffice.app.crypto.VaultCrypto
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.util.Base64
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory

/**
 * 100% Wire-Compatible Kotlin implementation of Desktop `app/services/gmail_storage.py`:
 *  - Connects to `imap.gmail.com:993` over TLS (or plain/custom SSLSocketFactory in unit tests)
 *  - Uses `IMAP APPEND "MyOffice/Sync" (\Seen) {<bytes>}` — never touches Inbox or sends emails!
 *  - Uses exact headers `X-MyOffice-Rel`, `X-MyOffice-Size`, `X-MyOffice-Mtime`, `X-MyOffice-Hash`,
 *    `X-MyOffice-Part`, `X-MyOffice-Rev` and base64 MIME attachments so files synced on Windows PC
 *    appear immediately on Android and edits on Android sync straight back to Windows PC!
 */
class GmailImapBackend(
    rawEmail: String,
    rawAppPassword: String,
    val label: String = "MyOffice/Sync",
    val host: String = "imap.gmail.com",
    val port: Int = 993,
    val useSsl: Boolean = true,
    private val customSslContext: SSLContext? = null,
    val chunkSize: Int = 12 * 1024 * 1024
) : MobileSyncBackend {

    override val name: String = "gmail"
    val email: String = rawEmail.trim()
    val password: String = rawAppPassword.filterNot { it.isWhitespace() }

    init {
        require(email.contains("@")) { "Enter your full Gmail address (e.g. yourname@gmail.com)." }
        require(password.isNotEmpty()) { "Enter your 16-character Google App Password." }
    }

    private data class PartRecord(
        val size: Long,
        val mtime: Double,
        val sha256: String,
        val totalParts: Int,
        val parts: MutableMap<Int, String> = LinkedHashMap(),
        var staleUids: List<String> = emptyList()
    )

    private class ImapWire(socket: Socket) : AutoCloseable {
        private val input = BufferedInputStream(socket.getInputStream())
        private val output = BufferedOutputStream(socket.getOutputStream())
        private val sock = socket
        private var seq = 0

        init {
            // Read initial `* OK ...` server greeting line
            readLineAscii()
        }

        fun nextTag(): String = "A%04d".format(++seq)

        fun readLineBytes(): ByteArray? {
            val baos = ByteArrayOutputStream(256)
            while (true) {
                val b = input.read()
                if (b == -1) return if (baos.size() == 0) null else baos.toByteArray()
                baos.write(b)
                if (b == '\n'.code) break
            }
            return baos.toByteArray()
        }

        fun readLineAscii(): String =
            (readLineBytes() ?: throw RuntimeException("IMAP connection closed unexpectedly"))
                .toString(Charsets.ISO_8859_1)

        fun readExact(len: Int): ByteArray {
            val out = ByteArray(len)
            var read = 0
            while (read < len) {
                val n = input.read(out, read, len - read)
                if (n < 0) throw RuntimeException("Truncated IMAP literal ($read/$len bytes)")
                read += n
            }
            return out
        }

        data class ImapReply(
            val ok: Boolean,
            val statusLine: String,
            val untaggedLines: List<String>,
            val literals: List<Pair<String, ByteArray>>
        )

        fun command(cmd: String): ImapReply {
            val tag = nextTag()
            output.write("$tag $cmd\r\n".toByteArray(Charsets.UTF_8))
            output.flush()
            return collectUntilTag(tag)
        }

        fun append(mailbox: String, flags: String, rawMessage: ByteArray): ImapReply {
            val tag = nextTag()
            output.write("$tag APPEND $mailbox ($flags) {${rawMessage.size}}\r\n".toByteArray(Charsets.UTF_8))
            output.flush()
            val cont = readLineAscii()
            if (!cont.startsWith("+")) {
                return ImapReply(false, cont, emptyList(), emptyList())
            }
            output.write(rawMessage)
            output.write("\r\n".toByteArray(Charsets.US_ASCII))
            output.flush()
            return collectUntilTag(tag)
        }

        private fun collectUntilTag(tag: String): ImapReply {
            val lines = mutableListOf<String>()
            val literals = mutableListOf<Pair<String, ByteArray>>()
            val litRegex = Regex("""\{(\d+)\}\r?\n$""")
            while (true) {
                val line = readLineAscii()
                if (line.startsWith("$tag ")) {
                    val ok = line.substring(tag.length + 1).trimStart().startsWith("OK", ignoreCase = true)
                    return ImapReply(ok, line.trim(), lines, literals)
                }
                val m = litRegex.find(line)
                if (m != null) {
                    val len = m.groupValues[1].toInt()
                    val data = readExact(len)
                    literals.add(line.trim() to data)
                } else {
                    lines.add(line.trim())
                }
            }
        }

        override fun close() {
            runCatching {
                val tag = nextTag()
                output.write("$tag LOGOUT\r\n".toByteArray(Charsets.US_ASCII))
                output.flush()
            }
            runCatching { sock.close() }
        }
    }

    private fun <T> withImap(block: (ImapWire, String) -> T): T {
        val rawSock = Socket()
        rawSock.connect(InetSocketAddress(host, port), 20_000)
        rawSock.soTimeout = 45_000
        val finalSock: Socket = if (useSsl) {
            val factory: SSLSocketFactory = customSslContext?.socketFactory
                ?: (SSLSocketFactory.getDefault() as SSLSocketFactory)
            factory.createSocket(rawSock, host, port, true)
        } else {
            rawSock
        }
        return ImapWire(finalSock).use { wire ->
            val qEmail = quoteImap(email)
            val qPass = quoteImap(password)
            val login = wire.command("LOGIN $qEmail $qPass")
            if (!login.ok) {
                throw RuntimeException(
                    "Gmail login failed (${login.statusLine}). Generate a 16-character Google App Password at " +
                        "https://myaccount.google.com/apppasswords and paste it here."
                )
            }
            var mbox = "\"$label\""
            wire.command("CREATE $mbox")
            var sel = wire.command("SELECT $mbox")
            if (!sel.ok) {
                mbox = label
                wire.command("CREATE $mbox")
                sel = wire.command("SELECT $mbox")
                if (!sel.ok) throw RuntimeException("Could not open Gmail label '$label': ${sel.statusLine}")
            }
            block(wire, mbox)
        }
    }

    private fun quoteImap(s: String): String =
        "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    private fun encodeRel(rel: String): String =
        Base64.getUrlEncoder().encodeToString(rel.toByteArray(Charsets.UTF_8))

    private fun decodeRel(rawVal: String): String {
        val trimmed = rawVal.trim()
        return if (trimmed.startsWith("b64:")) {
            String(Base64.getUrlDecoder().decode(trimmed.removePrefix("b64:")), Charsets.UTF_8)
        } else {
            trimmed
        }
    }

    private fun parseHeaders(rawBytes: ByteArray): Map<String, String> {
        val text = String(rawBytes, Charsets.UTF_8)
        val headerBlock = text.substringBefore("\r\n\r\n").substringBefore("\n\n")
        val unfolded = mutableListOf<String>()
        for (line in headerBlock.replace("\r\n", "\n").split('\n')) {
            if ((line.startsWith(" ") || line.startsWith("\t")) && unfolded.isNotEmpty()) {
                unfolded[unfolded.lastIndex] = unfolded.last() + " " + line.trim()
            } else {
                unfolded.add(line)
            }
        }
        val map = HashMap<String, String>()
        for (line in unfolded) {
            val idx = line.indexOf(':')
            if (idx > 0) {
                map[line.substring(0, idx).trim().lowercase()] = line.substring(idx + 1).trim()
            }
        }
        return map
    }

    private fun scanMessages(wire: ImapWire): Map<String, PartRecord> {
        val search = wire.command("UID SEARCH ALL")
        if (!search.ok) return emptyMap()
        val uids = search.untaggedLines
            .filter { it.startsWith("* SEARCH", ignoreCase = true) }
            .flatMap { it.removePrefix("* SEARCH").trim().split(Regex("\\s+")) }
            .filter { it.isNotEmpty() && it.all(Char::isDigit) }
        if (uids.isEmpty()) return emptyMap()

        val fetch = wire.command(
            "UID FETCH ${uids.joinToString(",")} " +
                "(BODY.PEEK[HEADER.FIELDS (X-MyOffice-Rel X-MyOffice-Size X-MyOffice-Mtime X-MyOffice-Hash X-MyOffice-Part X-MyOffice-Rev)])"
        )
        if (!fetch.ok) return emptyMap()

        val uidRegex = Regex("""UID\s+(\d+)""", RegexOption.IGNORE_CASE)
        val grouped = LinkedHashMap<String, LinkedHashMap<String, PartRecord>>()

        for ((metaLine, hdrBytes) in fetch.literals) {
            val uid = uidRegex.find(metaLine)?.groupValues?.get(1) ?: continue
            val hdrs = parseHeaders(hdrBytes)
            val relRaw = hdrs["x-myoffice-rel"] ?: continue
            runCatching {
                val rel = decodeRel(relRaw)
                val size = hdrs["x-myoffice-size"]?.toLongOrNull() ?: 0L
                val mtime = hdrs["x-myoffice-mtime"]?.toDoubleOrNull() ?: 0.0
                val sha = hdrs["x-myoffice-hash"] ?: ""
                val partStr = hdrs["x-myoffice-part"] ?: "1/1"
                val pIdx = partStr.substringBefore('/').trim().toInt()
                val pTot = partStr.substringAfter('/').trim().toInt()
                val rev = hdrs["x-myoffice-rev"]?.ifBlank { null } ?: "$mtime-${sha.take(8)}"
                val rec = grouped.getOrPut(rel) { LinkedHashMap() }.getOrPut(rev) {
                    PartRecord(size = size, mtime = mtime, sha256 = sha, totalParts = pTot)
                }
                rec.parts[pIdx] = uid
            }
        }

        val resolved = LinkedHashMap<String, PartRecord>()
        for ((rel, revs) in grouped) {
            val complete = revs.entries.filter { it.value.parts.size == it.value.totalParts }
            if (complete.isEmpty()) continue
            val best = complete.maxBy { it.value.mtime }
            val stale = revs.entries
                .filter { it.key != best.key }
                .flatMap { it.value.parts.values }
            best.value.staleUids = stale
            resolved[rel] = best.value
        }
        return resolved
    }

    private fun deleteUids(wire: ImapWire, uids: List<String>) {
        if (uids.isEmpty()) return
        wire.command("UID STORE ${uids.joinToString(",")} +FLAGS (\\Deleted)")
        wire.command("EXPUNGE")
    }

    override fun list(): Map<String, RemoteFileEntry> = withImap { wire, _ ->
        scanMessages(wire).mapValues { (_, info) ->
            RemoteFileEntry(
                size = info.size,
                mtime = info.mtime,
                etag = info.sha256.ifEmpty { "${info.size}-${info.mtime.toLong()}" }
            )
        }
    }

    override fun upload(localFile: File, relPath: String): RemoteFileEntry {
        val raw = localFile.readBytes()
        val size = raw.size.toLong()
        val mtime = localFile.lastModified() / 1000.0
        val sha = VaultCrypto.sha256Hex(raw)
        val rev = "${"%.6f".format(System.currentTimeMillis() / 1000.0)}-${VaultCrypto.randomBytes(4).joinToString("") { "%02x".format(it) }}"
        val chunks = if (raw.isEmpty()) {
            listOf(ByteArray(0))
        } else {
            raw.asList().chunked(chunkSize).map { it.toByteArray() }
        }
        val total = chunks.size

        withImap { wire, mbox ->
            val existing = scanMessages(wire)[relPath]
            chunks.forEachIndexed { zeroIdx, chunk ->
                val idx = zeroIdx + 1
                val mime = buildRfc5322PartMessage(relPath, idx, total, size, mtime, sha, rev, chunk)
                val app = wire.append(mbox, "\\Seen", mime)
                if (!app.ok) {
                    throw RuntimeException("Gmail IMAP APPEND failed for $relPath: ${app.statusLine}")
                }
            }
            if (existing != null) {
                deleteUids(wire, existing.parts.values.toList() + existing.staleUids)
            }
        }
        return RemoteFileEntry(size = size, mtime = mtime, etag = sha)
    }

    private fun buildRfc5322PartMessage(
        rel: String,
        idx: Int,
        total: Int,
        totalSize: Long,
        mtime: Double,
        sha: String,
        rev: String,
        chunk: ByteArray
    ): ByteArray {
        val boundary = "====MyOfficeMime_${VaultCrypto.randomBytes(8).joinToString("") { "%02x".format(it) }}===="
        val b64Chunk = Base64.getMimeEncoder(76, "\r\n".toByteArray(Charsets.US_ASCII)).encodeToString(chunk)
        val fname = File(rel).name + if (total > 1) ".part$idx" else ""
        val sb = StringBuilder(b64Chunk.length + 1024)
        sb.append("From: $email\r\n")
        sb.append("To: $email\r\n")
        sb.append("Subject: [MyOffice] $rel${if (total > 1) " (part $idx/$total)" else ""}\r\n")
        sb.append("MIME-Version: 1.0\r\n")
        sb.append("X-MyOffice-Rel: b64:${encodeRel(rel)}\r\n")
        sb.append("X-MyOffice-Size: $totalSize\r\n")
        sb.append("X-MyOffice-Mtime: ${"%.6f".format(mtime)}\r\n")
        sb.append("X-MyOffice-Hash: $sha\r\n")
        sb.append("X-MyOffice-Part: $idx/$total\r\n")
        sb.append("X-MyOffice-Rev: $rev\r\n")
        sb.append("Content-Type: multipart/mixed; boundary=\"$boundary\"\r\n\r\n")
        sb.append("--$boundary\r\n")
        sb.append("Content-Type: text/plain; charset=\"utf-8\"\r\n")
        sb.append("Content-Transfer-Encoding: 7bit\r\n\r\n")
        sb.append("MyOffice synchronized file: $rel\r\nSHA-256: $sha\r\nSize: $totalSize bytes\r\n\r\n")
        sb.append("--$boundary\r\n")
        sb.append("Content-Type: application/octet-stream\r\n")
        sb.append("Content-Transfer-Encoding: base64\r\n")
        sb.append("Content-Disposition: attachment; filename=\"$fname\"\r\n\r\n")
        sb.append(b64Chunk)
        sb.append("\r\n--$boundary--\r\n")
        return sb.toString().toByteArray(Charsets.UTF_8)
    }

    override fun download(relPath: String, destFile: File) {
        val fullBytes = withImap { wire, _ ->
            val info = scanMessages(wire)[relPath]
                ?: throw IllegalArgumentException("File '$relPath' not found in Gmail label '$label'")
            val out = ByteArrayOutputStream(info.size.toInt().coerceAtLeast(64))
            for (idx in 1..info.totalParts) {
                val uid = info.parts[idx] ?: throw IllegalStateException("Missing chunk $idx for $relPath")
                val fetched = wire.command("UID FETCH $uid (BODY.PEEK[])")
                val rawMsg = fetched.literals.firstOrNull()?.second
                    ?: throw IllegalStateException("Empty MIME payload on UID $uid")
                out.write(extractMimeAttachmentBytes(rawMsg))
            }
            val combined = out.toByteArray()
            if (info.sha256.isNotEmpty() && !VaultCrypto.sha256Hex(combined).equals(info.sha256, true)) {
                throw IllegalStateException("Gmail attachment SHA-256 verification failed for '$relPath'")
            }
            combined
        }
        destFile.parentFile?.mkdirs()
        destFile.writeBytes(fullBytes)
    }

    private fun extractMimeAttachmentBytes(rawMime: ByteArray): ByteArray {
        val text = String(rawMime, Charsets.ISO_8859_1)
        val ctHeader = Regex("""Content-Type:\s*multipart/[^;]+;\s*boundary="?([^"\r\n;]+)"?""", RegexOption.IGNORE_CASE)
            .find(text)
        if (ctHeader != null) {
            val boundary = "--" + ctHeader.groupValues[1]
            val parts = text.split(boundary)
            for (part in parts.drop(1)) {
                if (part.startsWith("--")) break
                if (part.contains("application/octet-stream", ignoreCase = true) ||
                    part.contains("Content-Disposition: attachment", ignoreCase = true)
                ) {
                    val splitIdx = part.indexOf("\r\n\r\n").let { if (it >= 0) it + 4 else part.indexOf("\n\n") + 2 }
                    if (splitIdx > 1) {
                        val body = part.substring(splitIdx).trim()
                        val cleaned = body.filterNot { it.isWhitespace() }
                        return Base64.getDecoder().decode(cleaned)
                    }
                }
            }
        }
        throw IllegalStateException("Could not locate binary attachment in MIME message.")
    }

    override fun delete(relPath: String) {
        withImap { wire, _ ->
            val info = scanMessages(wire)[relPath] ?: return@withImap
            deleteUids(wire, info.parts.values.toList() + info.staleUids)
        }
    }
}
