package com.myoffice.app.sync

import com.myoffice.app.crypto.VaultCrypto
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.apache.commons.net.ftp.FTP
import org.apache.commons.net.ftp.FTPClient
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.MessageDigest
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Base64
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

data class RemoteFileEntry(
    val size: Long,
    val mtime: Double,
    val etag: String = ""
)

data class SyncReportMobile(
    val uploaded: MutableList<String> = mutableListOf(),
    val downloaded: MutableList<String> = mutableListOf(),
    val deletedLocal: MutableList<String> = mutableListOf(),
    val deletedRemote: MutableList<String> = mutableListOf(),
    val conflicts: MutableList<String> = mutableListOf(),
    val errors: MutableList<String> = mutableListOf()
) {
    fun summary(): String =
        "↑ ${uploaded.size} uploaded, ↓ ${downloaded.size} downloaded, " +
            "${deletedLocal.size + deletedRemote.size} deleted, ${conflicts.size} conflict(s), ${errors.size} error(s)"

    fun toJson(): JSONObject = JSONObject()
        .put("summary", summary())
        .put("uploaded", JSONArray(uploaded))
        .put("downloaded", JSONArray(downloaded))
        .put("deletedLocal", JSONArray(deletedLocal))
        .put("deletedRemote", JSONArray(deletedRemote))
        .put("conflicts", JSONArray(conflicts))
        .put("errors", JSONArray(errors))
}

interface MobileSyncBackend {
    val name: String
    fun list(): Map<String, RemoteFileEntry>
    fun upload(localFile: File, relPath: String): RemoteFileEntry
    fun download(relPath: String, destFile: File)
    fun delete(relPath: String)
}

object LanNetworkUtils {
    const val DISCOVERY_PORT = 41889
    const val DEFAULT_SYNC_PORT = 41880
    const val DISCOVERY_MAGIC = "MYOFFICE_DISCOVER_V1"

    fun listLanIpv4Addresses(): List<String> {
        val scored = mutableListOf<Pair<Int, String>>()
        runCatching {
            val ifaces = NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
            for (nif in ifaces) {
                if (!nif.isUp || nif.isLoopback) continue
                val iname = nif.name.lowercase()
                if (iname.startsWith("docker") || iname.startsWith("veth") || iname.startsWith("tun")) continue
                for (addr in nif.inetAddresses.toList()) {
                    if (addr !is Inet4Address || addr.isLoopbackAddress) continue
                    val ip = addr.hostAddress ?: continue
                    if (ip.startsWith("169.254.")) continue // Filter Windows/Android link-local APIPA
                    val score = when {
                        ip.startsWith("192.168.") && (iname.startsWith("wlan") || iname.startsWith("ap") || iname.startsWith("eth")) -> 100
                        ip.startsWith("192.168.") -> 90
                        ip.startsWith("10.") -> 80
                        ip.startsWith("172.") -> 70
                        else -> 50
                    }
                    scored.add(score to ip)
                }
            }
        }
        if (scored.isEmpty()) return listOf("127.0.0.1")
        return scored.sortedByDescending { it.first }.map { it.second }.distinct()
    }

    fun primaryLanIp(): String = listLanIpv4Addresses().firstOrNull() ?: "127.0.0.1"

    fun parsePairUri(rawInput: String): JSONObject {
        val raw = rawInput.trim()
        val res = JSONObject()
        if (raw.startsWith("myoffice://pair", ignoreCase = true)) {
            val query = raw.substringAfter('?', "")
            for (pair in query.split('&')) {
                val k = URLDecoder.decode(pair.substringBefore('='), "UTF-8")
                val v = URLDecoder.decode(pair.substringAfter('=', ""), "UTF-8")
                if (k.isNotEmpty()) res.put(k, v)
            }
            res.put("url", res.optString("url").trimEnd('/'))
            return res
        }
        if (raw.startsWith("http://") || raw.startsWith("https://")) {
            return res.put("url", raw.trimEnd('/'))
        }
        if (':' in raw && !raw.startsWith('/')) {
            return res.put("url", "http://${raw.trimEnd('/')}")
        }
        return res.put("url", "")
    }

    fun buildPairUri(baseUrl: String, token: String, pin: String, deviceName: String, vaultKeyHex: String = ""): String {
        fun enc(v: String) = URLEncoder.encode(v, "UTF-8")
        val sb = StringBuilder("myoffice://pair?url=${enc(baseUrl.trimEnd('/'))}&token=${enc(token)}&pin=${enc(pin)}&device=${enc(deviceName)}")
        if (vaultKeyHex.isNotEmpty()) sb.append("&vk=${enc(vaultKeyHex)}")
        return sb.toString()
    }

