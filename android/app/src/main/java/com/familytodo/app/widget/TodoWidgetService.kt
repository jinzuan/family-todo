package com.familytodo.app.widget

import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import android.widget.RemoteViewsService
import com.familytodo.app.ApiClient
import com.familytodo.app.R
import com.familytodo.app.TokenStore
import com.familytodo.app.Todo

/** 小组件列表的数据源服务。 */
class TodoWidgetService : RemoteViewsService() {
    override fun onGetViewFactory(intent: Intent): RemoteViewsFactory =
        TodoRemoteViewsFactory(applicationContext)
}

/**
 * 列表工厂：在 onDataSetChanged()（系统保证在后台线程）里同步拉取待办。
 * 每行的点击通过 fill-in intent 传给 Provider 的 PendingIntent 模板。
 */
class TodoRemoteViewsFactory(private val context: Context) :
    RemoteViewsService.RemoteViewsFactory {

    private val items = ArrayList<Todo>()

    override fun onCreate() {}

    override fun onDestroy() {
        items.clear()
    }

    override fun onDataSetChanged() {
        items.clear()
        val store = TokenStore(context)
        if (!store.isLoggedIn) return
        try {
            items.addAll(ApiClient.listTodos(store.apiBase, store.deviceToken!!))
        } catch (e: Exception) {
            // 网络失败保持空列表，用户可点右上角刷新
        }
    }

    override fun getCount(): Int = items.size

    override fun getViewAt(position: Int): RemoteViews {
        val todo = items[position]
        val views = RemoteViews(context.packageName, R.layout.widget_item_todo)
        views.setTextViewText(R.id.item_title, todo.title)
        // 已完成/忽略/取消项已由接口过滤（include_done=0）不再出现；此处保留兜底渲染
        val isDone = todo.statusDetail == "completed"
        if (isDone) {
            views.setTextColor(R.id.item_title, 0xFF9aa39c.toInt())
            // RemoteViews 无 getInt，直接 setInt 设置划线标志（无旧标志可读，直接赋值即可）
            views.setInt(R.id.item_title, "setPaintFlags", android.graphics.Paint.STRIKE_THRU_TEXT_FLAG)
        } else {
            views.setTextColor(R.id.item_title, 0xFF333a35.toInt())
        }
        // 划线开关：完成后划掉标题（如需恢复为无划线，单独处理）
        val subtitle = buildSubtitle(todo)
        // 无数量/分类时隐藏副标题，避免空行把行距撑大（紧凑）
        if (subtitle.isBlank()) {
            views.setViewVisibility(R.id.item_sub, android.view.View.GONE)
        } else {
            views.setViewVisibility(R.id.item_sub, android.view.View.VISIBLE)
            views.setTextViewText(R.id.item_sub, subtitle)
        }
        val fillIn = Intent().apply {
            putExtra(CompleteActionReceiver.EXTRA_TODO_ID, todo.id)
        }
        views.setOnClickFillInIntent(R.id.item_root, fillIn)
        return views
    }

    override fun getLoadingView(): RemoteViews? = null

    override fun getViewTypeCount(): Int = 1

    override fun getItemId(position: Int): Long = items[position].id.hashCode().toLong()

    override fun hasStableIds(): Boolean = true

    private fun buildSubtitle(todo: Todo): String {
        val parts = ArrayList<String>()
        todo.qty?.let { parts.add("×" + formatNumber(it) + (todo.unit ?: "")) }
        todo.price?.let { parts.add("¥" + formatNumber(it)) }
        when (todo.category) {
            "shopping" -> parts.add("购物")
            "errand" -> parts.add("事务")
        }
        return parts.joinToString(" · ")
    }

    private fun formatNumber(value: Double): String =
        if (value == value.toLong().toDouble()) value.toLong().toString() else value.toString()
}
