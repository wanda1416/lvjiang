"""启动检查链混入类 - 公告 → 版本更新 → 匿名统计同意 → 统计上报 + 在线配置

前四步串行，顺序是有意的：前三步都是模态对话框，只能一个接一个弹；
上报排在最后是因为它没有 UI，不该拖慢前面弹窗的展示时机。每一步无论
成功还是失败都必须推进下一步，任何一环的网络故障都不能卡住启动流程。

在线配置同步和统计上报一样没有 UI，也放在链尾；两者互不依赖，各自起线程
即可，不必再串一层。它拉下来的配置**下次启动才生效**（不热切换），
理由见 `core/config/remote.py`。

各 checker/reporter/syncer 都要挂在 self 上防止被 GC（Qt 对象的 Python 端
引用一旦释放，底层线程还没跑完就会被回收）。

依赖主类提供：QMainWindow 自身（作为各对话框的 parent）。
"""


class StartupOpsMixin:
    """启动期检查链混入类"""

    def check_update_on_startup(self):
        """启动时先检查公告，处理完成后再检查版本更新。"""
        from ...core.announcement import (
            AnnouncementChecker,
            AnnouncementFetchResult,
            applicable_notices,
            cache_manifest,
            get_last_notice_version,
            mark_notice_version,
            should_prompt_manifest,
        )

        checker = AnnouncementChecker(self)

        def continue_to_update():
            self._start_update_check_on_startup()

        def on_finished(result: AnnouncementFetchResult):
            try:
                manifest = result.manifest
                cache_manifest(manifest, result.etag)
                if should_prompt_manifest(manifest):
                    from ..notices.announcement_dialog import AnnouncementDialog
                    notices = applicable_notices(manifest)
                    dialog = AnnouncementDialog(
                        manifest, notices, self, allow_refresh=False)
                    dialog.exec()
                    # 窗口确实展示并关闭后才推进，避免拉取成功但弹窗失败时吞公告。
                    mark_notice_version(manifest.notice_version)
                elif manifest.notice_version > get_last_notice_version():
                    # 新清单没有覆盖当前客户端，也无需在以后每次启动重复判断。
                    mark_notice_version(manifest.notice_version)
            finally:
                continue_to_update()

        checker.finished.connect(on_finished)
        checker.error.connect(lambda _message: continue_to_update())
        checker.start()
        self._startup_announcement_checker = checker  # 防止被 GC

    def _start_update_check_on_startup(self):
        """公告检查完成后执行原有的静默版本检查，再串上统计的同意提示与上报。"""
        from ...core.update import UpdateChecker, should_prompt_update

        checker = UpdateChecker(self)

        def on_finished(release):
            try:
                if not should_prompt_update(release.version):
                    return  # 用户已选择跳过此版本

                from ..notices.update_dialog import UpdateDialog
                dialog = UpdateDialog(release, self)
                dialog.exec()  # 用户选择"继续使用"时直接关闭对话框
            finally:
                self._continue_after_update_check()

        def on_error(_error_msg: str):
            self._continue_after_update_check()  # 启动时检查失败静默忽略

        checker.finished.connect(on_finished)
        checker.error.connect(on_error)
        checker.start()
        self._startup_update_checker = checker  # 防止被 GC

    def _continue_after_update_check(self):
        """更新检查（成功或失败）之后：首启同意提示 → 启动期统计上报。

        同意提示放在这里而不是更早，是因为它是模态对话框，与公告/更新
        弹窗一样只能串行；上报放最后，因为它没有 UI，用户感知不到，
        也不该拖慢前面两个弹窗的展示时机。
        """
        from ..notices.telemetry_consent_dialog import maybe_prompt_and_record
        maybe_prompt_and_record(self)
        self._start_telemetry_report_on_startup()
        self._start_remote_config_sync_on_startup()

    def _start_remote_config_sync_on_startup(self):
        """启动期拉取在线配置（无 UI，失败静默）。

        job 在主线程构造、状态回写在主线程的槽里做——worker 线程绝对不能
        写 SessionStore，见 core/remote_config_sync.py 模块 docstring。

        拉下来的配置**不热切换**：下载落在暂存层（``config/remote.staging/``），
        由 ``__main__`` 在下次启动早期经 ``promote_pending()`` 提升为生效层。
        光靠约定做不到这点——工作流每次启动都会重新 ``load_layout()``
        （见 run_control.py），会立刻读到写进生效层的新布局，而场景注册表
        只在进程启动时加载一次，半新半旧配在一起出的问题极难查。
        """
        from ...core.config.remote import apply_outcome
        from ...core.remote_config_sync import RemoteConfigSyncer

        syncer = RemoteConfigSyncer(self)

        def on_finished_ok(result):
            apply_outcome(result)

        def on_failed(_message: str):
            pass  # 静默：拿不到在线配置就用出厂配置，本来就是可用状态

        syncer.finished_ok.connect(on_finished_ok)
        syncer.failed.connect(on_failed)
        syncer.start()
        self._startup_remote_config_syncer = syncer  # 防止被 GC

    def _start_telemetry_report_on_startup(self):
        """启动期的统计上报：心跳（若今天还没发过）+ 积压的调律批次。

        payload 在主线程构造（build_job() 读 UserConfig/i18n/游戏配置等，
        不该在 worker 线程里首次触发懒加载），worker 线程只做 HTTP，
        节流状态与已发批次的清理放回主线程的 finished 槽——工作线程
        绝对不能写 SessionStore，见 core/telemetry/reporter.py 模块 docstring。
        """
        from ...core.telemetry.reporter import (
            TelemetryReporter,
            apply_outcome,
            build_job,
        )

        job = build_job()
        if job.is_empty:
            return

        reporter = TelemetryReporter(job, self)

        def on_finished_ok(outcome):
            apply_outcome(outcome)

        def on_failed(_message: str):
            pass  # 静默：统计失败不影响任何用户可见行为

        reporter.finished_ok.connect(on_finished_ok)
        reporter.failed.connect(on_failed)
        reporter.start()
        self._startup_telemetry_reporter = reporter  # 防止被 GC