    fun discoverPeersOnLan(timeoutMs: Int = 850, discoveryPort: Int = DISCOVERY_PORT): JSONArray {
        val found = LinkedHashMap<String, JSONObject>()
        DatagramSocket().use { sock ->
            sock.broadcast = true
            sock.soTimeout = 250
            val probe = DISCOVERY_MAGIC.toByteArray(Charsets.UTF_8)
            val targets = linkedSetOf("255.255.255.255", "127.0.0.1")
            val lan = primaryLanIp().split('.')
            if (lan.size == 4 && lan[0] != "127") {
                targets.add("${lan[0]}.${lan[1]}.${lan[2]}.255")
            }
            for (t in targets) {
                runCatching {
                    sock.send(DatagramPacket(probe, probe.size, InetAddress.getByName(t), discoveryPort))
                }
            }
            val deadline = System.currentTimeMillis() + timeoutMs
            val buf = ByteArray(4096)
            while (System.currentTimeMillis() < deadline) {
                sock.soTimeout = (deadline - System.currentTimeMillis()).toInt().coerceAtLeast(60)
                val pkt = DatagramPacket(buf, buf.size)
                try {
                    sock.receive(pkt)
                } catch (_: Exception) {
                    break
                }
                runCatching {
                    val obj = JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
                    if (obj.optString("service") == "myoffice-sync-v1") {
                        val ip = pkt.address.hostAddress ?: "127.0.0.1"
                        val p = obj.optInt("port", DEFAULT_SYNC_PORT)
                        val url = "http://$ip:$p"
                        found[url] = JSONObject()
                            .put("device", obj.optString("device", ip))
                            .put("platform", obj.optString("platform", "pc"))
                            .put("url", url)
                            .put("ip", ip)
                            .put("port", p)
                            .put("version", obj.optString("version", ""))
                    }
                }
            }
        }
        return JSONArray(found.values.toList())
    }
}

/**
 * 1. Direct Wi-Fi / Hotspot Peer Backend (`PcLanPeerBackend`) — syncs directly with Windows/Linux PC
 *    `MobileSyncServer` (`app/services/mobile_sync.py`) over fast HTTP REST + Bearer Token + Vault Key Exchange.
 */
class PcLanPeerBackend(
    rawUrlOrQr: String,
    initialToken: String = "",
    initialPin: String = "",
    val deviceName: String = "MyOffice-Android",
    private val keyStore: VaultCrypto.DeviceKeyStore? = null
) : MobileSyncBackend {

    override val name: String = "pc_lan"
    val baseUrl: String
    var token: String
        private set
    private val pin: String

    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    init {
        val parsed = LanNetworkUtils.parsePairUri(rawUrlOrQr)
        baseUrl = parsed.optString("url").ifEmpty { rawUrlOrQr.trimEnd('/') }
        require(baseUrl.startsWith("http://") || baseUrl.startsWith("https://")) {
            "Invalid PC Sync URL: '$rawUrlOrQr'"
        }
        token = initialToken.ifEmpty { parsed.optString("token") }
        pin = initialPin.ifEmpty { parsed.optString("pin") }
        val qrVk = parsed.optString("vk")
        if (qrVk.length == 64 && keyStore != null) {
            runCatching { keyStore?.authorizePeerMasterKeyHex(qrVk) }
        }
    }

    fun pairWithPc(pinOverride: String = "", shareMyVaultKey: Boolean = true): JSONObject {
        val bodyJson = JSONObject()
            .put("pin", pinOverride.ifEmpty { pin })
            .put("token", token)
            .put("device_name", deviceName)
            .put("platform", "android")
        if (shareMyVaultKey && keyStore != null) {
            bodyJson.put("vault_key_hex", with(VaultCrypto) { keyStore!!.masterKey.toHex() })
        }
        val req = Request.Builder()
            .url("$baseUrl/api/v1/pair")
            .header("X-Device-Name", deviceName)
            .post(bodyJson.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()
        http.newCall(req).execute().use { resp ->
            val txt = resp.body?.string() ?: "{}"
            val obj = JSONObject(txt)
            if (!resp.isSuccessful || !obj.optBoolean("ok", false)) {
                throw RuntimeException(obj.optString("error", "PC pairing rejected (${resp.code})"))
            }
            token = obj.getString("token")
            val pcVk = obj.optString("vault_key_hex", "")
            if (pcVk.length == 64 && keyStore != null) {
                keyStore.authorizePeerMasterKeyHex(pcVk)
            }
            return obj
        }
    }

    private fun ensurePaired() {
        if (token.isEmpty()) pairWithPc()
    }

    private fun Request.Builder.authHeaders(): Request.Builder =
        header("Authorization", "Bearer $token")
            .header("X-Device-Name", deviceName)

    override fun list(): Map<String, RemoteFileEntry> {
        ensurePaired()
        val req = Request.Builder().url("$baseUrl/api/v1/files").authHeaders().get().build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw RuntimeException("PC sync list failed (HTTP ${resp.code})")
            val obj = JSONObject(resp.body?.string() ?: "{}")
            val filesObj = obj.optJSONObject("files") ?: JSONObject()
            val out = LinkedHashMap<String, RemoteFileEntry>()
            for (k in filesObj.keys()) {
                val f = filesObj.getJSONObject(k)
                out[k] = RemoteFileEntry(
                    size = f.optLong("size", 0L),
                    mtime = f.optDouble("mtime", 0.0),
                    etag = f.optString("sha256", "")
                )
            }
            return out
        }
    }

    override fun download(relPath: String, destFile: File) {
        ensurePaired()
        val q = URLEncoder.encode(relPath, "UTF-8")
        val req = Request.Builder().url("$baseUrl/api/v1/file?path=$q").authHeaders().get().build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw RuntimeException("Download failed for '$relPath' (HTTP ${resp.code})")
            val bytes = resp.body?.bytes() ?: ByteArray(0)
            destFile.parentFile?.mkdirs()
            val tmp = File(destFile.parentFile, ".dl_${destFile.name}")
            tmp.writeBytes(bytes)
            tmp.renameTo(destFile)
        }
    }

    override fun upload(localFile: File, relPath: String): RemoteFileEntry {
        ensurePaired()
        val mtimeSec = localFile.lastModified() / 1000.0
        val q = URLEncoder.encode(relPath, "UTF-8")
        val req = Request.Builder()
            .url("$baseUrl/api/v1/file?path=$q&mtime=${"%.3f".format(mtimeSec)}")
            .authHeaders()
            .put(localFile.readBytes().toRequestBody("application/octet-stream".toMediaType()))
            .build()
        http.newCall(req).execute().use { resp ->
            val txt = resp.body?.string() ?: "{}"
            if (!resp.isSuccessful) throw RuntimeException("Upload failed for '$relPath' (HTTP ${resp.code}): $txt")
            val info = JSONObject(txt)
            return RemoteFileEntry(
                size = info.optLong("size", localFile.length()),
                mtime = info.optDouble("mtime", mtimeSec),
                etag = info.optString("sha256", VaultCrypto.sha256Hex(localFile.readBytes()))
            )
        }
    }

    override fun delete(relPath: String) {
        ensurePaired()
        val q = URLEncoder.encode(relPath, "UTF-8")
        val req = Request.Builder().url("$baseUrl/api/v1/file?path=$q").authHeaders().delete().build()
        http.newCall(req).execute().close()
    }

    fun fetchActivePcCollabRooms(): JSONArray {
        ensurePaired()
        val req = Request.Builder().url("$baseUrl/api/v1/collab-rooms").authHeaders().get().build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return JSONArray()
            val obj = JSONObject(resp.body?.string() ?: "{}")
            return obj.optJSONArray("rooms") ?: JSONArray()
        }
    }
}

