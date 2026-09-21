package com.familytodo.app.widget

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * 小组件定时刷新：最短 15 分钟一次，需要网络。
 * 只负责通知数据源重新加载，真正拉取在 TodoRemoteViewsFactory.onDataSetChanged。
 */
class WidgetRefreshWorker(context: Context, params: WorkerParameters) :
    Worker(context, params) {

    override fun doWork(): Result {
        TodoWidgetProvider.refreshAll(applicationContext)
        return Result.success()
    }

    companion object {
        private const val PERIODIC_NAME = "widget_refresh"

        private fun networkConstraint(): Constraints =
            Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

        /** 安排周期刷新（KEEP：已存在则不重复建） */
        fun schedulePeriodic(context: Context) {
            val request = PeriodicWorkRequestBuilder<WidgetRefreshWorker>(15, TimeUnit.MINUTES)
                .setConstraints(networkConstraint())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                PERIODIC_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
        }

        /** 立即刷新一次（手动点刷新、勾选完成后调用） */
        fun enqueueNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<WidgetRefreshWorker>()
                .setConstraints(networkConstraint())
                .build()
            WorkManager.getInstance(context).enqueue(request)
        }
    }
}
