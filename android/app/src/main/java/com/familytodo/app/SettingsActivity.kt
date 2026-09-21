package com.familytodo.app

import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

/**
 * 设置 / 加入家庭：
 * - 配置网页地址与接口地址（可指向自建服务器）；
 * - 输入家庭令牌 → 调 /api/auth/join → 存设备令牌（WebView 与小组件共用）；
 * - 可退出本机设备。
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var store: TokenStore
    private lateinit var webUrlInput: EditText
    private lateinit var apiBaseInput: EditText
    private lateinit var familyTokenInput: EditText
    private lateinit var deviceNameInput: EditText
    private lateinit var roleSpinner: Spinner
    private lateinit var joinButton: Button

    private val roles = listOf("executor" to "执行端", "publisher" to "发布端")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        store = TokenStore(this)
        // 主题是 NoActionBar，用自绘头部栏的返回键
        findViewById<ImageButton>(R.id.btn_back).setOnClickListener { finish() }
        webUrlInput = findViewById(R.id.input_web_url)
        apiBaseInput = findViewById(R.id.input_api_base)
        familyTokenInput = findViewById(R.id.input_family_token)
        deviceNameInput = findViewById(R.id.input_device_name)
        roleSpinner = findViewById(R.id.spinner_role)
        joinButton = findViewById(R.id.btn_join)

        // 关于 / 版本：标签在 XML 里，代码只填值（应用名 + versionName/versionCode，来自 BuildConfig）
        findViewById<TextView>(R.id.text_about_app_name).text =
            getString(R.string.app_name)
        findViewById<TextView>(R.id.text_about_version).text =
            getString(R.string.about_version_value, BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE)
        // 制作者署名 + 致谢 + 开源许可
        findViewById<TextView>(R.id.text_about_credits).text =
            getString(R.string.about_credits)
        findViewById<TextView>(R.id.text_about_credits_note).text =
            getString(R.string.about_credits_note)
        findViewById<TextView>(R.id.text_about_license).text =
            getString(R.string.about_license)

        roleSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            roles.map { it.second },
        )

        // 回填已保存的设置
        webUrlInput.setText(store.webUrl)
        apiBaseInput.setText(store.apiBase)
        deviceNameInput.setText(store.deviceName ?: "安卓设备")
        roleSpinner.setSelection(roles.indexOfFirst { it.first == store.role }.coerceAtLeast(0))

        findViewById<Button>(R.id.btn_save).setOnClickListener {
            persistSettings()
            toast("已保存")
            finish()
        }
        joinButton.setOnClickListener { join() }
        findViewById<Button>(R.id.btn_logout).setOnClickListener {
            store.clearAuth()
            toast("已退出本机设备")
            finish()
        }
    }

    private fun selectedRole(): String =
        roles.getOrNull(roleSpinner.selectedItemPosition)?.first ?: "executor"

    private fun persistSettings() {
        store.webUrl = webUrlInput.text.toString()
        store.apiBase = apiBaseInput.text.toString()
        store.deviceName = deviceNameInput.text.toString().trim().ifBlank { "安卓设备" }
    }

    private fun join() {
        val api = apiBaseInput.text.toString().trim().trimEnd('/')
        val familyToken = familyTokenInput.text.toString().trim()
        if (api.isBlank()) {
            toast("请填写接口地址")
            return
        }
        if (familyToken.isBlank()) {
            toast("请填写家庭令牌")
            return
        }
        val deviceName = deviceNameInput.text.toString().trim().ifBlank { "安卓设备" }
        val role = selectedRole()

        joinButton.isEnabled = false
        Thread {
            try {
                val result = ApiClient.join(api, familyToken, deviceName, role)
                runOnUiThread {
                    store.webUrl = webUrlInput.text.toString()
                    store.apiBase = api
                    store.saveJoin(
                        result.deviceToken,
                        result.deviceId,
                        result.familyId,
                        result.role.ifBlank { role },
                        deviceName,
                    )
                    joinButton.isEnabled = true
                    toast("已加入家庭")
                    finish()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    joinButton.isEnabled = true
                    toast(e.message ?: "加入失败")
                }
            }
        }.start()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