// 2. Embedded Phone Sync Hub (MobileSyncHostServer) — turns the Android phone itself into a Wi-Fi / Mobile Hotspot
//    Sync Server speaking the EXACT SAME /api/v1/... protocol as Desktop MobileSyncServer!
class MobileSyncHostServer(
    val rootDir: File,
    val requestedPort: Int = LanNetworkUtils.DEFAULT_SYNC_PORT,
    val deviceName: String = "MyOffice-Android",
    private val keyStore: VaultCrypto.DeviceKeyStore? = null,
    val shareVaultKey: Boolean = true,
    val enableUdpDiscovery: Boolean = true
) {
    var port: Int = requestedPort
        private set
    var token: String = Base64.getUrlEncoder().withoutPadding().encodeToString(VaultCrypto.randomBytes(24))
        private set
    var pin: String = "%06d".format((100000..999999).random())
        private set

    private var serverSocket: ServerSocket? = null
    private var pool: ExecutorService? = null
    private var udpSock: DatagramSocket? = null
    @Volatile
    private var runningFlag = false
    val activityLog = mutableListOf<String>()

    val isRunning: Boolean get() = runningFlag

    fun baseUrl(ipOverride: String? = null): String =
        "http://${ipOverride ?: LanNetworkUtils.primaryLanIp()}:$port"

    fun pairUri(ipOverride: String? = null): String {
        val vk = if (shareVaultKey && keyStore != null) with(VaultCrypto) { keyStore!!.masterKey.toHex() } else ""
        return LanNetworkUtils.buildPairUri(baseUrl(ipOverride), token, pin, deviceName, vk)
    }

    private fun logEvent(msg: String) {
        synchronized(activityLog) {
            activityLog.add("[${LocalDateTime.now().format(DateTimeFormatter.ofPattern("HH:mm:ss"))}] $msg")
            if (activityLog.size > 150) activityLog.removeAt(0)
        }
    }

    private fun safeFile(rel: String): File {
        val clean = rel.replace('\\', '/').trimStart('/')
        require(clean.isNotEmpty() && clean.split('/').none { it.isEmpty() || it == "." || it == ".." }) {
            "Unsafe relative path: $rel"
        }
        val rootCanon = rootDir.canonicalFile
        val target = File(rootCanon, clean).canonicalFile
        require(target.path.startsWith(rootCanon.path + File.separator)) { "Path escapes workspace" }
        return target
    }

    fun start(): Int {
        if (runningFlag) return port
        rootDir.mkdirs()
        val ss = try {
            ServerSocket().apply {
                reuseAddress = true
                bind(InetSocketAddress("0.0.0.0", requestedPort), 32)
            }
        } catch (_: Exception) {
            ServerSocket(0, 32, InetAddress.getByName("0.0.0.0"))
        }
        port = ss.localPort
        serverSocket = ss
        val exec = Executors.newCachedThreadPool()
        pool = exec
        runningFlag = true

        Thread({
            while (runningFlag) {
                try {
                    val client = ss.accept()
                    exec.execute { handleHttpClient(client) }
                } catch (_: Exception) {
                    if (!runningFlag) break
                }
            }
        }, "MyOfficePhoneSyncAccept").apply { isDaemon = true }.start()

        if (enableUdpDiscovery) {
            runCatching {
                val ds = DatagramSocket(null).apply {
                    reuseAddress = true
                    bind(InetSocketAddress("0.0.0.0", LanNetworkUtils.DISCOVERY_PORT))
                    soTimeout = 600
                }
                udpSock = ds
                Thread({
                    val buf = ByteArray(1024)
                    while (runningFlag) {
                        val pkt = DatagramPacket(buf, buf.size)
                        try {
                            ds.receive(pkt)
                            val msg = String(pkt.data, 0, pkt.length, Charsets.UTF_8).trim()
                            if (msg == LanNetworkUtils.DISCOVERY_MAGIC) {
                                val reply = JSONObject()
                                    .put("service", "myoffice-sync-v1")
                                    .put("device", deviceName)
                                    .put("platform", "android")
                                    .put("port", port)
                                    .put("version", "1.4.0")
                                    .toString()
                                    .toByteArray(Charsets.UTF_8)
                                ds.send(DatagramPacket(reply, reply.size, pkt.address, pkt.port))
                            }
                        } catch (_: Exception) {
                        }
                    }
                }, "MyOfficeAndroidUdpDiscovery").apply { isDaemon = true }.start()
            }
        }
        logEvent("Phone Sync Hub active on port $port")
        return port
    }

    fun stop() {
        runningFlag = false
        runCatching { serverSocket?.close() }
        serverSocket = null
        runCatching { pool?.shutdownNow() }
        pool = null
        runCatching { udpSock?.close() }
        udpSock = null
    }

    private fun readHttpLine(ins: BufferedInputStream): String? {
        val baos = ByteArrayOutputStream()
        while (true) {
            val b = ins.read()
            if (b == -1) return if (baos.size() == 0) null else baos.toString("ISO-8859-1").trimEnd('\r')
            if (b == '\n'.code) return baos.toString("ISO-8859-1").trimEnd('\r')
            baos.write(b)
            if (baos.size() > 16384) break
        }
        return baos.toString("ISO-8859-1").trimEnd('\r')
    }

    private fun handleHttpClient(sock: Socket) {
        sock.use { s ->
            s.soTimeout = 30_000
            val ins = BufferedInputStream(s.getInputStream())
            val outs = BufferedOutputStream(s.getOutputStream())
            val reqLine = readHttpLine(ins) ?: return
            val parts = reqLine.split(' ')
            if (parts.size < 2) return
            val method = parts[0].uppercase()
            val fullTarget = parts[1]
            val route = fullTarget.substringBefore('?')
            val rawQuery = fullTarget.substringAfter('?', "")

            val headers = LinkedHashMap<String, String>()
            while (true) {
                val hl = readHttpLine(ins) ?: break
                if (hl.isEmpty()) break
                val colon = hl.indexOf(':')
                if (colon > 0) {
                    headers[hl.substring(0, colon).trim().lowercase()] = hl.substring(colon + 1).trim()
                }
            }
            val contentLen = headers["content-length"]?.toIntOrNull()?.coerceAtLeast(0) ?: 0
            val bodyBytes = ByteArray(contentLen)
            var off = 0
            while (off < contentLen) {
                val n = ins.read(bodyBytes, off, contentLen - off)
                if (n <= 0) break
                off += n
            }

            if (method == "OPTIONS") {
                writeHttpResponse(outs, 200, "application/json", ByteArray(0))
                return
            }

            when (route) {
                "/api/v1/info" -> {
                    val count = rootDir.walkTopDown().count { it.isFile && !SyncEngineMobile.isIgnored(it.name) }
                    writeJsonResponse(
                        outs,
                        200,
                        JSONObject()
                            .put("ok", true)
                            .put("service", "myoffice-sync-v1")
                            .put("device", deviceName)
                            .put("platform", "android")
                            .put("version", "1.4.0")
                            .put("file_count", count)
                            .put("requires_auth", true)
                    )
                }

                "/api/v1/pair" -> {
                    if (method != "POST") {
                        writeJsonResponse(outs, 405, JSONObject().put("ok", false).put("error", "Method not allowed"))
                        return
                    }
                    val body = runCatching { JSONObject(bodyBytes.toString(Charsets.UTF_8)) }.getOrElse { JSONObject() }
                    val inPin = body.optString("pin").trim()
                    val inTok = body.optString("token").trim()
                    val pinOk = inPin.isNotEmpty() && MessageDigest.isEqual(inPin.toByteArray(), pin.toByteArray())
                    val tokOk = inTok.isNotEmpty() && MessageDigest.isEqual(inTok.toByteArray(), token.toByteArray())
                    val peerAddr = s.inetAddress?.hostAddress ?: "peer"
                    if (!pinOk && !tokOk) {
                        logEvent("Rejected invalid PIN from $peerAddr")
                        writeJsonResponse(outs, 403, JSONObject().put("ok", false).put("error", "Invalid 6-digit pairing PIN"))
                        return
                    }
                    val peerName = body.optString("device_name", peerAddr)
                    val peerVk = body.optString("vault_key_hex", "")
                    if (shareVaultKey && peerVk.length == 64 && keyStore != null) {
                        runCatching { keyStore?.authorizePeerMasterKeyHex(peerVk) }
                    }
                    logEvent("Paired '$peerName' ($peerAddr)")
                    val resp = JSONObject()
                        .put("ok", true)
                        .put("token", token)
                        .put("device", deviceName)
                        .put("platform", "android")
                        .put("version", "1.4.0")
                    if (shareVaultKey && keyStore != null) {
                        resp.put("vault_key_id", keyStore!!.keyId)
                        resp.put("vault_key_hex", with(VaultCrypto) { keyStore!!.masterKey.toHex() })
                    }
                    writeJsonResponse(outs, 200, resp)
                }

                "/api/v1/files" -> {
                    if (!isAuthorized(headers)) {
                        writeJsonResponse(outs, 401, JSONObject().put("ok", false).put("error", "Unauthorized"))
                        return
                    }
                    val filesJson = JSONObject()
                    val rootCanon = rootDir.canonicalFile
                    rootCanon.walkTopDown().forEach { f ->
                        if (f.isFile) {
                            val rel = f.relativeTo(rootCanon).invariantSeparatorsPath
                            if (!SyncEngineMobile.isIgnored(rel)) {
                                filesJson.put(
                                    rel,
                                    JSONObject()
                                        .put("size", f.length())
                                        .put("mtime", f.lastModified() / 1000.0)
                                        .put("sha256", VaultCrypto.sha256Hex(f.readBytes()))
                                )
                            }
                        }
                    }
                    writeJsonResponse(outs, 200, JSONObject().put("ok", true).put("device", deviceName).put("files", filesJson))
                }

                "/api/v1/file" -> {
                    if (!isAuthorized(headers)) {
                        writeJsonResponse(outs, 401, JSONObject().put("ok", false).put("error", "Unauthorized"))
                        return
                    }
                    val params = parseQuery(rawQuery)
                    val rel = params["path"] ?: ""
                    val target = try {
                        safeFile(rel)
                    } catch (e: Exception) {
                        writeJsonResponse(outs, 400, JSONObject().put("ok", false).put("error", e.message))
                        return
                    }
                    when (method) {
                        "GET" -> {
                            if (!target.isFile) {
                                writeJsonResponse(outs, 404, JSONObject().put("ok", false).put("error", "Not found"))
                                return
                            }
                            val bytes = target.readBytes()
                            val extra = mapOf(
                                "X-SHA256" to VaultCrypto.sha256Hex(bytes),
                                "X-MTime" to "%.3f".format(target.lastModified() / 1000.0)
                            )
                            writeHttpResponse(outs, 200, "application/octet-stream", bytes, extra)
                            logEvent("Sent '$rel' (${bytes.size} B)")
                        }
                        "PUT" -> {
                            target.parentFile?.mkdirs()
                            val tmp = File(target.parentFile, ".put_${target.name}")
                            tmp.writeBytes(bodyBytes)
                            tmp.renameTo(target)
                            params["mtime"]?.toDoubleOrNull()?.let { target.setLastModified((it * 1000).toLong()) }
                            val sha = VaultCrypto.sha256Hex(bodyBytes)
                            logEvent("Received '$rel' (${bodyBytes.size} B)")
                            writeJsonResponse(
                                outs,
                                200,
                                JSONObject()
                                    .put("ok", true)
                                    .put("size", bodyBytes.size)
                                    .put("mtime", target.lastModified() / 1000.0)
                                    .put("sha256", sha)
                            )
                        }
                        "DELETE" -> {
                            if (target.isFile) target.delete()
                            logEvent("Deleted '$rel'")
                            writeJsonResponse(outs, 200, JSONObject().put("ok", true))
                        }
                        else -> writeJsonResponse(outs, 405, JSONObject().put("ok", false))
                    }
                }

                "/api/v1/collab-rooms" -> {
                    writeJsonResponse(outs, 200, JSONObject().put("ok", true).put("rooms", JSONArray()))
                }

                else -> writeJsonResponse(outs, 404, JSONObject().put("ok", false).put("error", "Unknown endpoint"))
            }
        }
    }

    private fun isAuthorized(headers: Map<String, String>): Boolean {
        val hdr = headers["authorization"] ?: ""
        if (!hdr.startsWith("Bearer ", ignoreCase = true)) return false
        val got = hdr.substring(7).trim()
        return MessageDigest.isEqual(got.toByteArray(), token.toByteArray())
    }

    private fun parseQuery(rawQuery: String): Map<String, String> =
        rawQuery.split('&').filter { it.isNotEmpty() }.associate {
            URLDecoder.decode(it.substringBefore('='), "UTF-8") to
                URLDecoder.decode(it.substringAfter('=', ""), "UTF-8")
        }

    private fun writeJsonResponse(outs: BufferedOutputStream, status: Int, obj: JSONObject) {
        writeHttpResponse(outs, status, "application/json; charset=utf-8", obj.toString().toByteArray(Charsets.UTF_8))
    }

    private fun writeHttpResponse(
        outs: BufferedOutputStream,
        status: Int,
        contentType: String,
        body: ByteArray,
        extraHeaders: Map<String, String> = emptyMap()
    ) {
        val reason = when (status) {
            200 -> "OK"
            400 -> "Bad Request"
            401 -> "Unauthorized"
            403 -> "Forbidden"
            404 -> "Not Found"
            405 -> "Method Not Allowed"
            else -> "Response"
        }
        val sb = StringBuilder()
        sb.append("HTTP/1.1 $status $reason\r\n")
        sb.append("Content-Type: $contentType\r\n")
        sb.append("Content-Length: ${body.size}\r\n")
        sb.append("Access-Control-Allow-Origin: *\r\n")
        sb.append("Access-Control-Allow-Methods: GET, PUT, POST, DELETE, OPTIONS\r\n")
        sb.append("Access-Control-Allow-Headers: Authorization, Content-Type, X-Device-Name\r\n")
        sb.append("Connection: close\r\n")
        for ((k, v) in extraHeaders) sb.append("$k: $v\r\n")
        sb.append("\r\n")
        outs.write(sb.toString().toByteArray(Charsets.ISO_8859_1))
        outs.write(body)
        outs.flush()
    }
}

