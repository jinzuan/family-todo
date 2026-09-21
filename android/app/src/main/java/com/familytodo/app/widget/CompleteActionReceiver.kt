package com.familytodo.app.widget

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * 小组件「点即勾」：收到列表项点击，把「勾选完成」快速委托给 WorkManager，
 * 立即释放广播，不在广播线程里等网络。
 *
 * 修复 0.5.4 报告的点勾迟钝/丢点：
 * - 旧版：onReceive 里 goAsync() + 线程内同步 await ApiClient.completeTodo +
 *   refreshAll。Android 对 BroadcastReceiver 有 ~10 秒执行时限，网络稍慢就会
 *   被系统回收，请求没发出去 = 用户看到「点了没反应，要点好几次」。
 * - 新版：onReceive 立即入队 CompleteTodoWorker（WorkManager，带网络约束、
 *   失败可重试），然后 pendingResult.finish() 秒回，网络操作移出广播生命周期。
 */
class CompleteActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val todoId = intent.getStringExtra(EXTRA_TODO_ID)
        if (todoId.isNullOrBlank()) return
        val pendingResult = goAsync()
        // 快速委托，不阻塞广播；为防极端情况下 Worker 未能及时入队，仍用 goAsync 兜底。
        CompleteTodoWorker.enqueue(context, todoId)
        pendingResult.finish()
    }

    companion object {
        const val EXTRA_TODO_ID = "extra_todo_id"
    }
}