package com.familytodo.app.widget

import android.content.Context
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.familytodo.app.ApiClient
import com.familytodo.app.TokenStore

/**
 * 小组件「点即勾」的后台执行器（WorkManager）。
 *
 * 修复 0.5.4 报告的小组件点勾「迟钝/丢点」：
 * 旧逻辑在 BroadcastReceiver.onReceive 里 goAsync() + 起线程同步等网络
 * (ApiClient.completeTodo) + refreshAll，全程占用广播 -> Android ~10 秒后
 * 会回收广播接收器，网络稍慢就把请求丢掉，表现为「点了没反应 / 要点几次」。
 *
 * 改为：onReceive 只快速入队一个 OneTimeWork（传 todoId），立即 finish() 释放
 * 广播；真正的 complete + 刷新交给本 Worker 在后台做，不占广播超时，不丢点；
 * 失败可重试，网络未连时排队等待。
 */
class CompleteTodoWorker(context: Context, params: WorkerParameters) :
    Worker(context, params) {

    override fun doWork(): Result {
        val todoId = inputData.getString(KEY_TODO_ID) ?: return Result.failure()
        return try {
            val store = TokenStore(applicationContext)
            if (store.isLoggedIn) {
                val token = store.deviceToken
                if (token != null) {
                    // 同旧约定：只发 /complete。父项有未决子项时服务端返回
                    // needs_decision，此处忽略、保持原状，交 App 内界面处理。
                    ApiClient.completeTodo(store.apiBase, token, todoId)
                }
            }
            // 刷新小组件列表（含自身 & 各预设）
            TodoWidgetProvider.refreshAll(applicationContext)
            Result.success()
        } catch (e: Exception) {
            // 网络/服务端失败：可重试一次；条目仍在，失败也能被后续刷新拉回。
            Result.retry()
        }
    }

    companion object {
        const val KEY_TODO_ID = "extra_todo_id"

        private fun networkConstraint(): Constraints =
            Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

        /** 入队一次「勾选完成」后台任务。 */
        fun enqueue(context: Context, todoId: String) {
            val data = Data.Builder().putString(KEY_TODO_ID, todoId).build()
            val request = OneTimeWorkRequestBuilder<CompleteTodoWorker>()
                .setInputData(data)
                .setConstraints(networkConstraint())
                .addTag("widget_complete")
                .build()
            WorkManager.getInstance(context).enqueue(request)
        }
    }
}