/**
 * 3. Wi-Fi Router USB / NAS FTP Storage Backend (`RouterFtpBackend`) — 100% compatible with Desktop
 *    `FtpStorageBackend` (`ftp://192.168.1.1/usb`).
 */
class RouterFtpBackend(
    ftpUrl: String,
    private val username: String = "anonymous",
    private val password: String = ""
) : MobileSyncBackend {

    override val name: String = "ftp"
    private val uri = URI(if (ftpUrl.contains("://")) ftpUrl else "ftp://$ftpUrl")
    private val host: String = uri.host ?: throw IllegalArgumentException("Invalid FTP host in '$ftpUrl'")
    private val port: Int = if (uri.port > 0) uri.port else 21
    private val baseDir: String = uri.path?.trimEnd('/')?.ifEmpty { "/" } ?: "/"

    private fun <T> withFtp(block: (FTPClient) -> T): T {
        val ftp = FTPClient()
        ftp.connectTimeout = 15_000
        ftp.defaultTimeout = 30_000
        try {
            ftp.connect(host, port)
            if (!ftp.login(username.ifEmpty { "anonymous" }, password)) {
                throw RuntimeException("Router FTP login rejected for user '$username'")
            }
            ftp.enterLocalPassiveMode()
            ftp.setFileType(FTP.BINARY_FILE_TYPE)
            ensureDirs(ftp, baseDir)
            ftp.changeWorkingDirectory(baseDir)
            return block(ftp)
        } finally {
            runCatching {
                if (ftp.isConnected) {
                    ftp.logout()
                    ftp.disconnect()
                }
            }
        }
    }

    private fun ensureDirs(ftp: FTPClient, dirPath: String) {
        var cur = ""
        for (part in dirPath.split('/').filter { it.isNotEmpty() }) {
            cur += "/$part"
            if (!ftp.changeWorkingDirectory(cur)) {
                ftp.makeDirectory(cur)
                ftp.changeWorkingDirectory(cur)
            }
        }
    }

    override fun list(): Map<String, RemoteFileEntry> = withFtp { ftp ->
        val out = LinkedHashMap<String, RemoteFileEntry>()
        fun walk(prefix: String, remoteDir: String) {
            for (f in ftp.listFiles(remoteDir).orEmpty()) {
                val nm = f.name ?: continue
                if (nm == "." || nm == "..") continue
                val rel = if (prefix.isEmpty()) nm else "$prefix/$nm"
                if (f.isDirectory) {
                    walk(rel, "$remoteDir/$nm")
                } else if (f.isFile && !SyncEngineMobile.isIgnored(rel)) {
                    val mt = (f.timestamp?.timeInMillis ?: 0L) / 1000.0
                    out[rel] = RemoteFileEntry(f.size, mt, "${f.size}-${mt.toLong()}")
                }
            }
        }
        walk("", baseDir)
        out
    }

    override fun upload(localFile: File, relPath: String): RemoteFileEntry = withFtp { ftp ->
        val fullRemote = "$baseDir/${relPath.trimStart('/')}"
        val parent = fullRemote.substringBeforeLast('/', "/")
        ensureDirs(ftp, parent)
        localFile.inputStream().use { ins ->
            if (!ftp.storeFile(fullRemote, ins)) {
                throw RuntimeException("FTP upload failed for '$relPath': ${ftp.replyString}")
            }
        }
        val mt = localFile.lastModified() / 1000.0
        RemoteFileEntry(localFile.length(), mt, "${localFile.length()}-${mt.toLong()}")
    }

    override fun download(relPath: String, destFile: File): Unit = withFtp { ftp ->
        val fullRemote = "$baseDir/${relPath.trimStart('/')}"
        destFile.parentFile?.mkdirs()
        destFile.outputStream().use { outs ->
            if (!ftp.retrieveFile(fullRemote, outs)) {
                throw RuntimeException("FTP download failed for '$relPath': ${ftp.replyString}")
            }
        }
    }

    override fun delete(relPath: String): Unit = withFtp { ftp ->
        ftp.deleteFile("$baseDir/${relPath.trimStart('/')}")
    }
}

