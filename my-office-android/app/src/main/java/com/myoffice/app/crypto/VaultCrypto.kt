package com.myoffice.app.crypto

import org.bouncycastle.crypto.digests.SHA256Digest
import org.bouncycastle.crypto.generators.HKDFBytesGenerator
import org.bouncycastle.crypto.generators.SCrypt
import org.bouncycastle.crypto.params.HKDFParameters
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.LocalDateTime
import java.time.temporal.ChronoUnit
import java.util.Base64
import java.util.zip.Deflater
import java.util.zip.Inflater
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

open class VaultException(message: String, cause: Throwable? = null) : RuntimeException(message, cause)
class VaultAccessDeniedException(message: String, cause: Throwable? = null) : VaultException(message, cause)
class VaultTamperedException(message: String, cause: Throwable? = null) : VaultException(message, cause)

data class VaultMetadata(
    val vaultFilename: String,
    val originalName: String,
    val originalPath: String,
    val createdAt: String,
    val originalSize: Long,
    val encryptedSize: Long,
    val sha256: String,
    val keyId: String,
    val deviceLabel: String,
    val lockedToOtherDevice: Boolean
)

data class DecryptedVaultPayload(
    val bytes: ByteArray,
    val originalName: String,
    val originalPath: String,
    val createdAt: String,
    val sha256: String,
    val keyId: String,
    val deviceLabel: String
)

/**
 * 100% Bit-Exact Binary Interoperable implementation of MyOffice `MOVAULT1` & `MOVAULTK`
 * matching Desktop `app/services/vault.py`:
 *  - Magic: `MOVAULT1` + Big-Endian uint32 header length + UTF-8 JSON header + zlib-compressed AES-256-GCM payload
 *  - Device Wrap: HKDF-SHA256(masterKey, salt, "MOVAULT1-WRAP-DEVICE") -> AES-256-GCM(wrap_nonce, dek, AAD="MOVAULT1-WRAP-DEVICE")
 *  - Passphrase Wrap: Scrypt(passphrase, psalt, N=16384, r=8, p=1, dkLen=32) -> AES-256-GCM(pnonce, dek, AAD="MOVAULT1-WRAP-PASSPHRASE")
 *  - Recovery / Device Authorization Kit (`MOVAULTK`): seamless key sharing between paired PC & Android phone
 *  - Sealed at rest on Android via hardware-backed `AndroidKeyStore` (`AES/GCM/NoPadding`)
 */
object VaultCrypto {
    private val MAGIC = "MOVAULT1".toByteArray(Charsets.US_ASCII)
    private val KIT_MAGIC = "MOVAULTK".toByteArray(Charsets.US_ASCII)
    private val AAD_WRAP_DEV = "MOVAULT1-WRAP-DEVICE".toByteArray(Charsets.US_ASCII)
    private val AAD_WRAP_PASS = "MOVAULT1-WRAP-PASSPHRASE".toByteArray(Charsets.US_ASCII)
    private val AAD_META = "MOVAULT1-METADATA".toByteArray(Charsets.US_ASCII)
    private val AAD_DATA = "MOVAULT1-PAYLOAD".toByteArray(Charsets.US_ASCII)
    private const val SECRET_PREFIX = "enc1:"
    private const val ANDROID_KEYSTORE_ALIAS = "MyOfficeHardwareVaultMasterKey_v1"
    private val rng = SecureRandom()

    fun randomBytes(len: Int): ByteArray = ByteArray(len).also { rng.nextBytes(it) }

    fun sha256Hex(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).toHex()

    fun computeKeyId(masterKey: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        md.update("myoffice-vault-key-id-v1:".toByteArray(Charsets.US_ASCII))
        md.update(masterKey)
        return md.digest().toHex().substring(0, 16)
    }

    fun deriveHkdf(secret: ByteArray, salt: ByteArray, info: ByteArray): ByteArray {
        val gen = HKDFBytesGenerator(SHA256Digest())
        gen.init(HKDFParameters(secret, salt, info))
        val out = ByteArray(32)
        gen.generateBytes(out, 0, 32)
        return out
    }

    fun derivePassphraseKey(passphrase: String, salt: ByteArray): ByteArray {
        return SCrypt.generate(passphrase.toByteArray(Charsets.UTF_8), salt, 16384, 8, 1, 32)
    }

    fun aesGcmEncrypt(key: ByteArray, nonce: ByteArray, plain: ByteArray, aad: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(aad)
        return cipher.doFinal(plain)
    }

