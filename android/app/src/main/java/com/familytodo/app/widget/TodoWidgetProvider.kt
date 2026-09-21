package com.familytodo.app.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.RemoteViews
import com.familytodo.app.MainActivity
import com.familytodo.app.R

/**
 * 桌面小组件入口（3×2 默认预设）。
 *
 * V5：新增 4×2 / 2×2 预设子类，并在 appwidget-provider 里声明 resizeMode 与
 * min/maxResize、targetCellWidth/Height，API 31+ 可自由拖拽缩放（低版本仅预设）。
 *
 * - onUpdate：构建 RemoteViews，绑定列表数据源与各类点击 PendingIntent；
 * - ACTION_REFRESH：手动刷新按钮触发；
 * - 定时刷新交给 WorkManager（见 WidgetRefreshWorker），故 updatePeriodMillis=0。
 */
open class TodoWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
    ) {
        for (id in appWidgetIds) {
            updateWidget(context, appWidgetManager, id, javaClass)
        }
        WidgetRefreshWorker.schedulePeriodic(context)
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action == ACTION_REFRESH) {
            refreshAll(context, javaClass)
            WidgetRefreshWorker.enqueueNow(context)
        }
    }

    companion object {
        const val ACTION_REFRESH = "com.familytodo.app.action.WIDGET_REFRESH"

        /** 为一个小组件实例绑定布局与数据源（providerClass 决定刷新广播回到哪个预设） */
        fun updateWidget(
            context: Context,
            manager: AppWidgetManager,
            appWidgetId: Int,
            providerClass: Class<*> = TodoWidgetProvider::class.java,
        ) {
            val views = RemoteViews(context.packageName, R.layout.widget_todo)

            // 列表数据源：每个 widgetId 用独立 data Uri，避免多个小组件串数据
            val serviceIntent = Intent(context, TodoWidgetService::class.java).apply {
                putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId)
            }
            serviceIntent.data = Uri.parse(serviceIntent.toUri(Intent.URI_INTENT_SCHEME))
            views.setRemoteAdapter(R.id.widget_list, serviceIntent)
            views.setEmptyView(R.id.widget_list, R.id.widget_empty)

            // 点标题 → 打开 App
            val openApp = Intent(context, MainActivity::class.java)
            val openPi = PendingIntent.getActivity(
                context, 0, openApp,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            views.setOnClickPendingIntent(R.id.widget_header, openPi)

            // 点刷新图标 → 广播回当前预设 Provider
            val refresh = Intent(context, providerClass).setAction(ACTION_REFRESH)
            val refreshPi = PendingIntent.getBroadcast(
                context, 1, refresh,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            views.setOnClickPendingIntent(R.id.widget_refresh, refreshPi)

            // 列表项的点击模板：具体 id 由 item 的 fill-in intent 提供
            val template = Intent(context, CompleteActionReceiver::class.java)
            val templatePi = PendingIntent.getBroadcast(
                context, 2, template,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
            )
            views.setPendingIntentTemplate(R.id.widget_list, templatePi)

            manager.updateAppWidget(appWidgetId, views)
        }

        /** 通知所有预设小组件重新加载列表数据（触发工厂 onDataSetChanged） */
        fun refreshAll(context: Context) {
            refreshAll(context, TodoWidgetProvider::class.java)
            refreshAll(context, TodoWidgetWideProvider::class.java)
            refreshAll(context, TodoWidgetSmallProvider::class.java)
        }

        /** 通知指定预设的小组件重新加载数据 */
        fun refreshAll(context: Context, providerClass: Class<*>) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, providerClass))
            if (ids.isNotEmpty()) {
                manager.notifyAppWidgetViewDataChanged(ids, R.id.widget_list)
            }
        }
    }
}

/** 4×2 宽版预设：多展示几条，适合购物清单。 */
class TodoWidgetWideProvider : TodoWidgetProvider()

/** 2×2 紧凑预设：只看最近待办。 */
class TodoWidgetSmallProvider : TodoWidgetProvider()
