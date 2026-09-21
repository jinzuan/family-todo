package com.familytodo.app

import android.app.Activity
import android.app.Application
import android.os.Bundle
import com.familytodo.app.widget.TodoWidgetProvider
import com.familytodo.app.widget.WidgetRefreshWorker

/**
 * 应用入口：
 * - 读取 assets/server.properties 里的真实服务器地址；
 * - 安排小组件周期刷新任务；
 * - 监听 App 前台/后台切换，切换时刷新 3 个桌面小组件。
 */
class App : Application() {

    /** 已 start 的 Activity 数：0→1 进前台，1→0 退后台 */
    private var startedActivities = 0

    override fun onCreate() {
        super.onCreate()
        // 从 assets/server.properties 读取真实服务器地址（开源为占位符），写入 SharedPreferences
        AppConfig.initFromServer(this)
        // WorkManager 周期任务：最短 15 分钟，作为 WebSocket 实时刷新之外的兜底
        WidgetRefreshWorker.schedulePeriodic(this)
        registerActivityLifecycleCallbacks(LifecycleWatcher())
    }

    private inner class LifecycleWatcher : ActivityLifecycleCallbacks {
        override fun onActivityStarted(activity: Activity) {
            startedActivities++
            if (startedActivities == 1) {
                // 回到前台：通知小组件拉取最新数据
                TodoWidgetProvider.refreshAll(this@App)
            }
        }

        override fun onActivityStopped(activity: Activity) {
            startedActivities = (startedActivities - 1).coerceAtLeast(0)
            if (startedActivities == 0) {
                // 退到后台：再刷一次，保证桌面小组件显示最新
                TodoWidgetProvider.refreshAll(this@App)
            }
        }

        override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
        override fun onActivityResumed(activity: Activity) {}
        override fun onActivityPaused(activity: Activity) {}
        override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
        override fun onActivityDestroyed(activity: Activity) {}
    }
}