/**
 * 4. Local / OTG USB Folder Backend (`LocalFolderBackend`)
 */
class LocalFolderBackend(private val targetRoot: File) : MobileSyncBackend {
    override val name: String = "folder"

    init {
        targetRoot.mkdirs()
    }

    override fun list(): Map<String, RemoteFileEntry> {
        val out = LinkedHashMap<String, RemoteFileEntry>()
        targetRoot.walkTopDown().forEach { f ->
            if (f.isFile) {
                val rel = f.relativeTo(targetRoot).invariantSeparatorsPath
                if (!SyncEngineMobile.isIgnored(rel)) {
                    val mt = f.lastModified() / 1000.0
                    out[rel] = RemoteFileEntry(f.length(), mt, VaultCrypto.sha256Hex(f.readBytes()))
                }
            }
        }
        return out
    }

    override fun upload(localFile: File, relPath: String): RemoteFileEntry {
        val dst = File(targetRoot, relPath)
        dst.parentFile?.mkdirs()
        localFile.copyTo(dst, overwrite = true)
        dst.setLastModified(localFile.lastModified())
        return RemoteFileEntry(dst.length(), dst.lastModified() / 1000.0, VaultCrypto.sha256Hex(dst.readBytes()))
    }

    override fun download(relPath: String, destFile: File) {
        val src = File(targetRoot, relPath)
        require(src.isFile) { "Remote file not found: $relPath" }
        destFile.parentFile?.mkdirs()
        src.copyTo(destFile, overwrite = true)
    }

