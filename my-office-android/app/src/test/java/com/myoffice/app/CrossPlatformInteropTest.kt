package com.myoffice.app

import com.myoffice.app.ai.MobileAiClient
import com.myoffice.app.collab.MobileCollabManager
import com.myoffice.app.crypto.VaultCrypto
import com.myoffice.app.ooxml.OoxmlEngine
import com.myoffice.app.pdf.PdfStudioEngine
import com.myoffice.app.qr.QrCodec
import com.myoffice.app.sync.GmailImapBackend
import com.myoffice.app.sync.LanNetworkUtils
import com.myoffice.app.sync.MobileSyncHostServer
import com.myoffice.app.sync.PcLanPeerBackend
import com.myoffice.app.sync.SyncEngineMobile
import com.myoffice.app.sync.VaultEncryptedBackend
import com.myoffice.app.update.OtaUpdateManager
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.nio.file.Files
import java.security.Security
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class CrossPlatformInteropTest {

    companion object {
        private val pcSuiteDir: File = File("../my-office-suite").takeIf { it.isDirectory }
            ?: File("my-office-suite").takeIf { it.isDirectory }
            ?: File("/home/user/my-office-suite")

        @JvmStatic
        @BeforeClass
        fun setupProviders() {
            if (Security.getProvider(BouncyCastleProvider.PROVIDER_NAME) == null) {
                Security.addProvider(BouncyCastleProvider())
            }
        }
    }

    private fun readNextJsonLine(reader: BufferedReader): JSONObject {
        val seen = mutableListOf<String>()
        while (true) {
            val line = reader.readLine() ?: throw AssertionError("Subprocess exited before JSON output:\n${seen.joinToString("\n")}")
            val trimmed = line.trim()
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
                return JSONObject(trimmed)
            }
            seen.add(line)
        }
    }

    private fun runPythonInline(script: String): String {
        val proc = ProcessBuilder("python3", "-c", script)
            .directory(pcSuiteDir)
            .redirectErrorStream(true)
            .start()
        val out = proc.inputStream.bufferedReader(Charsets.UTF_8).readText()
        val code = proc.waitFor()
        if (code != 0) throw AssertionError("Python script failed ($code):\n$out")
        return out.trim()
    }

    @Test
    fun `1 standalone OOXML DOCX XLSX PPTX round-trip and Python cross-read`() {
        val dir = Files.createTempDirectory("mo_ooxml_test").toFile()
        try {
            // 1. Android creates .docx, .xlsx, .pptx -> Python python-docx / openpyxl / python-pptx reads them!
            val docxBytes = OoxmlEngine.writeDocx(
                JSONArray()
                    .put(JSONObject().put("type", "p").put("heading", 1).put("text", "Android Executive Report"))
                    .put(JSONObject().put("type", "p").put("rtl", true).put("text", "سلام پاکستان")),
                "Android Report"
            )
            val docxFile = File(dir, "from_android.docx").apply { writeBytes(docxBytes) }

            val xlsxBytes = OoxmlEngine.writeXlsx(
                JSONArray().put(
                    JSONObject().put("name", "Sales").put(
                        "cells",
                        JSONObject()
                            .put("A1", JSONObject().put("raw", "Q1").put("value", "Q1"))
                            .put("B1", JSONObject().put("raw", "1500").put("value", "1500"))
                            .put("B2", JSONObject().put("raw", "2500").put("value", "2500"))
                            .put("B3", JSONObject().put("raw", "=SUM(B1:B2)").put("value", "4000"))
                    )
                )
            )
            val xlsxFile = File(dir, "from_android.xlsx").apply { writeBytes(xlsxBytes) }

            val pptxBytes = OoxmlEngine.writePptx(
                JSONArray().put(
                    JSONObject()
                        .put("title", "Mobile Roadmap")
                        .put("bullets", JSONArray().put("Offline First").put("Instant PC Sync"))
                        .put("notes", "Speaker note test")
                )
            )
            val pptxFile = File(dir, "from_android.pptx").apply { writeBytes(pptxBytes) }

            // Verify Kotlin reads its own files AND repairs archives
            assertTrue(OoxmlEngine.readDocx(docxBytes).getString("plainText").contains("سلام پاکستان"))
            assertEquals("Sales", OoxmlEngine.readXlsx(xlsxBytes).getJSONArray("sheets").getJSONObject(0).getString("name"))
            assertEquals("Mobile Roadmap", OoxmlEngine.readPptx(pptxBytes).getJSONArray("slides").getJSONObject(0).getString("title"))
            val repaired = File(dir, "repaired.docx")
            assertTrue(OoxmlEngine.repairOoxmlArchive(docxBytes, repaired).getBoolean("ok"))

            // Verify Python's python-docx, openpyxl, and python-pptx open every Android file cleanly AND create PC files for Android!
            val pyCheck = runPythonInline(
                """
                import docx, openpyxl, pptx, json
                d = docx.Document(r"${docxFile.absolutePath}")
                wb = openpyxl.load_workbook(r"${xlsxFile.absolutePath}")
                prs = pptx.Presentation(r"${pptxFile.absolutePath}")

                # Also create PC files so Kotlin reads PC-authored files!
                pc_doc = docx.Document()
                pc_doc.add_heading("Created on Windows PC", level=1)
                pc_doc.add_paragraph("Edited seamlessly on Android!")
                pc_doc.save(r"${File(dir, "from_pc.docx").absolutePath}")

                pc_wb = openpyxl.Workbook()
                ws = pc_wb.active
                ws.title = "PCData"
                ws["A1"] = "Revenue"
                ws["B1"] = 98765
                pc_wb.save(r"${File(dir, "from_pc.xlsx").absolutePath}")

                pc_prs = pptx.Presentation()
                sl = pc_prs.slides.add_slide(pc_prs.slide_layouts[1])
                sl.shapes.title.text = "PC Slide Deck"
                sl.placeholders[1].text = "Synced over Wi-Fi"
                pc_prs.save(r"${File(dir, "from_pc.pptx").absolutePath}")

                print(json.dumps({
                    "paras": [p.text for p in d.paragraphs],
                    "formula": wb["Sales"]["B3"].value,
                    "slide_title": prs.slides[0].shapes.title.text
                }))
                """.trimIndent()
            )
            val pyJson = JSONObject(pyCheck.lines().last())
            assertEquals("=SUM(B1:B2)", pyJson.getString("formula"))
            assertEquals("Mobile Roadmap", pyJson.getString("slide_title"))

            // Now read PC-created DOCX, XLSX, PPTX in Kotlin OoxmlEngine!
            val readPcDocx = OoxmlEngine.readDocx(File(dir, "from_pc.docx").readBytes())
            assertTrue(readPcDocx.getString("plainText").contains("Edited seamlessly on Android!"))

            val readPcXlsx = OoxmlEngine.readXlsx(File(dir, "from_pc.xlsx").readBytes())
            val pcCells = readPcXlsx.getJSONArray("sheets").getJSONObject(0).getJSONObject("cells")
            assertEquals("98765", pcCells.getJSONObject("B1").getString("value"))

            val readPcPptx = OoxmlEngine.readPptx(File(dir, "from_pc.pptx").readBytes())
            assertEquals("PC Slide Deck", readPcPptx.getJSONArray("slides").getJSONObject(0).getString("title"))
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `2 standalone PDF Studio QR Codec and BM25 AI Retriever`() {
        val dir = Files.createTempDirectory("mo_pdf_qr_test").toFile()
        try {
            val p1 = File(dir, "doc1.pdf")
            val p2 = File(dir, "doc2.pdf")
            PdfStudioEngine.writePlainDocumentPdf("Quarterly Financials", listOf("Total revenue reached PKR 14.5M in Q3."), p1)
            PdfStudioEngine.writePlainDocumentPdf("Appendix", listOf("Audit verified by Lahore team."), p2)

            val merged = File(dir, "merged.pdf")
            assertEquals(2, PdfStudioEngine.mergePdfs(listOf(p1, p2), merged).getInt("pages"))

            val numbered = File(dir, "numbered.pdf")
            PdfStudioEngine.addPageNumbersPdf(merged, "bottom-center", "Page {n} of {total}", 1, numbered)

            val stamped = File(dir, "stamped.pdf")
            PdfStudioEngine.stampPdf(numbered, "APPROVED", true, "#16A34A", stamped)

            val watermarked = File(dir, "watermarked.pdf")
            PdfStudioEngine.watermarkPdf(stamped, "CONFIDENTIAL", 0.25f, 40f, watermarked)

            val locked = File(dir, "locked.pdf")
            PdfStudioEngine.protectPdf(watermarked, "userpass", "ownerpass", locked)
            val unlocked = File(dir, "unlocked.pdf")
            PdfStudioEngine.unlockPdf(locked, "userpass", unlocked)

            val info = PdfStudioEngine.inspectPdf(unlocked)
            assertEquals(2, info.getInt("pageCount"))
            val pages = (0 until info.getJSONArray("pagesText").length()).map { info.getJSONArray("pagesText").getString(it) }
            val topPages = MobileAiClient.retrieveTopPdfPagesBm25(pages, "Audit Lahore team", topK = 1)
            assertEquals(2, topPages.first().first)

            // QR round-trip
            val qrUri = "myoffice://pair?url=http%3A%2F%2F192.168.1.55%3A41880&pin=849201&device=OfficePC"
            val pixels = QrCodec.encodeToArgbPixels(qrUri, 260)
            assertEquals(qrUri, QrCodec.decodeFromArgbPixels(pixels, 260, 260))
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `3 bit-exact MOVAULT1 and MOVAULTK encryption interoperability between Python PC and Android Kotlin`() {
        val dir = Files.createTempDirectory("mo_vault_interop").toFile()
        try {
            val androidStore = VaultCrypto.DeviceKeyStore(File(dir, "android_sec"), "Samsung Galaxy S25")
            val androidVault = File(dir, "from_android.movault")
            val androidKit = File(dir, "android.movaultkey")
            androidVault.writeBytes(
                VaultCrypto.encryptBytes(
                    "Top Secret Merger Contract 2026 — اردو ٹیسٹ".toByteArray(Charsets.UTF_8),
                    originalName = "Merger_Contract.docx",
                    store = androidStore,
                    passphrase = "correct-horse-battery"
                )
            )
            androidKit.writeBytes(androidStore.exportRecoveryKit("kit-pass-99"))

            val pcVault = File(dir, "from_pc.movault")
            val pcKit = File(dir, "pc.movaultkey")

            // Python PC imports Android's .movaultkey, decrypts Android's .movault (both via device key AND via passphrase),
            // AND encrypts its own PC .movault + .movaultkey for Android to unlock!
            val pyOut = runPythonInline(
                """
                import os, json
                os.environ["MYOFFICE_CONFIG_DIR"] = r"${File(dir, "pc_cfg").absolutePath}"
                from app.services import vault

                # 1. Decrypt Android .movault via passphrase BEFORE importing key
                raw_pw, hdr_pw = vault.decrypt_bytes(open(r"${androidVault.absolutePath}", "rb").read(), passphrase="correct-horse-battery")
                assert raw_pw.decode("utf-8").startswith("Top Secret Merger Contract 2026")

                # 2. Import Android .movaultkey & decrypt WITHOUT passphrase using device key wrap!
                ks = vault.keystore()
                imported = ks.import_recovery_kit(r"${androidKit.absolutePath}", "kit-pass-99")
                raw_dev, _ = vault.decrypt_bytes(open(r"${androidVault.absolutePath}", "rb").read(), store=ks)
                assert raw_dev == raw_pw

                # 3. Create PC .movault and export PC .movaultkey
                blob = vault.encrypt_bytes(b"Windows 11 Router USB Encrypted Backup Payload!", original_name="RouterBackup.xlsx", store=ks, passphrase="pc-pass-42")
                open(r"${pcVault.absolutePath}", "wb").write(blob)
                ks.export_recovery_kit(r"${pcKit.absolutePath}", "pc-kit-pw")
                print(json.dumps({"ok": True, "imported": imported}))
                """.trimIndent()
            )
            assertTrue(JSONObject(pyOut.lines().last()).getBoolean("ok"))

            // Now Android decrypts PC's .movault first via passphrase, then imports PC's .movaultkey and decrypts via authorized key!
            val viaPass = VaultCrypto.decryptBytes(pcVault.readBytes(), androidStore, passphrase = "pc-pass-42")
            assertEquals("RouterBackup.xlsx", viaPass.originalName)
            assertEquals("Windows 11 Router USB Encrypted Backup Payload!", String(viaPass.bytes, Charsets.UTF_8))

            androidStore.importRecoveryKit(pcKit.readBytes(), "pc-kit-pw")
            val viaImportedKey = VaultCrypto.decryptBytes(pcVault.readBytes(), androidStore, passphrase = "")
            assertEquals("Windows 11 Router USB Encrypted Backup Payload!", String(viaImportedKey.bytes, Charsets.UTF_8))
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `4 live two-way Wi-Fi sync between Python PC MobileSyncServer and Android PcLanPeerBackend AND Phone Host mode`() {
        val dir = Files.createTempDirectory("mo_live_sync").toFile()
        val pcDir = File(dir, "pc_workspace").apply { mkdirs() }
        val phoneDir = File(dir, "phone_workspace").apply { mkdirs() }
        File(pcDir, "PC_Budget.txt").writeText("Created on Windows PC v1")
        File(phoneDir, "Phone_Note.txt").writeText("Created on Android Phone v1")

        // Spawn live Python MobileSyncServer
        val pyServer = ProcessBuilder(
            "python3", "-u", "-c",
            """
            import os, json, time, sys
            os.environ["MYOFFICE_CONFIG_DIR"] = r"${File(dir, "pc_sync_cfg").absolutePath}"
            from app.services.mobile_sync import MobileSyncServer
            srv = MobileSyncServer(r"${pcDir.absolutePath}", port=0, enable_udp_discovery=True)
            port = srv.start()
            print(json.dumps({"port": port, "pin": srv.pin, "uri": srv.pair_uri("127.0.0.1")}), flush=True)
            for line in sys.stdin:
                if line.strip() == "STOP":
                    break
            srv.stop()
            """.trimIndent()
        ).directory(pcSuiteDir).redirectErrorStream(true).start()

        try {
            val reader = BufferedReader(InputStreamReader(pyServer.inputStream))
            val boot = readNextJsonLine(reader)
            val qrUri = boot.getString("uri")

            // Test UDP LAN auto-discovery finds the running Python PC server!
            val discovered = LanNetworkUtils.discoverPeersOnLan(timeoutMs = 700)
            assertTrue("Expected UDP discovery to find running PC server", discovered.length() >= 1)

            // Pair & 3-way sync Android <-> Python PC using scanned QR URI!
            val androidKeys = VaultCrypto.DeviceKeyStore(File(dir, "ak"), "Pixel 9 Pro")
            val peer = PcLanPeerBackend(qrUri, deviceName = "Pixel 9 Pro", keyStore = androidKeys)
            val rep1 = SyncEngineMobile(phoneDir, peer).syncOnce()
            assertEquals(1, rep1.downloaded.size)
            assertEquals(1, rep1.uploaded.size)
            assertEquals("Created on Windows PC v1", File(phoneDir, "PC_Budget.txt").readText())
            assertEquals("Created on Android Phone v1", File(pcDir, "Phone_Note.txt").readText())

            // Modify on Phone -> re-sync -> verify PC gets updated version automatically!
            Thread.sleep(20)
            File(phoneDir, "PC_Budget.txt").apply {
                writeText("Edited on Android Phone v2!")
                setLastModified(System.currentTimeMillis() + 5_000)
            }
            val rep2 = SyncEngineMobile(phoneDir, peer).syncOnce()
            assertTrue(rep2.uploaded.contains("PC_Budget.txt"))
            assertEquals("Edited on Android Phone v2!", File(pcDir, "PC_Budget.txt").readText())

            // Now reverse roles: Start Android's MobileSyncHostServer (Hotspot Host Mode) and have Python PC MobilePeerBackend sync with the Phone!
            val phoneHostDir = File(dir, "phone_hotspot_root").apply { mkdirs() }
            File(phoneHostDir, "Hotspot_Scan.pdf").writeBytes("PDF-FROM-PHONE-HOTSPOT".toByteArray())
            val phoneHost = MobileSyncHostServer(phoneHostDir, requestedPort = 0, keyStore = androidKeys, enableUdpDiscovery = false)
            phoneHost.start()
            try {
                val pcOut = runPythonInline(
                    """
                    import json
                    from app.services.mobile_sync import MobilePeerBackend
                    from app.services.cloud_sync import SyncEngine
                    b = MobilePeerBackend(r"${phoneHost.pairUri("127.0.0.1")}", device_name="Windows-Laptop")
                    rep = SyncEngine(r"${File(dir, "pc_from_hotspot").apply { mkdirs() }.absolutePath}", b).sync_once()
                    print(json.dumps({"down": rep.downloaded, "ok": True}))
                    """.trimIndent()
                )
                val hotJson = JSONObject(pcOut.lines().last())
                assertEquals("Hotspot_Scan.pdf", hotJson.getJSONArray("down").getString(0))
            } finally {
                phoneHost.stop()
            }
        } finally {
            runCatching { pyServer.outputStream.write("STOP\n".toByteArray()); pyServer.outputStream.flush() }
            pyServer.destroy()
            dir.deleteRecursively()
        }
    }

    @Test
    fun `5 live two-way Gmail Cloud Sync wire compatibility between Python PC GmailBackend and Android GmailImapBackend`() {
        val dir = Files.createTempDirectory("mo_gmail_wire_test").toFile()
        val pcSyncDir = File(dir, "pc_gmail").apply { mkdirs() }
        val androidSyncDir = File(dir, "android_gmail").apply { mkdirs() }
        File(pcSyncDir, "Contract_v1.txt").writeText("Uploaded to Gmail from Windows PC!")
        File(androidSyncDir, "MobileFieldReport.txt").writeText("Uploaded to Gmail from Android Phone!")

        // Start a real TLS IMAP4rev1 server speaking RFC 3501 (`LOGIN`, `CREATE`, `SELECT`, `UID SEARCH`, `UID FETCH`, `APPEND`, `UID STORE`, `EXPUNGE`)
        val imapProc = ProcessBuilder(
            "python3", "-u", "-c",
            """
            import socket, ssl, subprocess, tempfile, os, re, json, sys, threading

            cert_dir = tempfile.mkdtemp()
            cert_pem = os.path.join(cert_dir, "cert.pem")
            key_pem = os.path.join(cert_dir, "key.pem")
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", key_pem, "-out", cert_pem, "-days", "1", "-subj", "/CN=localhost"
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            mailboxes = {}
            uid_seq = [100]
            lock = threading.Lock()

            def handle(conn):
                f = conn.makefile("rwb", buffering=0)
                f.write(b"* OK [CAPABILITY IMAP4rev1] MockGmail Ready\r\n")
                cur_box = "INBOX"
                while True:
                    line = f.readline()
                    if not line:
                        break
                    s = line.decode("latin1").rstrip("\r\n")
                    parts = s.split(" ", 2)
                    tag, cmd = parts[0], parts[1].upper()
                    arg = parts[2] if len(parts) > 2 else ""
                    if cmd == "CAPABILITY":
                        f.write(b"* CAPABILITY IMAP4rev1\r\n" + f"{tag} OK CAPABILITY\r\n".encode())
                    elif cmd == "LOGIN":
                        f.write(f"{tag} OK Logged in\r\n".encode())
                    elif cmd == "CREATE":
                        box = arg.strip('"')
                        with lock:
                            mailboxes.setdefault(box, [])
                        f.write(f"{tag} OK Created\r\n".encode())
                    elif cmd == "SELECT":
                        cur_box = arg.strip('"')
                        with lock:
                            msgs = mailboxes.setdefault(cur_box, [])
                            n = len(msgs)
                        f.write(f"* {n} EXISTS\r\n{tag} OK [READ-WRITE] Selected\r\n".encode())
                    elif cmd == "UID":
                        sub, subarg = arg.split(" ", 1)
                        sub = sub.upper()
                        if sub == "SEARCH":
                            with lock:
                                uids = [str(m["uid"]) for m in mailboxes.get(cur_box, []) if not m["deleted"]]
                            f.write((f"* SEARCH {' '.join(uids)}".rstrip() + "\r\n").encode())
                            f.write(f"{tag} OK SEARCH done\r\n".encode())
                        elif sub == "FETCH":
                            uid_str, spec = subarg.split(" ", 1)
                            target_set = set(uid_str.split(","))
                            with lock:
                                matches = [m for m in mailboxes.get(cur_box, []) if str(m["uid"]) in target_set]
                            for idx, m in enumerate(matches, 1):
                                raw = m["raw"]
                                if "HEADER.FIELDS" in spec.upper():
                                    payload = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                                    f.write(f"* {idx} FETCH (UID {m['uid']} BODY[HEADER.FIELDS (SUBJECT)] {{{len(payload)}}}\r\n".encode())
                                    f.write(payload + b")\r\n")
                                else:
                                    f.write(f"* {idx} FETCH (UID {m['uid']} BODY[] {{{len(raw)}}}\r\n".encode())
                                    f.write(raw + b")\r\n")
                            f.write(f"{tag} OK FETCH done\r\n".encode())
                        elif sub == "STORE":
                            uid_str = subarg.split(" ", 1)[0]
                            target_set = set(uid_str.split(","))
                            with lock:
                                for m in mailboxes.get(cur_box, []):
                                    if str(m["uid"]) in target_set:
                                        m["deleted"] = True
                            f.write(f"{tag} OK STORE done\r\n".encode())
                    elif cmd == "EXPUNGE":
                        with lock:
                            mailboxes[cur_box] = [m for m in mailboxes.get(cur_box, []) if not m["deleted"]]
                        f.write(f"{tag} OK EXPUNGE done\r\n".encode())
                    elif cmd == "APPEND":
                        m = re.search(r'"([^"]+)".*\{(\d+)\}$', arg)
                        box, nbytes = m.group(1), int(m.group(2))
                        f.write(b"+ Ready for literal\r\n")
                        buf = b""
                        while len(buf) < nbytes:
                            buf += f.read(nbytes - len(buf))
                        f.readline()  # trailing CRLF
                        with lock:
                            uid_seq[0] += 1
                            uid = uid_seq[0]
                            mailboxes.setdefault(box, []).append({"uid": uid, "raw": buf, "deleted": False})
                        f.write(f"{tag} OK [APPENDUID 1 {uid}] APPEND completed\r\n".encode())
                    elif cmd == "LOGOUT":
                        f.write(f"* BYE Logging out\r\n{tag} OK LOGOUT\r\n".encode())
                        break
                    else:
                        f.write(f"{tag} OK {cmd}\r\n".encode())
                conn.close()

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_pem, key_pem)
            raw_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_srv.bind(("127.0.0.1", 0))
            raw_srv.listen(16)
            port = raw_srv.getsockname()[1]

            def accept_loop():
                while True:
                    try:
                        c, _ = raw_srv.accept()
                        sc = ctx.wrap_socket(c, server_side=True)
                        threading.Thread(target=handle, args=(sc,), daemon=True).start()
                    except Exception:
                        break

            threading.Thread(target=accept_loop, daemon=True).start()

            # Sync #1 from Python PC GmailBackend -> uploads Contract_v1.txt
            import ssl as stdssl
            from app.services import gmail_storage
            from app.services.cloud_sync import SyncEngine
            pc_backend = gmail_storage.GmailBackend(
                "user@gmail.com",
                "abcd efgh ijkl mnop",
                imap_host="127.0.0.1",
                imap_port=port,
                ssl_context=stdssl._create_unverified_context(),
            )
            SyncEngine(r"${pcSyncDir.absolutePath}", pc_backend).sync_once()

            print(json.dumps({"port": port}), flush=True)
            for l in sys.stdin:
                if l.strip() == "SYNC_PC_AGAIN":
                    rep = SyncEngine(r"${pcSyncDir.absolutePath}", pc_backend).sync_once()
                    print(json.dumps({"pc_down": rep.downloaded}), flush=True)
                elif l.strip() == "STOP":
                    break
            raw_srv.close()
            """.trimIndent()
        ).directory(pcSuiteDir).redirectErrorStream(true).start()

        try {
            val reader = BufferedReader(InputStreamReader(imapProc.inputStream))
            val port = readNextJsonLine(reader).getInt("port")

            // Sync #2 from Android Kotlin GmailImapBackend -> downloads Contract_v1.txt from PC AND uploads MobileFieldReport.txt to Gmail!
            val trustAll = javax.net.ssl.SSLContext.getInstance("TLS").apply {
                init(null, arrayOf(object : javax.net.ssl.X509TrustManager {
                    override fun checkClientTrusted(c: Array<java.security.cert.X509Certificate>, a: String) {}
                    override fun checkServerTrusted(c: Array<java.security.cert.X509Certificate>, a: String) {}
                    override fun getAcceptedIssuers(): Array<java.security.cert.X509Certificate> = emptyArray()
                }), java.security.SecureRandom())
            }
            val androidGmail = GmailImapBackend(
                rawEmail = "user@gmail.com",
                rawAppPassword = "abcd efgh ijkl mnop",
                host = "127.0.0.1",
                port = port,
                customSslContext = trustAll
            )
            val androidRep = SyncEngineMobile(androidSyncDir, androidGmail).syncOnce()
            assertEquals(listOf("Contract_v1.txt"), androidRep.downloaded)
            assertEquals(listOf("MobileFieldReport.txt"), androidRep.uploaded)
            assertEquals("Uploaded to Gmail from Windows PC!", File(androidSyncDir, "Contract_v1.txt").readText())

            // Sync #3 back on Python PC -> proves PC downloads MobileFieldReport.txt uploaded by Android!
            imapProc.outputStream.write("SYNC_PC_AGAIN\n".toByteArray())
            imapProc.outputStream.flush()
            val pcSecond = readNextJsonLine(reader)
            assertEquals("MobileFieldReport.txt", pcSecond.getJSONArray("pc_down").getString(0))
            assertEquals("Uploaded to Gmail from Android Phone!", File(pcSyncDir, "MobileFieldReport.txt").readText())
        } finally {
            runCatching { imapProc.outputStream.write("STOP\n".toByteArray()); imapProc.outputStream.flush() }
            imapProc.destroy()
            dir.deleteRecursively()
        }
    }

    @Test(timeout = 45_000)
    fun `6 live real-time collaboration and Team Chat between Python PC CollabRoomServer and Android MobileCollabManager`() {
        val dir = Files.createTempDirectory("mo_live_collab").toFile()
        val pcDoc = File(dir, "SharedSpec.docx").apply {
            writeBytes(OoxmlEngine.writeDocx(JSONArray().put(JSONObject().put("type", "p").put("text", "Initial PC text")), "Spec"))
        }

        val serverProc = ProcessBuilder(
            "python3", "-u", "-c",
            """
            import json, sys, time, uuid, threading
            import pycrdt
            from pycrdt import Array, Doc, Text
            import websockets.sync.client as ws_client
            from app.collab.server import CollabServer

            srv = CollabServer(host="127.0.0.1", port=0, persist=False)
            srv.start()
            srv.register_shared_file("SpecRoom", file_path=r"${pcDoc.absolutePath}", kind="writer")
            ws_url = f"ws://127.0.0.1:{srv.port}/SpecRoom"

            # Real Desktop binary pycrdt Yjs WebSocket peer connected simultaneously!
            desk_doc = Doc()
            desk_text = desk_doc.get("content", type=Text)
            desk_chat = desk_doc.get("chat", type=Array)
            desk_ws = ws_client.connect(ws_url)
            desk_ws.send(pycrdt.create_sync_message(desk_doc))

            def pump_desktop():
                while True:
                    try:
                        data = desk_ws.recv(timeout=0.5)
                        if data and data[0] == 0:
                            rep = pycrdt.handle_sync_message(data[1:], desk_doc)
                            if rep is not None:
                                desk_ws.send(rep)
                    except TimeoutError:
                        continue
                    except Exception:
                        break

            threading.Thread(target=pump_desktop, daemon=True).start()
            desk_doc.observe(lambda ev: desk_ws.send(pycrdt.create_update_message(ev.update)))
            time.sleep(0.2)
            print(json.dumps({"ws": ws_url}), flush=True)
            for l in sys.stdin:
                cmd = l.strip()
                if cmd == "PC_CHAT":
                    with desk_doc.transaction():
                        desk_chat.append({
                            "id": str(uuid.uuid4()),
                            "sender": "Desktop Lead",
                            "color": "#2563EB",
                            "text": "Welcome Android teammate!",
                            "ts": time.time(),
                        })
                    print(json.dumps({"ok": True, "desk_text": str(desk_text)}), flush=True)
                elif cmd == "STOP":
                    break
            desk_ws.close()
            srv.stop()
            """.trimIndent()
        ).directory(pcSuiteDir).redirectErrorStream(true).start()

        try {
            val reader = BufferedReader(InputStreamReader(serverProc.inputStream))
            val wsUrl = readNextJsonLine(reader).getString("ws")

            val events = CopyOnWriteArrayList<JSONObject>()
            val gotPcChatLatch = CountDownLatch(1)
            val gotAndroidEchoLatch = CountDownLatch(1)
            val manager = MobileCollabManager(File(dir, "mobile_collab")) { _, evt ->
                events.add(evt)
                if (evt.optString("type") == "chat_push") {
                    val msg = evt.optJSONObject("message")
                    if (msg?.optString("sender") == "Desktop Lead") gotPcChatLatch.countDown()
                    if (msg?.optString("sender") == "Android Field") gotAndroidEchoLatch.countDown()
                }
            }

            try {
                val joined = manager.joinRoom(wsUrl, userName = "Android Field")
                assertEquals("SpecRoom", joined.getString("room"))
                assertTrue(File(joined.getString("localPath")).isFile)

                // Send chat from Android -> live CollabServer
                assertTrue(manager.sendChat("SpecRoom", "Loud and clear from Android!"))
                assertTrue(gotAndroidEchoLatch.await(5, TimeUnit.SECONDS))

                // Push live Writer update from Android -> Python pycrdt Y.Doc!
                assertTrue(manager.pushWriterText("SpecRoom", "Updated live from Android phone!"))
                Thread.sleep(250)

                // Send chat from Python PC -> Android WebSocket + verify Desktop Y.Text received Android's edit!
                serverProc.outputStream.write("PC_CHAT\n".toByteArray())
                serverProc.outputStream.flush()
                val pcState = readNextJsonLine(reader)
                assertEquals("Updated live from Android phone!", pcState.getString("desk_text"))
                assertTrue("Expected live chat from Desktop Lead to arrive on Android", gotPcChatLatch.await(5, TimeUnit.SECONDS))
            } finally {
                manager.shutdownAll()
            }
        } finally {
            runCatching { serverProc.outputStream.write("STOP\n".toByteArray()); serverProc.outputStream.flush() }
            serverProc.destroy()
            dir.deleteRecursively()
        }
    }

    @Test
    fun `7 live signed GitHub OTA hotpatch and boot watchdog rollback between Python publisher and Android OtaUpdateManager`() {
        val dir = Files.createTempDirectory("mo_ota_live_test").toFile()
        val pyServer = ProcessBuilder(
            "python3", "-u", "-c",
            """
import functools, http.server, json, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(r"${pcSuiteDir.parentFile.absolutePath}") / "ota-system"))
import publish_release

serve_root = Path(r"${dir.absolutePath}") / "repo"
ota_dir = serve_root / "ota"
ota_dir.mkdir(parents=True, exist_ok=True)
priv, pub = publish_release.ensure_signing_keypair(
    priv_path=Path(r"${dir.absolutePath}") / "test_key.pem",
    pub_b64_path=Path(r"${dir.absolutePath}") / "test_pub.b64",
)

ui_idx = serve_root / "index.html"
ui_idx.write_text("<!doctype html><html><body>OTA_LIVE_9_8_0</body></html>", encoding="utf-8")
sha, sz = publish_release.build_deterministic_zip([(ui_idx, "index.html")], ota_dir / "android-ui.zip")

manifest = {
    "schema": "myoffice-ota-v2",
    "version": "9.8.0",
    "version_code": 90800,
    "date": "2026-10-23T12:30:00Z",
    "notes": "Instant cloud UI hotpatch without APK rebuild",
    "mandatory": False,
    "min_version": "1.0.0",
    "android": {
        "version": "9.8.0",
        "version_code": 90800,
        "min_native_code": 10600,
        "bundle_url": "./android-ui.zip",
        "bundle_sha256": sha,
        "bundle_size": sz,
    },
}
manifest["signature"] = publish_release.sign_manifest(manifest, priv)
(ota_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(serve_root))
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
print(json.dumps({"port": httpd.server_address[1], "pub": pub}), flush=True)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
sys.stdin.readline()
httpd.shutdown()
            """.trimIndent()
        ).redirectErrorStream(true).start()

        try {
            val reader = BufferedReader(InputStreamReader(pyServer.inputStream))
            val bootJson = readNextJsonLine(reader)
            val port = bootJson.getInt("port")
            val testPub = bootJson.getString("pub")
            val filesDir = File(dir, "android_files").apply { mkdirs() }
            val cacheDir = File(dir, "android_cache").apply { mkdirs() }

            // 1. Live check + ECDSA P-256 verification against Python-signed manifest
            val manifestUrl = "http://127.0.0.1:$port/ota/manifest.json"
            val check = OtaUpdateManager.checkUpdate(filesDir, manifestUrl, pubKeyB64 = testPub)
            assertTrue(check.getBoolean("available"))
            assertTrue(check.getBoolean("signed"))
            assertEquals("hotpatch", check.getString("mode"))
            assertEquals("9.8.0", check.getString("latestVersion"))

            // 2. Download, SHA-256 verify, and atomically stage live bundle
            val staged = OtaUpdateManager.downloadAndStageUpdate(filesDir, cacheDir, check)
            assertEquals("9.8.0", OtaUpdateManager.activeVersionName(filesDir))
            val (entryUrl, armed) = OtaUpdateManager.resolveStartupEntryUrl(filesDir)
            assertTrue(armed)
            assertTrue(File(java.net.URI(entryUrl)).readText().contains("OTA_LIVE_9_8_0"))
            OtaUpdateManager.markBootHealthy(filesDir)

            // 3. Verify 1-byte manifest tamper is cryptographically rejected
            val rawManifest = JSONObject(File(dir, "repo/ota/manifest.json").readText())
            rawManifest.put("notes", "Tampered attacker payload")
            assertEquals(false, OtaUpdateManager.verifyManifestSignature(rawManifest, testPub))

            // 4. Verify automatic Boot Watchdog rollback on repeated crash
            OtaUpdateManager.writeState(
                filesDir,
                JSONObject().put("activeVersion", "9.8.0").put("bootInProgress", true).put("bootAttempts", 2)
            )
            val (fallbackUrl, fallbackArmed) = OtaUpdateManager.resolveStartupEntryUrl(filesDir)
            assertEquals("file:///android_asset/ui/index.html", fallbackUrl)
            assertEquals(false, fallbackArmed)
        } finally {
            runCatching { pyServer.outputStream.write("STOP\n".toByteArray()); pyServer.outputStream.flush() }
            pyServer.destroy()
            dir.deleteRecursively()
        }
    }

    @Test
    fun `8 live Windows CollabSession to Android MobileCollabManager unsaved DOCX shared file download and two-way PC-to-Mobile AND Mobile-to-PC chat`() {
        val dir = Files.createTempDirectory("mo_live_collab_fix").toFile()
        val receivedMobileEvents = java.util.Collections.synchronizedList(mutableListOf<JSONObject>())

        // Spawn REAL Qt/PySide6 Windows CollabSession + CollabServer hosting an unsaved WriterDocument!
        val pyHost = ProcessBuilder(
            "python3", "-u", "-c",
            """
import json, os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["MYOFFICE_CONFIG_DIR"] = r"${File(dir, "pc_cfg").absolutePath}"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.writer.widget import WriterDocument
from app.collab.server import CollabServer
from app.collab.binding import CollabSession, extract_doc_snapshot_bytes

qapp = QApplication([])
srv = CollabServer("127.0.0.1", 0, persist=False)
srv.start()

doc = WriterDocument()
doc.editor.document().setPlainText("Live Contract Draft from Windows PC")
raw_bytes, fname, kind = extract_doc_snapshot_bytes(doc)
srv.register_shared_file("draft-room", file_path=None, filename=fname, kind=kind, raw_bytes=raw_bytes)

sess = CollabSession(doc, f"ws://127.0.0.1:{srv.port}", "draft-room", user="Abdullah PC", http_base=f"http://127.0.0.1:{srv.port}")
sess.publish_initial_file_snapshot()
sess.start()

# Wait until PC session is connected, then send an initial chat message BEFORE mobile joins!
deadline = time.time() + 4.0
while time.time() < deadline and not sess._synced:
    qapp.processEvents()
    time.sleep(0.03)

sess.send_chat("Salaam from Windows PC before join!")
for _ in range(10):
    qapp.processEvents()
    time.sleep(0.03)

qr_http_link = sess.share_links("127.0.0.1")["http"]
print(json.dumps({"qr_http": qr_http_link, "fname": fname}), flush=True)

for line in sys.stdin:
    cmd = line.strip()
    if cmd == "SEND_PC_MSG":
        sess.send_chat("Live reply from Windows PC to Mobile!")
        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.03)
        print(json.dumps({"pc_chat": [m["text"] for m in sess.chat_history()]}), flush=True)
    elif cmd == "CHECK_PC_CHAT":
        for _ in range(25):
            qapp.processEvents()
            time.sleep(0.03)
        print(json.dumps({"pc_chat": [m["text"] for m in sess.chat_history()]}), flush=True)
    elif cmd == "STOP":
        break

sess.stop()
srv.stop()
            """.trimIndent()
        ).directory(pcSuiteDir).redirectErrorStream(true).start()

        try {
            val reader = BufferedReader(InputStreamReader(pyHost.inputStream))
            val boot = readNextJsonLine(reader)
            val qrHttpLink = boot.getString("qr_http") // Exact QR URL format: http://127.0.0.1:<port>/f/draft-room

            val collabDir = File(dir, "Collaborations")
            val mgr = MobileCollabManager(collabDir) { _, evt -> receivedMobileEvents.add(evt) }

            // 1. Mobile joins using exact HTTP QR link (`http://127.0.0.1:<port>/f/draft-room`)
            val joinRes = mgr.joinRoom(qrHttpLink, userName = "Abdullah Mobile")
            assertEquals("draft-room", joinRes.getString("room"))
            assertTrue("Expected non-empty relPath with .docx extension", joinRes.getString("relPath").endsWith(".docx"))
            val downloadedDocx = File(joinRes.getString("localPath"))
            assertTrue("Shared DOCX file must exist and have non-zero size on Mobile", downloadedDocx.isFile && downloadedDocx.length() > 500)

            // Verify Mobile also opens the downloaded shared DOCX cleanly via OoxmlEngine!
            val openedDoc = OoxmlEngine.readDocx(downloadedDocx.readBytes())
            assertTrue(openedDoc.getString("plainText").contains("Live Contract Draft from Windows PC"))

            // 2. Verify initial PC chat message is already visible in joinRes.chat!
            val initialChats = (0 until joinRes.getJSONArray("chat").length())
                .map { joinRes.getJSONArray("chat").getJSONObject(it).getString("text") }
            assertTrue("Expected initial PC message on Mobile", initialChats.contains("Salaam from Windows PC before join!"))

            // 3. Mobile -> PC chat message
            assertTrue(mgr.sendChat("draft-room", "Salaam from Mobile to PC!"))
            pyHost.outputStream.write("CHECK_PC_CHAT\n".toByteArray())
            pyHost.outputStream.flush()
            val pcCheck = readNextJsonLine(reader).getJSONArray("pc_chat")
            val pcTexts = (0 until pcCheck.length()).map { pcCheck.getString(it) }
            assertTrue("PC must receive Mobile chat message", pcTexts.contains("Salaam from Mobile to PC!"))

            // 4. PC -> Mobile live chat message (THE EXACT BUG REPORTED BY USER!)
            pyHost.outputStream.write("SEND_PC_MSG\n".toByteArray())
            pyHost.outputStream.flush()
            readNextJsonLine(reader)

            // Wait up to 2.5s for live WebSocket push on Mobile
            val deadline = System.currentTimeMillis() + 2500
            var foundPcLiveReply = false
            while (System.currentTimeMillis() < deadline && !foundPcLiveReply) {
                foundPcLiveReply = synchronized(receivedMobileEvents) {
                    receivedMobileEvents.any {
                        it.optString("type") == "chat_push" &&
                            it.optJSONObject("message")?.optString("text") == "Live reply from Windows PC to Mobile!"
                    }
                }
                if (!foundPcLiveReply) Thread.sleep(40)
            }
            assertTrue("Mobile MUST receive live chat message sent from Windows PC!", foundPcLiveReply)

            mgr.shutdownAll()
        } finally {
            runCatching { pyHost.outputStream.write("STOP\n".toByteArray()); pyHost.outputStream.flush() }
            pyHost.destroy()
            dir.deleteRecursively()
        }
    }
}
