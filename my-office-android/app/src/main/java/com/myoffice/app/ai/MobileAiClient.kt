package com.myoffice.app.ai

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.ln

/**
 * Full Mobile AI Assistant Engine (`MobileAiClient`):
 *  - Works completely free & offline over Wi-Fi against **Ollama / LM Studio running on your Paired PC**
 *    (`http://<pc_ip>:11434/v1`) OR online with **OpenAI, Google Gemini, Anthropic Claude, Groq, OpenRouter**!
 *  - Includes an on-device pure-Kotlin **Okapi BM25 Retriever** for `Chat with PDF` with exact clickable page citations!
 */
object MobileAiClient {

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .build()

    fun complete(
        provider: String,
        endpointUrl: String,
        apiKey: String,
        model: String,
        systemPrompt: String,
        userPrompt: String
    ): String {
        val cleanProvider = provider.lowercase().trim()
        val effectiveUrl = when {
            endpointUrl.isNotBlank() -> endpointUrl.trimEnd('/')
            cleanProvider == "openai" -> "https://api.openai.com/v1"
            cleanProvider == "gemini" -> "https://generativelanguage.googleapis.com/v1beta/openai"
            cleanProvider == "groq" -> "https://api.groq.com/openai/v1"
            cleanProvider == "openrouter" -> "https://openrouter.ai/api/v1"
            cleanProvider == "anthropic" -> "https://api.anthropic.com/v1"
            else -> "http://127.0.0.1:11434/v1" // Ollama default
        }

        if (cleanProvider == "anthropic") {
            val payload = JSONObject()
                .put("model", model.ifBlank { "claude-3-5-sonnet-latest" })
                .put("max_tokens", 2048)
                .put("system", systemPrompt)
                .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", userPrompt)))
            val req = Request.Builder()
                .url("$effectiveUrl/messages")
                .header("x-api-key", apiKey)
                .header("anthropic-version", "2023-06-01")
                .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()
            http.newCall(req).execute().use { resp ->
                val raw = resp.body?.string() ?: "{}"
                if (!resp.isSuccessful) throw RuntimeException("Anthropic error (${resp.code}): $raw")
                val content = JSONObject(raw).optJSONArray("content") ?: return ""
                return (0 until content.length()).joinToString("") {
                    content.optJSONObject(it)?.optString("text", "") ?: ""
                }
            }
        }

        val payload = JSONObject()
            .put("model", model.ifBlank { "llama3.2" })
            .put(
                "messages",
                JSONArray()
                    .put(JSONObject().put("role", "system").put("content", systemPrompt))
                    .put(JSONObject().put("role", "user").put("content", userPrompt))
            )
            .put("temperature", 0.3)

        val builder = Request.Builder()
            .url("$effectiveUrl/chat/completions")
            .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
        if (apiKey.isNotBlank()) {
            builder.header("Authorization", "Bearer $apiKey")
        }
        http.newCall(builder.build()).execute().use { resp ->
            val raw = resp.body?.string() ?: "{}"
            if (!resp.isSuccessful) {
                throw RuntimeException("AI server error (${resp.code}): ${raw.take(240)}")
            }
            val choices = JSONObject(raw).optJSONArray("choices") ?: return ""
            return choices.optJSONObject(0)?.optJSONObject("message")?.optString("content", "") ?: ""
        }
    }

    /**
     * Local On-Device Okapi BM25 Retriever for **Chat with PDF**: ranks pages in milliseconds with zero network leak
     * of unneeded pages, returning top passages with `[Page X]` citations.
     */
    fun retrieveTopPdfPagesBm25(pagesText: List<String>, query: String, topK: Int = 4): List<Pair<Int, String>> {
        val tokenize = { s: String ->
            s.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.length >= 2 }
        }
        val qTerms = tokenize(query)
        if (pagesText.isEmpty()) return emptyList()
        if (qTerms.isEmpty()) {
            return pagesText.take(topK).mapIndexed { idx, txt -> (idx + 1) to txt }
        }
        val docs = pagesText.map(tokenize)
        val avgDl = docs.sumOf { it.size }.toDouble() / docs.size.coerceAtLeast(1)
        val n = docs.size
        val k1 = 1.5
        val b = 0.75

        val df = qTerms.distinct().associateWith { term -> docs.count { term in it } }
        val scored = docs.mapIndexed { idx, tokens ->
            val tfMap = tokens.groupingBy { it }.eachCount()
            var score = 0.0
            for (term in qTerms.distinct()) {
                val f = tfMap[term] ?: 0
                if (f == 0) continue
                val docFreq = df[term] ?: 0
                val idf = ln(1.0 + (n - docFreq + 0.5) / (docFreq + 0.5))
                val denom = f + k1 * (1.0 - b + b * (tokens.size / avgDl.coerceAtLeast(1.0)))
                score += idf * (f * (k1 + 1.0)) / denom
            }
            Triple(idx + 1, score, pagesText[idx])
        }
        return scored.sortedByDescending { it.second }
            .take(topK)
            .filter { it.third.isNotBlank() }
            .map { it.first to it.third }
    }
}
