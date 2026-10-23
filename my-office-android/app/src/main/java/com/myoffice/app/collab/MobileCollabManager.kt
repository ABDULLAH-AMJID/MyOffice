package com.myoffice.app.collab

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URLDecoder
import java.util.Base64
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * Live Multi-Room PC <-> Android Collaboration & Real-Time Team Chat Client (`MobileCollabManager`):
 *  - Accepts ANY room link scanned via QR or pasted (`http://192.168.x.x:8765/f/<room>`,
 *    `myoffice://join?ws=...&http=...&room=...`, `ws://192.168.x.x:8765/<room>`, or `ip:port/room`)
 *  - Auto-downloads the shared file snapshot (`/api/rooms/<room>/file`) of ANY file format
 *    (`.docx`, `.xlsx`, `.pptx`, `.pdf`, `.png`, `.jpg`, `.csv`, `.txt`, `.zip`, etc.)
 *  - Normalizes chat entries whether stored as JSON Objects OR JSON Strings in Yjs CRDT `ychat`
 *    so BOTH Mobile -> PC AND PC -> Mobile messages stream in real time without loss!
 */
class MobileCollabManager(
    private val cacheDir: File,
    private val onEvent: (room: String, eventJson: JSONObject) -> Unit
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // keep WebSocket alive indefinitely
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    data class ActiveSession(
        val room: String,
        val httpBase: String,
        val wsUrl: String,
        val userName: String,
        val userColor: String,
        val clientId: String,
        var localFile: File?,
        var filename: String = "",
        var kind: String = "any",
        var webSocket: WebSocket? = null,
        @Volatile var connected: Boolean = false,
        val chatHistory: MutableList<JSONObject> = mutableListOf()
    )

    private val sessions = ConcurrentHashMap<String, ActiveSession>()

    companion object {
        private val KIND_EXT_MAP = mapOf(
            "writer" to ".docx",
            "sheet" to ".xlsx",
            "slide" to ".pptx",
            "pdf" to ".pdf",
            "photos" to ".png"
        )

        fun ensureSafeFilename(rawName: String, kind: String, room: String): String {
            var base = rawName.trim()
                .replace('\\', '_')
                .replace('/', '_')
                .replace(':', '_')
                .ifEmpty { room.ifEmpty { "Shared_Document" } }
            val hasExt = base.substringAfterLast('.', "").isNotEmpty() && '.' in base
            if (!hasExt) {
                val ext = KIND_EXT_MAP[kind.lowercase()] ?: ""
                if (ext.isNotEmpty()) base += ext
            }
            return base
        }

        fun normalizeChatItem(rawItem: Any?): JSONObject? {
            return when (rawItem) {
                is JSONObject -> rawItem
                is String -> runCatching { JSONObject(rawItem) }.getOrNull()
                else -> null
            }
        }

        fun parseCollabJoinTarget(rawLink: String): JSONObject {
            var raw = rawLink.trim()
            if (raw.startsWith("myoffice://join", ignoreCase = true)) {
                val params = raw.substringAfter('?', "").split('&').filter { it.isNotEmpty() }.associate {
                    URLDecoder.decode(it.substringBefore('='), "UTF-8") to
                        URLDecoder.decode(it.substringAfter('=', ""), "UTF-8")
                }
                val room = params["room"] ?: "shared-room"
                val httpBase = (params["http"] ?: "").trimEnd('/')
                val wsBase = params["ws"] ?: ""
                val wsUrl = if ('?' in wsBase) "$wsBase&client=mobile" else "$wsBase?client=mobile"
                return JSONObject()
                    .put("room", room)
                    .put("httpBase", httpBase)
                    .put("wsUrl", wsUrl)
                    .put("filename", params["name"] ?: "")
            }
            if (!raw.contains("://") && raw.any { it.isDigit() } && ':' in raw) {
                raw = "http://$raw"
            }
            if (raw.startsWith("http://") || raw.startsWith("https://")) {
                val clean = raw.trimEnd('/')
                val idx = clean.indexOf("/f/")
                val httpBase: String
                val room: String
                if (idx > 0) {
                    httpBase = clean.substring(0, idx)
                    room = URLDecoder.decode(clean.substring(idx + 3), "UTF-8").ifEmpty { "default" }
                } else {
                    val schemeEnd = clean.indexOf("://") + 3
                    val slashIdx = clean.indexOf('/', schemeEnd)
                    if (slashIdx > 0) {
                        httpBase = clean.substring(0, slashIdx)
                        room = URLDecoder.decode(clean.substring(slashIdx + 1), "UTF-8").ifEmpty { "default" }
                    } else {
                        httpBase = clean
                        room = "default"
                    }
                }
                val wsScheme = if (clean.startsWith("https://")) "wss://" else "ws://"
                val hostPort = httpBase.removePrefix("http://").removePrefix("https://")
                return JSONObject()
                    .put("room", room)
                    .put("httpBase", httpBase)
                    .put("wsUrl", "$wsScheme$hostPort/$room?client=mobile")
            }
            if (raw.startsWith("ws://") || raw.startsWith("wss://")) {
                val baseNoQuery = raw.substringBefore('?').trimEnd('/')
                val schemeEnd = baseNoQuery.indexOf("://") + 3
                val slashIdx = baseNoQuery.indexOf('/', schemeEnd)
                val hostPort = if (slashIdx > 0) baseNoQuery.substring(schemeEnd, slashIdx) else baseNoQuery.substring(schemeEnd)
                val room = if (slashIdx > 0) URLDecoder.decode(baseNoQuery.substring(slashIdx + 1), "UTF-8").ifEmpty { "default" } else "default"
                val httpScheme = if (raw.startsWith("wss://")) "https://" else "http://"
                return JSONObject()
                    .put("room", room)
                    .put("httpBase", "$httpScheme$hostPort")
                    .put("wsUrl", "${if (slashIdx > 0) baseNoQuery else "$baseNoQuery/$room"}?client=mobile")
            }
            throw IllegalArgumentException("Unrecognized collaboration link: '$rawLink'")
        }
    }

    private fun downloadRoomSnapshotToDisk(httpBase: String, room: String, saveDir: File, fallbackName: String, fallbackKind: String): Pair<File?, Pair<String, String>> {
        if (httpBase.isEmpty()) return null to (ensureSafeFilename(fallbackName, fallbackKind, room) to fallbackKind)
        saveDir.mkdirs()
        var metaName = fallbackName
        var kind = fallbackKind
        runCatching {
            http.newCall(Request.Builder().url("$httpBase/api/rooms/$room/meta").get().build()).execute().use { r ->
                if (r.isSuccessful) {
                    val m = JSONObject(r.body?.string() ?: "{}")
                    metaName = m.optString("filename", metaName).ifEmpty { metaName }
                    kind = m.optString("kind", kind).ifEmpty { kind }
                }
            }
        }
        val safeName = ensureSafeFilename(metaName, kind, room)
        val destFile = File(saveDir, safeName)
        runCatching {
            http.newCall(Request.Builder().url("$httpBase/api/rooms/$room/file").get().build()).execute().use { r ->
                if (r.isSuccessful) {
                    val bytes = r.body?.bytes() ?: ByteArray(0)
                    if (bytes.isNotEmpty()) {
                        destFile.writeBytes(bytes)
                    }
                }
            }
        }
        return (if (destFile.isFile && destFile.length() > 0) destFile else null) to (safeName to kind)
    }

    fun joinRoom(
        rawLink: String,
        userName: String = "Mobile User",
        userColor: String = "#10B981",
        saveDir: File = cacheDir
    ): JSONObject {
        val parsed = parseCollabJoinTarget(rawLink)
        val room = parsed.getString("room")
        val httpBase = parsed.getString("httpBase")
        val wsUrl = parsed.getString("wsUrl")

        // Leave existing connection on same room cleanly before re-joining
        leaveRoom(room)

        val (initialFile, nameAndKind) = downloadRoomSnapshotToDisk(
            httpBase,
            room,
            saveDir,
            parsed.optString("filename", ""),
            "any"
        )

        val session = ActiveSession(
            room = room,
            httpBase = httpBase,
            wsUrl = wsUrl,
            userName = userName,
            userColor = userColor,
            clientId = "mob-${UUID.randomUUID().toString().take(8)}",
            localFile = initialFile,
            filename = nameAndKind.first,
            kind = nameAndKind.second
        )
        sessions[room] = session

        val initialStateLatch = java.util.concurrent.CountDownLatch(1)
        val seenChatIds = java.util.Collections.synchronizedSet(HashSet<String>())
        var initialWriterText = ""
        var initialSheets = JSONObject()

        val ws = http.newWebSocket(
            Request.Builder().url(wsUrl).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    session.connected = true
                    webSocket.send(
                        JSONObject()
                            .put("type", "mobile_hello")
                            .put("sender", userName)
                            .put("sender_id", session.clientId)
                            .put("color", userColor)
                            .toString()
                    )
                    onEvent(room, JSONObject().put("type", "connected").put("room", room))
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching {
                        val obj = JSONObject(text)
                        val mtype = obj.optString("type")
                        if (mtype == "state") {
                            val metaObj = obj.optJSONObject("meta")
                            if (metaObj != null) {
                                val mk = metaObj.optString("kind", session.kind).ifEmpty { session.kind }
                                val mn = ensureSafeFilename(metaObj.optString("filename", session.filename), mk, room)
                                session.kind = mk
                                session.filename = mn
                                // If file was not downloaded yet (or host just uploaded snapshot), pull it right now!
                                if ((session.localFile == null || session.localFile?.length() == 0L) && metaObj.optLong("size", 0L) > 0L) {
                                    val (pulled, nk) = downloadRoomSnapshotToDisk(httpBase, room, saveDir, mn, mk)
                                    if (pulled != null) {
                                        session.localFile = pulled
                                        session.filename = nk.first
                                        onEvent(
                                            room,
                                            JSONObject()
                                                .put("type", "file_push_notify")
                                                .put("meta", metaObj)
                                                .put("relPath", "Collaborations/${session.filename}")
                                                .put("filename", session.filename)
                                        )
                                    }
                                }
                            }

                            initialWriterText = obj.optString("writer_text", initialWriterText)
                            obj.optJSONObject("sheets")?.let { initialSheets = it }

                            // If room only has plain Writer CRDT text without binary file yet, materialize readable .txt file locally!
                            if (session.localFile == null && initialWriterText.isNotEmpty()) {
                                val fallbackTxt = File(saveDir.apply { mkdirs() }, "${room}.txt")
                                fallbackTxt.writeText(initialWriterText, Charsets.UTF_8)
                                session.localFile = fallbackTxt
                                session.filename = fallbackTxt.name
                                session.kind = "writer"
                            }

                            val arr = obj.optJSONArray("chat") ?: JSONArray()
                            val newMsgs = mutableListOf<JSONObject>()
                            synchronized(session.chatHistory) {
                                session.chatHistory.clear()
                                for (i in 0 until arr.length()) {
                                    normalizeChatItem(arr.opt(i))?.let { m ->
                                        session.chatHistory.add(m)
                                        val mid = m.optString("id", "${m.optString("sender")}:${m.optString("text")}:${m.optDouble("ts")}")
                                        if (seenChatIds.add(mid)) {
                                            newMsgs.add(m)
                                        }
                                    }
                                }
                            }
                            val normalizedChatArr = JSONArray()
                            synchronized(session.chatHistory) {
                                session.chatHistory.forEach { normalizedChatArr.put(it) }
                            }
                            obj.put("chat", normalizedChatArr)
                            if (session.localFile != null) {
                                obj.put("relPath", "Collaborations/${session.filename}")
                                obj.put("filename", session.filename)
                            }
                            initialStateLatch.countDown()
                            onEvent(room, obj)
                            for (nm in newMsgs) {
                                onEvent(room, JSONObject().put("type", "chat_push").put("message", nm))
                            }
                            return@runCatching
                        } else if (mtype == "chat_push") {
                            normalizeChatItem(obj.opt("message"))?.let { m ->
                                obj.put("message", m)
                                val mid = m.optString("id", "${m.optString("sender")}:${m.optString("text")}:${m.optDouble("ts")}")
                                seenChatIds.add(mid)
                                synchronized(session.chatHistory) {
                                    if (session.chatHistory.none { it.optString("id") == mid }) {
                                        session.chatHistory.add(m)
                                    }
                                }
                            }
                        } else if (mtype == "file_push_notify") {
                            val metaObj = obj.optJSONObject("meta")
                            val (pulled, nk) = downloadRoomSnapshotToDisk(
                                httpBase,
                                room,
                                saveDir,
                                metaObj?.optString("filename", session.filename) ?: session.filename,
                                metaObj?.optString("kind", session.kind) ?: session.kind
                            )
                            if (pulled != null) {
                                session.localFile = pulled
                                session.filename = nk.first
                                session.kind = nk.second
                                obj.put("relPath", "Collaborations/${session.filename}")
                                obj.put("filename", session.filename)
                            }
                        }
                        onEvent(room, obj)
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    session.connected = false
                    initialStateLatch.countDown()
                    onEvent(room, JSONObject().put("type", "disconnected").put("room", room))
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    session.connected = false
                    initialStateLatch.countDown()
                    onEvent(
                        room,
                        JSONObject().put("type", "error").put("room", room).put("message", t.message ?: "Network error")
                    )
                }
            }
        )
        session.webSocket = ws
        initialStateLatch.await(4, TimeUnit.SECONDS)

        val chatSnap = JSONArray()
        synchronized(session.chatHistory) {
            session.chatHistory.forEach { chatSnap.put(it) }
        }
        val relPath = if (session.localFile != null && session.localFile!!.isFile) {
            "Collaborations/${session.filename}"
        } else {
            ""
        }

        return JSONObject()
            .put("ok", true)
            .put("room", room)
            .put("filename", session.filename)
            .put("fileName", session.filename)
            .put("kind", session.kind)
            .put("relPath", relPath)
            .put("localPath", session.localFile?.absolutePath ?: "")
            .put("hasFile", relPath.isNotEmpty())
            .put("writerText", initialWriterText)
            .put("sheets", initialSheets)
            .put("chat", chatSnap)
            .put("wsUrl", wsUrl)
            .put("httpBase", httpBase)
    }

    fun sendChat(room: String, text: String): Boolean {
        val s = sessions[room] ?: return false
        val msg = JSONObject()
            .put("type", "chat_send")
            .put("id", "mob-${System.currentTimeMillis()}-${(100..999).random()}")
            .put("sender", s.userName)
            .put("sender_id", s.clientId)
            .put("color", s.userColor)
            .put("text", text)
            .put("ts", System.currentTimeMillis() / 1000.0)
        return s.webSocket?.send(msg.toString()) ?: false
    }

    fun pushWriterText(room: String, fullText: String): Boolean {
        val s = sessions[room] ?: return false
        return s.webSocket?.send(
            JSONObject()
                .put("type", "writer_update")
                .put("sender", s.userName)
                .put("text", fullText)
                .toString()
        ) ?: false
    }

    fun pushSheetCell(room: String, sheetName: String, r1c1OrA1: String, rawFormulaOrVal: String): Boolean {
        val s = sessions[room] ?: return false
        return s.webSocket?.send(
            JSONObject()
                .put("type", "sheet_cell")
                .put("sheet", sheetName)
                .put("cell", r1c1OrA1)
                .put("value", rawFormulaOrVal)
                .toString()
        ) ?: false
    }

    fun pushBinaryFileSnapshot(room: String, file: File, kind: String = "any"): Boolean {
        val s = sessions[room] ?: return false
        val b64 = Base64.getEncoder().encodeToString(file.readBytes())
        return s.webSocket?.send(
            JSONObject()
                .put("type", "file_push")
                .put("filename", file.name)
                .put("kind", kind)
                .put("b64", b64)
                .toString()
        ) ?: false
    }

    fun activeRooms(): JSONArray {
        val arr = JSONArray()
        for (s in sessions.values) {
            arr.put(
                JSONObject()
                    .put("room", s.room)
                    .put("filename", s.filename)
                    .put("relPath", if (s.localFile?.isFile == true) "Collaborations/${s.filename}" else "")
                    .put("connected", s.connected)
                    .put("localPath", s.localFile?.absolutePath ?: "")
                    .put("httpBase", s.httpBase)
                    .put("chatCount", synchronized(s.chatHistory) { s.chatHistory.size })
            )
        }
        return arr
    }

    fun leaveRoom(room: String) {
        sessions.remove(room)?.let {
            it.connected = false
            runCatching { it.webSocket?.close(1000, "User left room") }
        }
    }

    fun shutdownAll() {
        sessions.keys.toList().forEach { leaveRoom(it) }
    }
}
