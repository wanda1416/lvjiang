package com.lvjiang.app

import android.util.Log
import kotlin.system.exitProcess

/**
 * ShellService — 运行在 Shizuku 托管进程内（shell uid），实现 IShellService。
 *
 * 每次 exec 起一个子进程执行命令，stdout 原样返回字节流（screencap -p 依赖二进制安全），
 * stderr 仅在非空时以 "[stderr]" 前缀附加在末尾（文本命令调试用）。
 */
class ShellService : IShellService.Stub() {

    @Volatile
    private var lastExit = -1

    override fun exec(cmd: Array<String>): ByteArray {
        return try {
            val proc = ProcessBuilder(*cmd).start()
            // 并行读 stderr，避免管道缓冲区互锁
            val errBuf = StringBuilder()
            val errThread = Thread {
                proc.errorStream.bufferedReader().forEachLine { errBuf.appendLine(it) }
            }
            errThread.start()
            val out = proc.inputStream.readBytes()
            errThread.join(5000)
            lastExit = proc.waitFor()
            if (errBuf.isNotEmpty()) {
                out + "\n[stderr]$errBuf".toByteArray()
            } else {
                out
            }
        } catch (e: Exception) {
            Log.e(TAG, "exec failed: ${cmd.joinToString(" ")}", e)
            lastExit = -1
            "[stderr]${e.message}".toByteArray()
        }
    }

    override fun lastExitCode(): Int = lastExit

    override fun destroy() {
        exitProcess(0)
    }

    companion object {
        private const val TAG = "ShellService"
    }
}
