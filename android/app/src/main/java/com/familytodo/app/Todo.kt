package com.familytodo.app

import org.json.JSONObject

/** 待办实体：字段对应服务端 GET /api/todos 返回结构。 */
data class Todo(
    val id: String,
    val title: String,
    val parentId: String?,
    val qty: Double?,
    val unit: String?,
    val price: Double?,
    val category: String,
    val statusDetail: String,
) {
    companion object {
        fun fromJson(o: JSONObject): Todo = Todo(
            id = o.optString("id"),
            title = o.optString("title"),
            parentId = o.optString("parent_id").takeIf { it.isNotBlank() && it != "null" },
            qty = if (o.isNull("qty")) null else o.optDouble("qty"),
            unit = o.optString("unit").takeIf { it.isNotBlank() && it != "null" },
            price = if (o.isNull("price")) null else o.optDouble("price"),
            category = o.optString("category", "other"),
            statusDetail = o.optString("status_detail", "pending"),
        )
    }
}
