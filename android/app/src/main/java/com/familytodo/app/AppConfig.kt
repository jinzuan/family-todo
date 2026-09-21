package com.familytodo.app

import android.content.Context
import androidx.core.content.edit
import java.util.Properties

/**
 * 全局默认配置与常量。
 *
 * 默认占位符（不含真实域名，开源脱敏）。真实地址通过本地文件
 * assets/server.properties（被 .gitignore 忽略，不进 git）填写：
 *   web_url=https://your-server.example/
 *   api_base=https://your-server.example
 * App 启动时调用 [initFromServer] 把真实值写入 SharedPreferences，
 * TokenStore 从 prefs 读到真实地址；无该文件则回退占位符。
 */
object AppConfig {
    const val PREFS_NAME = "family_todo"

    // 占位符默认（开源/无本地配置时的回退，不给真实域名）
    const val DEFAULT_WEB_URL = "https://YOUR_SERVER.invalid/"
    const val DEFAULT_API_BASE = "https://YOUR_SERVER.invalid"

    /**
     * 从 assets/server.properties 读取真实服务器地址；存在则写入 prefs，
     * 供 TokenStore 读取。若文件缺失，什么都不写（TokenStore 回退占位符）。
     */
    fun initFromServer(context: Context) {
        var webUrl = DEFAULT_WEB_URL
        var apiBase = DEFAULT_API_BASE
        try {
            val input = context.assets.open("server.properties")
            val p = Properties()
            p.load(input)
            input.close()
            p.getProperty("web_url")?.takeIf { it.isNotBlank() }?.let { webUrl = it.trim() }
            p.getProperty("api_base")?.takeIf { it.isNotBlank() }?.let { apiBase = it.trim().trimEnd('/') }
        } catch (_: Exception) {
            return  // 无本地配置 → 保持占位符，不覆盖 prefs
        }
        // 把真实地址写进 prefs，TokenStore 会读到
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit {
            putString("web_url", webUrl)
            putString("api_base", apiBase)
        }
    }
}