    fun aesGcmDecrypt(key: ByteArray, nonce: ByteArray, cipherTextWithTag: ByteArray, aad: ByteArray): ByteArray {
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
            cipher.updateAAD(aad)
            cipher.doFinal(cipherTextWithTag)
        } catch (e: Exception) {
            throw VaultAccessDeniedException("Cryptographic authentication failed or unauthorized device key.", e)
        }
    }

    private fun zlibCompress(input: ByteArray): ByteArray {
        val deflater = Deflater(6)
        deflater.setInput(input)
        deflater.finish()
        val out = ByteArrayOutputStream(input.size + 64)
        val buf = ByteArray(8192)
        while (!deflater.finished()) {
            val n = deflater.deflate(buf)
            if (n > 0) out.write(buf, 0, n)
        }
        deflater.end()
        return out.toByteArray()
    }

    private fun zlibDecompress(input: ByteArray): ByteArray {
        val inflater = Inflater()
        inflater.setInput(input)
        val out = ByteArrayOutputStream(input.size * 2 + 256)
        val buf = ByteArray(8192)
        while (!inflater.finished()) {
            val n = inflater.inflate(buf)
            if (n > 0) out.write(buf, 0, n)
            else if (inflater.needsInput() || inflater.needsDictionary()) break
        }
        inflater.end()
        return out.toByteArray()
    }

    fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    fun String.hexToBytes(): ByteArray {
        val clean = trim()
        require(clean.length % 2 == 0) { "Odd hex string length" }
        return ByteArray(clean.length / 2) { i ->
            clean.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
    }

    class DeviceKeyStore(
        private val storeDir: File,
        val deviceLabel: String = "MyOffice-Android",
        private val fallbackBindingSecret: ByteArray = "myoffice-android-local-seal".toByteArray(Charsets.UTF_8)
    ) {
        private val vaultFile = File(storeDir.apply { mkdirs() }, "android_device_vault.json")
        var masterKey: ByteArray
            private set
        private val extraKeys = LinkedHashMap<String, ByteArray>()

        val keyId: String get() = computeKeyId(masterKey)

        init {
            val loaded = loadExisting()
            if (loaded != null) {
                masterKey = loaded.first
                extraKeys.putAll(loaded.second)
            } else {
                masterKey = randomBytes(32)
                save()
            }
        }

        private fun androidKeyStoreKey(): SecretKey? {
            return try {
                val ks = KeyStore.getInstance("AndroidKeyStore")
                ks.load(null)
                val existing = ks.getKey(ANDROID_KEYSTORE_ALIAS, null) as? SecretKey
                if (existing != null) return existing
                val specClass = Class.forName("android.security.keystore.KeyGenParameterSpec\$Builder")
                val purposes = 1 or 2 // PURPOSE_ENCRYPT | PURPOSE_DECRYPT
                val builder = specClass.getConstructor(String::class.java, Int::class.javaPrimitiveType)
                    .newInstance(ANDROID_KEYSTORE_ALIAS, purposes)
                specClass.getMethod("setBlockModes", Array<String>::class.java)
                    .invoke(builder, arrayOf("GCM") as Any)
                specClass.getMethod("setEncryptionPaddings", Array<String>::class.java)
                    .invoke(builder, arrayOf("NoPadding") as Any)
                specClass.getMethod("setKeySize", Int::class.javaPrimitiveType)
                    .invoke(builder, 256)
                val spec = specClass.getMethod("build").invoke(builder)
                val kg = KeyGenerator.getInstance("AES", "AndroidKeyStore")
                kg.init(spec as java.security.spec.AlgorithmParameterSpec)
                kg.generateKey()
            } catch (_: Throwable) {
                null // Non-Android JVM test environment falls back cleanly to HKDF seal
            }
        }

        private fun sealInner(plain: ByteArray): JSONObject {
            val hwKey = androidKeyStoreKey()
            if (hwKey != null) {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.ENCRYPT_MODE, hwKey)
                val ct = cipher.doFinal(plain)
                return JSONObject()
                    .put("mode", "android_keystore")
                    .put("iv", cipher.iv.toHex())
                    .put("ct", ct.toHex())
            }
            val salt = randomBytes(16)
            val nonce = randomBytes(12)
            val k = deriveHkdf(fallbackBindingSecret, salt, "myoffice-keystore-seal".toByteArray())
            val ct = aesGcmEncrypt(k, nonce, plain, "keystore".toByteArray())
            return JSONObject()
                .put("mode", "hkdf")
                .put("salt", salt.toHex())
                .put("nonce", nonce.toHex())
                .put("ct", ct.toHex())
        }

        private fun unsealInner(obj: JSONObject): ByteArray {
            return if (obj.optString("mode") == "android_keystore") {
                val hwKey = androidKeyStoreKey()
                    ?: throw VaultAccessDeniedException("Android Hardware Keystore unavailable")
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(Cipher.DECRYPT_MODE, hwKey, GCMParameterSpec(128, obj.getString("iv").hexToBytes()))
                cipher.doFinal(obj.getString("ct").hexToBytes())
            } else {
                val salt = obj.getString("salt").hexToBytes()
                val nonce = obj.getString("nonce").hexToBytes()
                val ct = obj.getString("ct").hexToBytes()
                val k = deriveHkdf(fallbackBindingSecret, salt, "myoffice-keystore-seal".toByteArray())
                aesGcmDecrypt(k, nonce, ct, "keystore".toByteArray())
            }
        }

        private fun loadExisting(): Pair<ByteArray, Map<String, ByteArray>>? {
            if (!vaultFile.isFile) return null
            return try {
                val outer = JSONObject(vaultFile.readText(Charsets.UTF_8))
                val inner = JSONObject(String(unsealInner(outer.getJSONObject("sealed")), Charsets.UTF_8))
                val primary = inner.getString("primary").hexToBytes()
                val extras = LinkedHashMap<String, ByteArray>()
                val extraObj = inner.optJSONObject("extra")
                if (extraObj != null) {
                    for (k in extraObj.keys()) {
                        extras[k] = extraObj.getString(k).hexToBytes()
                    }
                }
                Pair(primary, extras)
            } catch (_: Exception) {
                null
            }
        }

        @Synchronized
        fun save() {
            val extraObj = JSONObject()
            for ((k, v) in extraKeys) extraObj.put(k, v.toHex())
            val inner = JSONObject()
                .put("primary", masterKey.toHex())
                .put("extra", extraObj)
                .toString()
                .toByteArray(Charsets.UTF_8)
            val outer = JSONObject()
                .put("version", 1)
                .put("key_id", keyId)
                .put("device_label", deviceLabel)
                .put("sealed", sealInner(inner))
            val tmp = File(storeDir, "android_device_vault.tmp")
            tmp.writeText(outer.toString(2), Charsets.UTF_8)
            tmp.renameTo(vaultFile)
        }

        fun keyFor(targetKeyId: String): ByteArray? =
            if (targetKeyId == keyId) masterKey else extraKeys[targetKeyId]

        fun knownKeyIds(): List<String> = listOf(keyId) + extraKeys.keys.sorted()

        @Synchronized
        fun authorizePeerMasterKeyHex(peerHex: String): String {
            val raw = peerHex.trim().hexToBytes()
            require(raw.size == 32) { "Expected 256-bit vault key (64 hex chars)" }
            val id = computeKeyId(raw)
            if (id != keyId) {
                extraKeys[id] = raw
                save()
            }
            return id
        }

        fun exportRecoveryKit(recoveryPassword: String): ByteArray {
            if (recoveryPassword.length < 6) {
                throw VaultException("Recovery password must be at least 6 characters.")
            }
            val salt = randomBytes(16)
            val nonce = randomBytes(12)
            val kek = derivePassphraseKey(recoveryPassword, salt)
            val keysJson = JSONObject().put(keyId, masterKey.toHex())
            for ((k, v) in extraKeys) keysJson.put(k, v.toHex())
            val body = JSONObject()
                .put("device_label", deviceLabel)
                .put("primary_id", keyId)
                .put("keys", keysJson)
                .put("exported_at", LocalDateTime.now().truncatedTo(ChronoUnit.SECONDS).toString())
                .toString()
                .toByteArray(Charsets.UTF_8)
            val ct = aesGcmEncrypt(kek, nonce, body, KIT_MAGIC)
            return KIT_MAGIC + salt + nonce + ct
        }

        @Synchronized
        fun importRecoveryKit(blob: ByteArray, recoveryPassword: String): List<String> {
            if (blob.size < 40 || !blob.sliceArray(0 until 8).contentEquals(KIT_MAGIC)) {
                throw VaultTamperedException("Not a valid MyOffice Vault Recovery Key (.movaultkey).")
            }
            val salt = blob.sliceArray(8 until 24)
            val nonce = blob.sliceArray(24 until 36)
            val ct = blob.sliceArray(36 until blob.size)
            val kek = derivePassphraseKey(recoveryPassword, salt)
            val plain = aesGcmDecrypt(kek, nonce, ct, KIT_MAGIC)
            val body = JSONObject(String(plain, Charsets.UTF_8))
            val keysObj = body.getJSONObject("keys")
            val added = mutableListOf<String>()
            for (k in keysObj.keys()) {
                val raw = keysObj.getString(k).hexToBytes()
                val realId = computeKeyId(raw)
                if (realId != keyId) {
                    extraKeys[realId] = raw
                    added.add(realId)
                }
            }
            save()
            return added
        }

        fun sealSecret(secret: String): String {
            if (secret.isEmpty() || secret.startsWith(SECRET_PREFIX)) return secret
            val salt = randomBytes(12)
            val nonce = randomBytes(12)
            val key = deriveHkdf(masterKey, salt, "myoffice-config-secret-v1".toByteArray())
            val ct = aesGcmEncrypt(key, nonce, secret.toByteArray(Charsets.UTF_8), "config-secret".toByteArray())
            return SECRET_PREFIX + Base64.getUrlEncoder().encodeToString(salt + nonce + ct)
        }

        fun unsealSecret(token: String): String {
            if (token.isEmpty() || !token.startsWith(SECRET_PREFIX)) return token
            return try {
                val raw = Base64.getUrlDecoder().decode(token.removePrefix(SECRET_PREFIX))
                val salt = raw.sliceArray(0 until 12)
                val nonce = raw.sliceArray(12 until 24)
                val ct = raw.sliceArray(24 until raw.size)
                val key = deriveHkdf(masterKey, salt, "myoffice-config-secret-v1".toByteArray())
                String(aesGcmDecrypt(key, nonce, ct, "config-secret".toByteArray()), Charsets.UTF_8)
            } catch (_: Exception) {
                ""
            }
        }
    }

    fun encryptBytes(
        data: ByteArray,
        originalName: String,
        originalPath: String = "",
        store: DeviceKeyStore,
        passphrase: String = ""
    ): ByteArray {
        val dek = randomBytes(32)
        val salt = randomBytes(16)
        val wrapNonce = randomBytes(12)
        val kek = deriveHkdf(store.masterKey, salt, AAD_WRAP_DEV)
        val wrappedDek = aesGcmEncrypt(kek, wrapNonce, dek, AAD_WRAP_DEV)

        val now = LocalDateTime.now().truncatedTo(ChronoUnit.SECONDS).toString()
        val sha = sha256Hex(data)
        val compressed = zlibCompress(data)
        val metaPlain = JSONObject()
            .put("original_name", originalName)
            .put("original_path", originalPath)
            .put("created_at", now)
            .put("size", data.size)
            .put("sha256", sha)
            .put("compressed", true)
            .toString()
            .toByteArray(Charsets.UTF_8)

        val metaNonce = randomBytes(12)
        val metaCt = aesGcmEncrypt(dek, metaNonce, metaPlain, AAD_META)
        val dataNonce = randomBytes(12)
        val dataCt = aesGcmEncrypt(dek, dataNonce, compressed, AAD_DATA)

        val outer = JSONObject()
            .put("v", 1)
            .put("key_id", store.keyId)
            .put("device_label", store.deviceLabel)
            .put("salt", salt.toHex())
            .put("wrap_nonce", wrapNonce.toHex())
            .put("wrapped_dek", wrappedDek.toHex())
            .put("meta_nonce", metaNonce.toHex())
            .put("meta_ct", metaCt.toHex())
            .put("data_nonce", dataNonce.toHex())

        if (passphrase.isNotEmpty()) {
            val psalt = randomBytes(16)
            val pnonce = randomBytes(12)
            val pkek = derivePassphraseKey(passphrase, psalt)
            outer.put("pass_salt", psalt.toHex())
            outer.put("pass_nonce", pnonce.toHex())
            outer.put("pass_wrapped_dek", aesGcmEncrypt(pkek, pnonce, dek, AAD_WRAP_PASS).toHex())
        }

        val headerBytes = outer.toString().toByteArray(Charsets.UTF_8)
        val lenBuf = ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN).putInt(headerBytes.size).array()
        return MAGIC + lenBuf + headerBytes + dataCt
    }

    private fun parseHeader(blob: ByteArray): Pair<JSONObject, Int> {
        if (blob.size < 12 || !blob.sliceArray(0 until 8).contentEquals(MAGIC)) {
            throw VaultTamperedException("Not a valid MyOffice encrypted vault file (missing MOVAULT1 header).")
        }
        val hlen = ByteBuffer.wrap(blob, 8, 4).order(ByteOrder.BIG_ENDIAN).int
        if (hlen <= 2 || 12 + hlen > blob.size) {
            throw VaultTamperedException("Vault file header is truncated or damaged.")
        }
        val header = try {
            JSONObject(String(blob, 12, hlen, Charsets.UTF_8))
        } catch (e: Exception) {
            throw VaultTamperedException("Vault file header JSON is corrupted.", e)
        }
        return Pair(header, 12 + hlen)
    }

    private fun unwrapDek(header: JSONObject, store: DeviceKeyStore, passphrase: String = ""): ByteArray {
        val targetKeyId = header.optString("key_id", "")
        val devKey = store.keyFor(targetKeyId)
        if (devKey != null) {
            try {
                val salt = header.getString("salt").hexToBytes()
                val nonce = header.getString("wrap_nonce").hexToBytes()
                val wrapped = header.getString("wrapped_dek").hexToBytes()
                val kek = deriveHkdf(devKey, salt, AAD_WRAP_DEV)
                return aesGcmDecrypt(kek, nonce, wrapped, AAD_WRAP_DEV)
            } catch (_: VaultException) {
                // Fall through to passphrase slot if supplied
            }
        }
        if (passphrase.isNotEmpty() && header.has("pass_wrapped_dek")) {
            val psalt = header.getString("pass_salt").hexToBytes()
            val pnonce = header.getString("pass_nonce").hexToBytes()
            val pwrapped = header.getString("pass_wrapped_dek").hexToBytes()
            val pkek = derivePassphraseKey(passphrase, psalt)
            return aesGcmDecrypt(pkek, pnonce, pwrapped, AAD_WRAP_PASS)
        }
        val owner = header.optString("device_label", "another computer")
        throw VaultAccessDeniedException(
            "Access denied: this .movault file is hardware-locked to '$owner' (key $targetKeyId). " +
                "Pair your phone with '$owner' via QR code or enter its Vault Recovery Passphrase."
        )
    }

    fun inspectVaultBlob(blob: ByteArray, filename: String, store: DeviceKeyStore, passphrase: String = ""): VaultMetadata {
        val (header, _) = parseHeader(blob)
        val keyId = header.optString("key_id", "")
        val label = header.optString("device_label", "Unknown")
        return try {
            val dek = unwrapDek(header, store, passphrase)
            val metaPlain = aesGcmDecrypt(
                dek,
                header.getString("meta_nonce").hexToBytes(),
                header.getString("meta_ct").hexToBytes(),
                AAD_META
            )
            val meta = JSONObject(String(metaPlain, Charsets.UTF_8))
            VaultMetadata(
                vaultFilename = filename,
                originalName = meta.optString("original_name", filename),
                originalPath = meta.optString("original_path", ""),
                createdAt = meta.optString("created_at", ""),
                originalSize = meta.optLong("size", 0L),
                encryptedSize = blob.size.toLong(),
                sha256 = meta.optString("sha256", ""),
                keyId = keyId,
                deviceLabel = label,
                lockedToOtherDevice = false
            )
        } catch (_: VaultAccessDeniedException) {
            VaultMetadata(
                vaultFilename = filename,
                originalName = "[Hardware-Locked Vault File]",
                originalPath = "",
                createdAt = "",
                originalSize = 0L,
                encryptedSize = blob.size.toLong(),
                sha256 = "",
                keyId = keyId,
                deviceLabel = label,
                lockedToOtherDevice = true
            )
        }
    }

    fun decryptBytes(blob: ByteArray, store: DeviceKeyStore, passphrase: String = ""): DecryptedVaultPayload {
        val (header, offset) = parseHeader(blob)
        val dek = unwrapDek(header, store, passphrase)
        val metaPlain = aesGcmDecrypt(
            dek,
            header.getString("meta_nonce").hexToBytes(),
            header.getString("meta_ct").hexToBytes(),
            AAD_META
        )
        val meta = JSONObject(String(metaPlain, Charsets.UTF_8))
        val dataCt = blob.sliceArray(offset until blob.size)
        val rawBody = try {
            aesGcmDecrypt(dek, header.getString("data_nonce").hexToBytes(), dataCt, AAD_DATA)
        } catch (e: VaultException) {
            throw VaultTamperedException("Vault payload integrity check failed (cipher tag mismatch).", e)
        }
        val plain = if (meta.optBoolean("compressed", true)) zlibDecompress(rawBody) else rawBody
        val expectedSha = meta.optString("sha256", "")
        if (expectedSha.isNotEmpty() && !sha256Hex(plain).equals(expectedSha, ignoreCase = true)) {
            throw VaultTamperedException("Decrypted payload SHA-256 mismatch.")
        }
        return DecryptedVaultPayload(
            bytes = plain,
            originalName = meta.optString("original_name", "restored_file"),
            originalPath = meta.optString("original_path", ""),
            createdAt = meta.optString("created_at", ""),
            sha256 = expectedSha,
            keyId = header.optString("key_id", ""),
            deviceLabel = header.optString("device_label", "")
        )
    }
}
