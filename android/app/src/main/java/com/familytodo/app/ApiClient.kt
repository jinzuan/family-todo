package com.familytodo.app

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/**
 * 极简 REST 客户端：只用 JDK 自带的 HttpURLConnection + org.json，不引第三方网络库。
 *
 * 所有方法都会阻塞，必须在后台线程调用（工作线程 / WorkManager / BroadcastReceiver 的 goAsync 线程）。
 */
object ApiClient {

    data class JoinResult(
        val deviceToken: String,
        val deviceId: String,
        val familyId: String,
        val role: String,
    )

    class ApiException(message: String) : Exception(message)

    /** 用家庭令牌加入，返回设备令牌等身份信息（对应 POST /api/auth/join） */
    fun join(apiBase: String, familyToken: String, deviceName: String?, role: String?): JoinResult {
        val payload = JSONObject().apply {
            put("family_token", familyToken)
            if (!deviceName.isNullOrBlank()) put("device_name", deviceName)
            if (!role.isNullOrBlank()) put("role", role)
        }
        val json = request("POST", "${apiBase.trimEnd('/')}/api/auth/join", null, payload.toString())
        return JoinResult(
            deviceToken = json.optString("device_token"),
            deviceId = json.optString("device_id"),
            familyId = json.optString("family_id"),
            role = json.optString("role", "executor"),
        )
    }

    /** 拉取未完成待办（平铺，含子待办），对应 GET /api/todos?flat=1&include_done=0；已勾/移除项不再返回，避免小组件残留。 */
    fun listTodos(apiBase: String, token: String): List<Todo> {
        val json = request("GET", "${apiBase.trimEnd('/')}/api/todos?flat=1&include_done=0", token, null)
        val arr = json.optJSONArray("items") ?: JSONArray()
        val result = ArrayList<Todo>(arr.length())
        for (i in 0 until arr.length()) {
            arr.optJSONObject(i)?.let { result.add(Todo.fromJson(it)) }
        }
        return result
    }

    /** 检查 App 最新版本，对应 GET /api/app/latest.json（免鉴权） */
    fun latestApp(apiBase: String): JSONObject {
        return request("GET", "${apiBase.trimEnd('/')}/api/app/latest.json", null, null)
    }

    /**
     * 勾选完成（POST /api/todos/{id}/complete）。
     * 若父项仍有未处理子项，服务端返回 {"needs_decision": true, ...}，本方法原样返回该 JSON。
     */
    fun completeTodo(apiBase: String, token: String, todoId: String): JSONObject {
        return request("POST", "${apiBase.trimEnd('/')}/api/todos/$todoId/complete", token, "{}")
    }

    private fun request(method: String, url: String, token: String?, body: String?): JSONObject {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8_000
            conn.readTimeout = 10_000
            conn.setRequestProperty("Accept", "application/json")
            if (!token.isNullOrBlank()) {
                conn.setRequestProperty("Authorization", "Bearer $token")
            }
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toByteArray(StandardCharsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream: InputStream? = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.let { readAll(it) } ?: ""
            if (code !in 200..299) {
                val detail = try {
                    JSONObject(text).optString("detail", text)
                } catch (e: Exception) {
                    text
                }
                throw ApiException(detail.ifBlank { "请求失败 $code" })
            }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    private fun readAll(input: InputStream): String =
        BufferedReader(InputStreamReader(input, StandardCharsets.UTF_8)).use { it.readText() }
}
