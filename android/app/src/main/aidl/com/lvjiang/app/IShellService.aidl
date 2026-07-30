// IShellService.aidl — Shizuku UserService 接口
// 运行在 Shizuku 服务进程（shell 权限），承载 input tap / screencap 等命令
package com.lvjiang.app;

interface IShellService {
    // AIDL 要求：一旦有方法显式编号，所有方法都必须编号
    /** 执行命令并返回 stdout 字节流（stderr 合并输出末尾，前缀 [stderr]） */
    byte[] exec(in String[] cmd) = 1;

    /** 命令退出码（最近一次 exec） */
    int lastExitCode() = 2;

    /** 销毁服务进程 */
    void destroy() = 16777114; // Shizuku 约定的 destroy transaction code
}
