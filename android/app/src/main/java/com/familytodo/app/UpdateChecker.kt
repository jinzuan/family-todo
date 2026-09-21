package com.familytodo.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import org.json.JSONObject

/**
 * App 自更新（简版）：
 * App 启动时拉取 GET /api/app/latest.json，若服务端 version_code 大于本机
 * BuildConfig.VERSION_CODE，弹窗提示「立即更新 / 以后再说」。
 * 「立即更新」打开 apk_url（交给系统浏览器/下载器下载并安装）。
 *
 * 版本受控：服务端通过环境变量 APP_VERSION_CODE / APP_VERSION_NAME / APP_APK_URL 等控制，
 * 无需改代码即可推送新版本（详见 server/app/routers/app_update.py）。
 */
object UpdateChecker {

    // 一次进程内成功拿到版本信息后才置位，避免每次 onStart 都弹窗
    @Volatile
    private var checkedThisProcess = false

    // 后台检查进行中：onCreate 与 onResume 会各调一次，防止并发重复请求
    @Volatile
    private var checking = false

    /** 在 MainActivity.onCreate / onResume 调用；必须在 UI 线程调用。 */
    fun checkOnLaunch(activity: Activity) {
        if (checkedThisProcess || checking) return

        val store = TokenStore(activity)
        // 未加入家庭时先不打扰（首次安装会先引导去设置页）；加入后的 onResume 会再检查。
        if (!store.isLoggedIn) return
        val apiBase = store.apiBase
        // latest.json 免鉴权；地址无效时提前返回且不置位，等地址配好后仍会检查。
        if (apiBase.isBlank() || apiBase.contains("YOUR_SERVER")) return
        checking = true

        Thread {
            // 拉取失败：本次不算「已检查」，checkedThisProcess 保持 false，
            // 下次 onResume 会再试；避免一次网络抖动/冷启动超时就永久吞掉更新提示。
            val info: JSONObject? = try {
                ApiClient.latestApp(apiBase)
            } catch (e: Exception) {
                null
            } finally {
                checking = false
            }
            if (info == null) return@Thread

            // 成功拿到版本信息才算检查完成（无论是否真的有新版）
            checkedThisProcess = true

            val latestCode = info.optInt("version_code", 0)
            val currentCode = BuildConfig.VERSION_CODE
            val minSupported = info.optInt("min_supported_version_code", 0)
            val tooOld = currentCode < minSupported
            if (latestCode <= currentCode && !tooOld) return@Thread

            val versionName = info.optString("version_name", latestCode.toString())
            val apkUrl = resolveUrl(apiBase, info.optString("apk_url", ""))
            if (apkUrl.isBlank()) return@Thread
            val changelog = info.optString("changelog", "")
            val force = info.optBoolean("force", false) || tooOld

            activity.runOnUiThread {
                if (activity.isFinishing || activity.isDestroyed) {
                    // 宿主已销毁、弹不出来：放行下一次 onResume 再检查，别把提示吞了
                    checkedThisProcess = false
                    return@runOnUiThread
                }
                showDialog(activity, versionName, changelog, apkUrl, force)
            }
        }.start()
    }

    /** apk_url 支持绝对地址，也支持 / 开头的相对路径（拼到接口基址） */
    private fun resolveUrl(apiBase: String, rawUrl: String): String {
        val url = rawUrl.trim()
        return when {
            url.isBlank() -> ""
            url.startsWith("http://") || url.startsWith("https://") -> url
            url.startsWith("/") -> apiBase.trimEnd('/') + url
            else -> apiBase.trimEnd('/') + "/" + url
        }
    }

    private fun showDialog(
        activity: Activity,
        versionName: String,
        changelog: String,
        apkUrl: String,
        force: Boolean,
    ) {
        val message = buildString {
            append("发现新版本 ")
            append(versionName)
            if (changelog.isNotBlank()) {
                append("\n\n")
                append(changelog)
            }
        }
        val builder = AlertDialog.Builder(activity)
            .setTitle("软件更新")
            .setMessage(message)
            .setPositiveButton("立即更新") { _, _ -> openDownload(activity, apkUrl) }
        if (!force) {
            builder.setNegativeButton("以后再说", null)
        }
        // setCanceledOnTouchOutside 是 Dialog 的实例方法，AlertDialog.Builder 上没有；
        // 必须先 create() 拿到 AlertDialog 再调用。
        val dialog = builder.create()
        if (force) {
            // 强制更新：无「以后再说」，返回键/点外部也不能关
            dialog.setCancelable(false)
            dialog.setCanceledOnTouchOutside(false)
        }
        dialog.show()
    }

    private fun openDownload(activity: Activity, apkUrl: String) {
        try {
            activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl)))
        } catch (e: Exception) {
            Toast.makeText(activity, "无法打开下载链接", Toast.LENGTH_SHORT).show()
        }
    }
}
