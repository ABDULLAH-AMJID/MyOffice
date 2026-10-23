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
 *    `myoffice://join?ws=...&http=...&room=...`, or `ws://192.168.x.x:8765/<room>`)
 *  - Auto-downloads the shared file snapshot (`/api/rooms/<room>/file`) of ANY file format
 *    (`.docx`, `.xlsx`, `.pptx`, `.pdf`, `.png`, `.jpg`, `.csv`, `.txt`, `.zip`, etc.)
 *  - Maintains multiple simultaneous active room WebSocket connections (`sessionsByRoom`) so users can
 *    collaborate on MULTIPLE open files at the exact same time — with real-time Writer text co-editing,
 *    Spreadsheet live cell updates, snapshot push/pull for Slides/PDF/Photos, and Live Team Chat!
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
        var webSocket: WebSocket? = null,
        @Volatile var connected: Boolean = false,
        val chatHistory: MutableList<JSONObject> = mutableListOf()
    )

    private val sessions = ConcurrentHashMap<String, ActiveSession>()

    companion object {
        fun parseCollabJoinTarget(rawLink: String): JSONObject {
            val raw = rawLink.trim()
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
            if (raw.startsWith("http://") || raw.startsWith("https://")) {
                val clean = raw.trimEnd('/')
                val idx = clean.indexOf("/f/")
                val httpBase = if (idx > 0) clean.substring(0, idx) else clean.substringBeforeLast('/')
                val room = if (idx > 0) URLDecoder.decode(clean.substring(idx + 3), "UTF-8") else "shared-room"
                val wsScheme = if (clean.startsWith("https://")) "wss://" else "ws://"
                val hostPort = httpBase.removePrefix("http://").removePrefix("https://")
                return JSONObject()
                    .put("room", room)
                    .put("httpBase", httpBase)
                    .put("wsUrl", "$wsScheme$hostPort/$room?client=mobile")
            }
            if (raw.startsWith("ws://") || raw.startsWith("wss://")) {
                val baseNoQuery = raw.substringBefore('?').trimEnd('/')
                val room = URLDecoder.decode(baseNoQuery.substringAfterLast('/'), "UTF-8").ifEmpty { "default" }
                val hostPort = baseNoQuery.removePrefix("ws://").removePrefix("wss://").substringBefore('/')
                val httpScheme = if (raw.startsWith("wss://")) "https://" else "http://"
                return JSONObject()
                    .put("room", room)
                    .put("httpBase", "$httpScheme$hostPort")
                    .put("wsUrl", "$baseNoQuery?client=mobile")
            }
            throw IllegalArgumentException("Unrecognized collaboration link: '$rawLink'")
        }
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

        // 1. Fetch room metadata + initial binary snapshot over HTTP so ANY format works out-of-the-box!
        saveDir.mkdirs()
        var filename = parsed.optString("filename", "").ifEmpty { room }
        runCatching {
            http.newCall(Request.Builder().url("$httpBase/api/rooms/$room/meta").get().build()).execute().use { r ->
                if (r.isSuccessful) {
                    val m = JSONObject(r.body?.string() ?: "{}")
                    filename = m.optString("filename", filename).ifEmpty { filename }
                }
            }
        }
        val destFile = File(saveDir, filename)
        runCatching {
            http.newCall(Request.Builder().url("$httpBase/api/rooms/$room/file").get().build()).execute().use { r ->
                if (r.isSuccessful) {
                    val bytes = r.body?.bytes() ?: ByteArray(0)
                    if (bytes.isNotEmpty()) destFile.writeBytes(bytes)
                }
            }
        }

        val session = ActiveSession(
            room = room,
            httpBase = httpBase,
            wsUrl = wsUrl,
            userName = userName,
            userColor = userColor,
            clientId = "mob-${UUID.randomUUID().toString().take(8)}",
            localFile = if (destFile.isFile) destFile else null
        )
        sessions[room] = session

        val openLatch = java.util.concurrent.CountDownLatch(1)
        val seenChatIds = java.util.Collections.synchronizedSet(HashSet<String>())

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
                    openLatch.countDown()
                    onEvent(room, JSONObject().put("type", "connected").put("room", room))
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    runCatching {
                        val obj = JSONObject(text)
                        if (obj.optString("type") == "state") {
                            val arr = obj.optJSONArray("chat") ?: JSONArray()
                            val newMsgs = mutableListOf<JSONObject>()
                            synchronized(session.chatHistory) {
                                session.chatHistory.clear()
                                for (i in 0 until arr.length()) {
                                    arr.optJSONObject(i)?.let { m ->
                                        session.chatHistory.add(m)
                                        val mid = m.optString("id", "${m.optString("sender")}:${m.optString("text")}:${m.optDouble("ts")}")
                                        if (seenChatIds.add(mid)) {
                                            newMsgs.add(m)
                                        }
                                    }
                                }
                            }
                            onEvent(room, obj)
                            for (nm in newMsgs) {
                                onEvent(room, JSONObject().put("type", "chat_push").put("message", nm))
                            }
                            return@runCatching
                        } else if (obj.optString("type") == "chat_push") {
                            obj.optJSONObject("message")?.let { m ->
                                val mid = m.optString("id", "${m.optString("sender")}:${m.optString("text")}:${m.optDouble("ts")}")
                                seenChatIds.add(mid)
                                synchronized(session.chatHistory) { session.chatHistory.add(m) }
                            }
                        } else if (obj.optString("type") == "file_push_notify") {
                            session.localFile?.let { target ->
                                runCatching {
                                    http.newCall(Request.Builder().url("$httpBase/api/rooms/$room/file").get().build())
                                        .execute().use { r ->
                                            if (r.isSuccessful) {
                                                val b = r.body?.bytes() ?: ByteArray(0)
                                                if (b.isNotEmpty()) target.writeBytes(b)
                                            }
                                        }
                                }
                            }
                        }
                        onEvent(room, obj)
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    session.connected = false
                    openLatch.countDown()
                    onEvent(room, JSONObject().put("type", "disconnected").put("room", room))
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    session.connected = false
                    openLatch.countDown()
                    onEvent(
                        room,
                        JSONObject().put("type", "error").put("room", room).put("message", t.message ?: "Network error")
                    )
                }
            }
        )
        session.webSocket = ws
        openLatch.await(4, TimeUnit.SECONDS)

        return JSONObject()
            .put("ok", true)
            .put("room", room)
            .put("filename", filename)
            .put("localPath", session.localFile?.absolutePath ?: "")
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
