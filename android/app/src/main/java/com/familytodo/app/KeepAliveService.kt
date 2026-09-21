package com.familytodo.app

import android.app.Service
import android.content.Intent
import android.os.IBinder

/**
 * 前台服务占位。
 *
 * 规划中用于「防杀后台 + 常驻通知 + 推送通道」（M4）。当前不主动启动，
 * 仅按任务书要求预留在 AndroidManifest 中，避免后续加服务时再改壳。
 */
class KeepAliveService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
}