    override fun delete(relPath: String) {
        File(targetRoot, relPath).delete()
    }
}

/**
 * 5. Transparent `MOVAULT1` Encrypted Sync Wrapper (`VaultEncryptedBackend`) — matches Desktop `VaultSyncBackend`!
 *    Every remote file on Router USB / Gmail / Cloud is stored encrypted with `VaultCrypto` so stolen drives leak ZERO!
 */
class VaultEncryptedBackend(
    private val inner: MobileSyncBackend,
    private val keyStore: VaultCrypto.DeviceKeyStore,
    private val passphrase: String = ""
) : MobileSyncBackend {
    override val name: String = "${inner.name}+vault"

    override fun list(): Map<String, RemoteFileEntry> =
        inner.list().mapKeys { (k, _) -> k.removeSuffix(".movault") }

    override fun upload(localFile: File, relPath: String): RemoteFileEntry {
        val encrypted = VaultCrypto.encryptBytes(
            localFile.readBytes(),
            originalName = localFile.name,
            originalPath = relPath,
            store = keyStore,
            passphrase = passphrase
        )
        val tmp = File.createTempFile("movault_up_", ".movault")
        try {
            tmp.writeBytes(encrypted)
            tmp.setLastModified(localFile.lastModified())
            val remoteEntry = inner.upload(tmp, "$relPath.movault")
            return RemoteFileEntry(
                size = localFile.length(),
                mtime = localFile.lastModified() / 1000.0,
                etag = remoteEntry.etag
            )
        } finally {
            tmp.delete()
        }
    }

    override fun download(relPath: String, destFile: File) {
        val tmp = File.createTempFile("movault_dl_", ".movault")
        try {
            inner.download("$relPath.movault", tmp)
            val decrypted = VaultCrypto.decryptBytes(tmp.readBytes(), keyStore, passphrase)
            destFile.parentFile?.mkdirs()
            destFile.writeBytes(decrypted.bytes)
        } finally {
            tmp.delete()
        }
    }

    override fun delete(relPath: String) {
        inner.delete("$relPath.movault")
    }
}

