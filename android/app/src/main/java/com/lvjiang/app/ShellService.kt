package com.lvjiang.app

import android.os.ParcelFileDescriptor
import android.util.Log
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.system.exitProcess

/**
 * ShellService — 运行在 Shizuku 托管进程内（shell uid），实现 IShellService。
 *
 * 每次 exec 起一个子进程执行命令，stdout 原样返回字节流，stderr 仅在非空时以
 * "[stderr]" 前缀附加在末尾；大数据量命令（如 screencap -p）走 execToFd 管道，
 * 避免 Binder 1MB TransactionTooLargeException，并统一强制超时与强杀。
 */
class ShellService : IShellService.Stub() {

    private val lastExitByThread = ThreadLocal.withInitial { -1 }

    @Volatile
    private var lastExit = -1

    private fun recordExit(code: Int) {
        lastExitByThread.set(code)
        lastExit = code
    }

    override fun exec(cmd: Array<String>): ByteArray {
        var proc: Process? = null
        return try {
            proc = ProcessBuilder(*cmd).start()
            val errBuf = StringBuilder()
            val errThread = Thread {
                try {
                    proc.errorStream.bufferedReader().forEachLine { errBuf.appendLine(it) }
                } catch (_: IOException) {
                }
            }.apply {
                isDaemon = true
                start()
            }
            val outBuf = java.io.ByteArrayOutputStream()
            val outThread = Thread {
                try {
                    proc.inputStream.copyTo(outBuf)
                } catch (_: IOException) {
                }
            }.apply {
                isDaemon = true
                start()
            }
            val finished = proc.waitFor(DEFAULT_EXEC_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            if (!finished) {
                proc.destroyForcibly()
                proc.waitFor(1000, TimeUnit.MILLISECONDS)
                outThread.join(1000)
                errThread.join(1000)
                recordExit(-1)
                return "[stderr]command timed out after ${DEFAULT_EXEC_TIMEOUT_MS}ms".toByteArray()
            }
            outThread.join(5000)
            errThread.join(5000)
            recordExit(proc.exitValue())
            val out = outBuf.toByteArray()
            if (errBuf.isNotEmpty()) {
                out + "\n[stderr]$errBuf".toByteArray()
            } else {
                out
            }
        } catch (e: Exception) {
            proc?.destroyForcibly()
            Log.e(TAG, "exec failed: ${cmd.joinToString(" ")}", e)
            recordExit(-1)
            "[stderr]${e.message}".toByteArray()
        }
    }

    override fun execToFd(cmd: Array<String>, timeoutMs: Long): ParcelFileDescriptor {
        val effectiveTimeout = if (timeoutMs > 0) timeoutMs else DEFAULT_EXEC_TIMEOUT_MS
        val pipe = ParcelFileDescriptor.createPipe()
        val readFd = pipe[0]
        val writeFd = pipe[1]
        Thread({
            var proc: Process? = null
            try {
                ParcelFileDescriptor.AutoCloseOutputStream(writeFd).use { os ->
                    proc = ProcessBuilder(*cmd).start()
                    val errBuf = StringBuilder()
                    val errThread = Thread {
                        try {
                            proc!!.errorStream.bufferedReader().forEachLine { errBuf.appendLine(it) }
                        } catch (_: IOException) {
                        }
                    }.apply {
                        isDaemon = true
                        start()
                    }
                    val copyThread = Thread {
                        try {
                            proc!!.inputStream.copyTo(os)
                            os.flush()
                        } catch (_: IOException) {
                        }
                    }.apply {
                        isDaemon = true
                        start()
                    }
                    val finished = proc!!.waitFor(effectiveTimeout, TimeUnit.MILLISECONDS)
                    if (!finished) {
                        proc!!.destroyForcibly()
                        proc!!.waitFor(1000, TimeUnit.MILLISECONDS)
                        recordExit(-1)
                        Log.w(TAG, "execToFd timed out after ${effectiveTimeout}ms: ${cmd.joinToString(" ")}")
                    } else {
                        copyThread.join(5000)
                        errThread.join(2000)
                        recordExit(proc!!.exitValue())
                    }
                }
            } catch (e: Exception) {
                proc?.destroyForcibly()
                recordExit(-1)
                Log.e(TAG, "execToFd failed: ${cmd.joinToString(" ")}", e)
                try {
                    writeFd.close()
                } catch (_: IOException) {
                }
            }
        }, "lvjiang-shell-fd").apply {
            isDaemon = true
            start()
        }
        return readFd
    }

    override fun lastExitCode(): Int = lastExitByThread.get() ?: lastExit

    override fun destroy() {
        exitProcess(0)
    }

    companion object {
        private const val TAG = "ShellService"
        private const val DEFAULT_EXEC_TIMEOUT_MS = 30_000L
    }
}
