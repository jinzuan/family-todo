package com.familytodo.app

import android.content.Context
import androidx.core.content.edit

/**
 * 令牌与服务器地址的统一存储（SharedPreferences）。
 *
 * WebView（通过注入 localStorage）与桌面小组件（通过 ApiClient）共用这一份，
 * 即「App 内 join 一次，网页和小组件都能用」。
 */
class TokenStore(context: Context) {

    private val prefs =
        context.applicationContext.getSharedPreferences(AppConfig.PREFS_NAME, Context.MODE_PRIVATE)

    /** M2 网页地址（WebView 加载） */
    var webUrl: String
        get() = prefs.getString(KEY_WEB_URL, null)?.takeIf { it.isNotBlank() }
            ?: AppConfig.DEFAULT_WEB_URL
        set(value) = prefs.edit { putString(KEY_WEB_URL, value.trim()) }

    /** 服务端接口基址，结尾不带 / */
    var apiBase: String
        get() = (prefs.getString(KEY_API_BASE, null)?.takeIf { it.isNotBlank() }
            ?: AppConfig.DEFAULT_API_BASE).trimEnd('/')
        set(value) = prefs.edit { putString(KEY_API_BASE, value.trim().trimEnd('/')) }

    var deviceToken: String?
        get() = prefs.getString(KEY_DEVICE_TOKEN, null)
        set(value) = prefs.edit { putString(KEY_DEVICE_TOKEN, value) }

    var deviceId: String?
        get() = prefs.getString(KEY_DEVICE_ID, null)
        set(value) = prefs.edit { putString(KEY_DEVICE_ID, value) }

    var familyId: String?
        get() = prefs.getString(KEY_FAMILY_ID, null)
        set(value) = prefs.edit { putString(KEY_FAMILY_ID, value) }

    var role: String?
        get() = prefs.getString(KEY_ROLE, null)
        set(value) = prefs.edit { putString(KEY_ROLE, value) }

    var deviceName: String?
        get() = prefs.getString(KEY_DEVICE_NAME, null)
        set(value) = prefs.edit { putString(KEY_DEVICE_NAME, value) }

    /** 是否已加入家庭（有设备令牌即视为已加入） */
    val isLoggedIn: Boolean
        get() = !deviceToken.isNullOrBlank()

    /** join 成功后保存服务端返回的设备身份 */
    fun saveJoin(
        token: String,
        deviceIdValue: String,
        familyIdValue: String,
        roleValue: String,
        deviceNameValue: String?,
    ) {
        prefs.edit {
            putString(KEY_DEVICE_TOKEN, token)
            putString(KEY_DEVICE_ID, deviceIdValue)
            putString(KEY_FAMILY_ID, familyIdValue)
            putString(KEY_ROLE, roleValue)
            putString(KEY_DEVICE_NAME, deviceNameValue)
        }
    }

    /** 退出本机设备：只清设备身份，保留服务器地址等设置 */
    fun clearAuth() {
        prefs.edit {
            remove(KEY_DEVICE_TOKEN)
            remove(KEY_DEVICE_ID)
            remove(KEY_FAMILY_ID)
            remove(KEY_ROLE)
        }
    }

    private companion object {
        const val KEY_WEB_URL = "web_url"
        const val KEY_API_BASE = "api_base"
        const val KEY_DEVICE_TOKEN = "device_token"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_FAMILY_ID = "family_id"
        const val KEY_ROLE = "role"
        const val KEY_DEVICE_NAME = "device_name"
    }
}