/**
 * Stateful Three-Way Synchronization Engine (`SyncEngineMobile`) matching Desktop `SyncEngine` in
 * `app/services/cloud_sync.py` 1-to-1:
 *  - Tracks `(size, mtime, sha256, etag, rsize, rmtime)` per relative path
 *  - Detects local-only edits, remote-only edits, deletions, and concurrent PC+Mobile edits
 *  - Preserves BOTH copies on concurrent edits (`name (conflict android <timestamp>).ext`) — ZERO data loss!
 */
class SyncEngineMobile(
    private val localRoot: File,
    private val backend: MobileSyncBackend,
    stateFileOverride: File? = null,
    private val propagateRemoteDelete: Boolean = false
) {
    private val stateFile: File = stateFileOverride ?: File(localRoot, ".myoffice_sync.json")

    init {
        localRoot.mkdirs()
    }

    companion object {
        fun isIgnored(rel: String): Boolean {
            val n = File(rel).name
            return n.startsWith("~") ||
                n.startsWith(".myoffice_sync") ||
                n.startsWith(".~lock") ||
                n.startsWith(".dl_") ||
                n.startsWith(".put_") ||
                n.endsWith(".tmp") ||
                n.endsWith(".partial") ||
                n.endsWith(".swp") ||
                n.equals("Thumbs.db", true) ||
                n.equals(".DS_Store", true)
        }
    }

    private fun loadState(): JSONObject =
        runCatching { JSONObject(stateFile.readText(Charsets.UTF_8)) }
            .getOrElse { JSONObject().put("files", JSONObject()) }

    private fun saveState(state: JSONObject) {
        val tmp = File(stateFile.parentFile, "${stateFile.name}.tmp")
        tmp.writeText(state.toString(1), Charsets.UTF_8)
        tmp.renameTo(stateFile)
    }

    private fun scanLocal(): Map<String, Pair<Long, Double>> {
        val out = LinkedHashMap<String, Pair<Long, Double>>()
        val rootCanon = localRoot.canonicalFile
        rootCanon.walkTopDown().forEach { f ->
            if (f.isFile) {
                val rel = f.relativeTo(rootCanon).invariantSeparatorsPath
                if (!isIgnored(rel)) {
                    out[rel] = f.length() to (f.lastModified() / 1000.0)
                }
            }
        }
        return out
    }

    private fun localChanged(rel: String, info: Pair<Long, Double>, known: JSONObject?): Boolean {
        if (known == null) return true
        if (info.first == known.optLong("size", -1L) &&
            kotlin.math.abs(info.second - known.optDouble("mtime", 0.0)) < 1.0
        ) {
            return false
        }
        val sha = VaultCrypto.sha256Hex(File(localRoot, rel).readBytes())
        return sha != known.optString("hash", "")
    }

    private fun remoteChanged(entry: RemoteFileEntry, known: JSONObject?): Boolean {
        if (known == null) return true
        val knownEtag = known.optString("etag", "")
        if (entry.etag.isNotEmpty() && knownEtag.isNotEmpty()) {
            return entry.etag != knownEtag
        }
        return entry.size != known.optLong("rsize", -1L) ||
            kotlin.math.abs(entry.mtime - known.optDouble("rmtime", 0.0)) > 1.0
    }

    private fun recordSynced(filesState: JSONObject, rel: String, remote: RemoteFileEntry) {
        val f = File(localRoot, rel)
        filesState.put(
            rel,
            JSONObject()
                .put("size", f.length())
                .put("mtime", f.lastModified() / 1000.0)
                .put("hash", VaultCrypto.sha256Hex(f.readBytes()))
                .put("etag", remote.etag)
                .put("rsize", remote.size)
                .put("rmtime", remote.mtime)
        )
    }

    private fun conflictName(rel: String): String {
        val f = File(rel)
        val stamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HHmmss"))
        val ext = if (f.extension.isNotEmpty()) ".${f.extension}" else ""
        val conflictLeaf = "${f.nameWithoutExtension} (conflict android $stamp)$ext"
        return if (f.parent.isNullOrEmpty()) conflictLeaf else "${f.parent!!.replace('\\', '/')}/$conflictLeaf"
    }

    @Synchronized
    fun syncOnce(): SyncReportMobile {
        val rep = SyncReportMobile()
        val state = loadState()
        val filesState = state.optJSONObject("files") ?: JSONObject().also { state.put("files", it) }

        val remote = backend.list()
        val local = scanLocal()
        val allNames = sortedSetOf<String>().apply {
            addAll(remote.keys)
            addAll(local.keys)
            addAll(filesState.keys().asSequence().toList())
        }

        for (rel in allNames) {
            try {
                val linfo = local[rel]
                val rentry = remote[rel]
                val known = filesState.optJSONObject(rel)
                val localFile = File(localRoot, rel)

                if (linfo != null && rentry != null) {
                    val lch = localChanged(rel, linfo, known)
                    val rch = remoteChanged(rentry, known)
                    if (lch && rch) {
                        val tmpCheck = File.createTempFile("sync_cmp_", ".bin")
                        val identical = try {
                            backend.download(rel, tmpCheck)
                            VaultCrypto.sha256Hex(tmpCheck.readBytes()) == VaultCrypto.sha256Hex(localFile.readBytes())
                        } finally {
                            tmpCheck.delete()
                        }
                        if (identical) {
                            recordSynced(filesState, rel, rentry)
                        } else {
                            val cname = conflictName(rel)
                            val conflictFile = File(localRoot, cname)
                            conflictFile.parentFile?.mkdirs()
                            localFile.copyTo(conflictFile, overwrite = true)
                            backend.download(rel, localFile)
                            recordSynced(filesState, rel, rentry)
                            val upConflict = backend.upload(conflictFile, cname)
                            recordSynced(filesState, cname, upConflict)
                            rep.conflicts.add(rel)
                        }
                    } else if (lch) {
                        val up = backend.upload(localFile, rel)
                        recordSynced(filesState, rel, up)
                        rep.uploaded.add(rel)
                    } else if (rch) {
                        backend.download(rel, localFile)
                        recordSynced(filesState, rel, rentry)
                        rep.downloaded.add(rel)
                    }
                } else if (linfo != null && rentry == null) {
                    if (known != null && !localChanged(rel, linfo, known)) {
                        localFile.delete()
                        filesState.remove(rel)
                        rep.deletedLocal.add(rel)
                    } else {
                        val up = backend.upload(localFile, rel)
                        recordSynced(filesState, rel, up)
                        rep.uploaded.add(rel)
                    }
                } else if (linfo == null && rentry != null) {
                    if (known != null && !remoteChanged(rentry, known)) {
                        if (propagateRemoteDelete) {
                            backend.delete(rel)
                            filesState.remove(rel)
                            rep.deletedRemote.add(rel)
                        } else {
                            backend.download(rel, localFile)
                            recordSynced(filesState, rel, rentry)
                            rep.downloaded.add(rel)
                        }
                    } else {
                        backend.download(rel, localFile)
                        recordSynced(filesState, rel, rentry)
                        rep.downloaded.add(rel)
                    }
                } else {
                    filesState.remove(rel)
                }
            } catch (e: Exception) {
                rep.errors.add("$rel: ${e.message ?: e.javaClass.simpleName}")
            }
        }
        saveState(state)
        return rep
    }
}
