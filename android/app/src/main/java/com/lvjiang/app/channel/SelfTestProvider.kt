package com.lvjiang.app

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.Binder
import android.os.ParcelFileDescriptor
import android.os.Process
import java.io.File

/**
 * SelfTestProvider — 自检报告的只读回读通道。
 *
 * debug 包时 adb 用 `run-as` 就能读私有目录，但 release 包不可调试，run-as 直接拒绝
 * （"package not debuggable"）。自检报告是 release 复验的唯一观测手段，因此开一条
 * 与 debuggable 无关的通道：
 *
 *     adb shell content read --uri content://com.lvjiang.app.selftest/log
 *
 * 只实现 openFile，只允许自检报告和当前/上一份运行日志三个固定只读路径，
 * 且校验调用方必须是 shell/root/自身 —— 其他应用即使看到这个 exported provider
 * 也拿不到任何东西。
 */
class SelfTestProvider : ContentProvider() {

    override fun onCreate(): Boolean = true

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        val caller = Binder.getCallingUid()
        require(caller == Process.SHELL_UID || caller == Process.ROOT_UID || caller == Process.myUid()) {
            "仅限 adb shell 访问"
        }
        require(mode == "r") { "只读通道" }
        val relative = when (uri.path) {
            "/log" -> "selftest.log"
            "/runtime" -> "lvjiang/data/diagnostics/android-runtime.jsonl"
            "/runtime-previous" -> "lvjiang/data/diagnostics/android-runtime.previous.jsonl"
            else -> throw IllegalArgumentException("未知路径: ${uri.path}")
        }
        val file = File(requireNotNull(context).filesDir, relative)
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    // 纯文件通道，表接口全部不支持
    override fun query(
        uri: Uri, projection: Array<String>?, selection: String?,
        selectionArgs: Array<String>?, sortOrder: String?,
    ): Cursor? = null

    override fun getType(uri: Uri): String = "text/plain"

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<String>?): Int = 0

    override fun update(
        uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<String>?,
    ): Int = 0
}
