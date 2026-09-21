package com.familytodo.app

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ImageButton
import android.widget.ProgressBar
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import com.familytodo.app.widget.TodoWidgetProvider
import org.json.JSONObject

/**
 * 主界面：WebView 承载 M2 网页。
 *
 * 若本机还没有设备令牌，自动打开设置页引导加入；
 * 加入成功后把令牌注入网页 localStorage（键名与 src/app.js 的 KEYS 一致），
 * 网页即自动进入已登录状态，无需在网页里再输一次令牌。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var settingsButton: ImageButton
    private lateinit var progressBar: ProgressBar
    private lateinit var store: TokenStore

    // 记录已加载的地址/令牌，用于判断是否需要重新加载
    private var loadedUrl: String? = null
    private var loadedToken: String? = null
    // 防止 onPageFinished 注入后 reload 造成死循环
    private var injected = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        // 从启动主题（品牌绿 + 图标）切回正常主题，避免冷启动白屏
        setTheme(R.style.Theme_FamilyTodo)
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        store = TokenStore(this)
        webView = findViewById(R.id.web_view)
        settingsButton = findViewById(R.id.btn_settings)
        progressBar = findViewById(R.id.web_progress)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // localStorage 存令牌
            databaseEnabled = true
            useWideViewPort = true
            loadWithOverviewMode = true
            // 缩放锁：固定 100%，不随系统字体大小(避免妈OPPO大字炸版)，但保留双指缩放能力
            textZoom = 100
            setSupportZoom(true)
            builtInZoomControls = true
            displayZoomControls = false
            setDefaultFontSize(16)
            // 注意: setInitialScale 不在 WebSettings, 是 WebView 方法; 这里不设, 让 useWideViewPort 决定
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                super.onProgressChanged(view, newProgress)
                progressBar.progress = newProgress
                progressBar.visibility =
                    if (newProgress in 1..99) View.VISIBLE else View.GONE
            }
        }
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (store.isLoggedIn && !injected) {
                    injectAuth()
                    injected = true
                    view?.reload()
                }
            }
        }

        settingsButton.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // 网页 WebSocket 收到 todo.* 变动时，通过 JS 桥通知原生刷新桌面小组件
        webView.addJavascriptInterface(WebBridge(), "FTAndroid")

        // App 启动时检查服务器 latest.json，有新版本则提示下载安装
        UpdateChecker.checkOnLaunch(this)

        // 首次进入且未加入：直接引导到设置页
        if (!store.isLoggedIn) {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // 返回键：优先网页后退
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        ensureLoaded()
    }

    override fun onStart() {
        super.onStart()
        // 回到前台：通知小组件拉取最新待办
        TodoWidgetProvider.refreshAll(this)
    }

    override fun onStop() {
        // 退到后台：再刷一次，保证桌面小组件显示最新数据
        TodoWidgetProvider.refreshAll(this)
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        // 从设置页返回后，地址或令牌可能变了，按需重新加载
        ensureLoaded()
        // 补一次更新检查：首次安装时 onCreate 尚未加入家庭会被跳过，加入后回到前台即可弹窗
        UpdateChecker.checkOnLaunch(this)
    }

    private fun ensureLoaded() {
        val url = store.webUrl
        val token = store.deviceToken
        if (loadedUrl != url || loadedToken != token) {
            loadedUrl = url
            loadedToken = token
            injected = false
            webView.loadUrl(url)
        }
    }

    /** 把设备令牌写入网页 localStorage（键名对应 src/app.js） */
    private fun injectAuth() {
        if (!store.isLoggedIn) return
        val js = """
            (function(){
              localStorage.setItem('ft_api', ${JSONObject.quote(store.apiBase)});
              localStorage.setItem('ft_token', ${JSONObject.quote(store.deviceToken ?: "")});
              localStorage.setItem('ft_device', ${JSONObject.quote(store.deviceId ?: "")});
              localStorage.setItem('ft_family', ${JSONObject.quote(store.familyId ?: "")});
              localStorage.setItem('ft_role', ${JSONObject.quote(store.role ?: "executor")});
              localStorage.setItem('ft_device_name', ${JSONObject.quote(store.deviceName ?: "")});
            })();
        """.trimIndent()
        webView.evaluateJavascript(js, null)
    }

    /**
     * 网页 → 原生 桥：src/app.js 的 WebSocket 收到 todo.* 事件时调用
     * window.FTAndroid.onTodosChanged()，原生立即刷新 3 个桌面小组件。
     */
    private inner class WebBridge {
        @JavascriptInterface
        fun onTodosChanged() {
            runOnUiThread {
                TodoWidgetProvider.refreshAll(this@MainActivity)
            }
        }
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
