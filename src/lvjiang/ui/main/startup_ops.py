"""启动检查链混入类 - 公告 → 版本更新 → 匿名统计同意 → 统计上报 + 在线配置

前四步串行，顺序是有意的：前三步都是模态对话框，只能一个接一个弹；
上报排在最后是因为它没有 UI，不该拖慢前面弹窗的展示时机。每一步无论
成功还是失败都必须推进下一步，任何一环的网络故障都不能卡住启动流程。

在线配置同步和统计上报一样没有 UI，也放在链尾；两者互不依赖，各自起线程
即可，不必再串一层。后台核验包完整性并组合文件，主线程安装并刷新，当前任务持有独立快照。

各 checker/reporter/syncer 都要挂在 self 上防止被 GC（Qt 对象的 Python 端
引用一旦释放，底层线程还没跑完就会被回收）。

依赖主类提供：QMainWindow 自身（作为各对话框的 parent）。
"""


class StartupOpsMixin:
    """启动期检查链混入类"""

    def check_update_on_startup(self):
        """启动时先检查公告，处理完成后再检查版本更新。"""
        from ...core.access import is_readonly
        if is_readonly():
            return  # 启动公告/同意/在线安装的持久化状态归主节点。
        from ...core.announcement import (
            AnnouncementFetchResult,
            applicable_notices,
            cache_manifest,
            get_last_notice_version,
            mark_notice_version,
            should_prompt_manifest,
        )
        from ..notices.announcement_checker import AnnouncementChecker

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
        from ...core.update import should_prompt_update
        from ..background.update import UpdateChecker

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
        """后台获取候选，在主线程提交安装、刷新控件；任务读取独立快照。"""
        from loguru import logger
        from PyQt6.QtCore import QTimer

        from ...core.config.remote import apply_outcome, build_sync_job
        from ...core.config.update_activation import activate_configuration
        from ...core.config.update_observer import InstallationObserver
        from ...core.config.update_store import UpdateStore
        from ..background.remote_config_sync import RemoteConfigSyncer

        running = getattr(self, "_startup_remote_config_syncer", None)
        if running is not None and running.isRunning():
            self._config_sync_again = True
            return
        try:
            job = build_sync_job()
            if job.is_empty:
                return
            observer = getattr(self, "_config_install_observer", None)
            if observer is None:
                observer = InstallationObserver(UpdateStore(job.config, job.app_version, job.baseline_sha256))
                self._config_install_observer = observer
                timer = QTimer(self)
                timer.setInterval(2000)
                def poll():
                    try:
                        def activate(changed):
                            activate_configuration(observer.store.system, changed)
                            self._refresh_installed_configuration(changed)
                        observer.poll(activate)
                    except Exception as error:
                        logger.warning("配置更新感知失败，下次重试: {}", error)
                timer.timeout.connect(poll)
                timer.start()
                self._config_install_timer = timer
        except Exception as error:
            logger.warning("无法初始化配置更新，保留当前配置: {}", error)
            return
        syncer = RemoteConfigSyncer(self, job=job)
        def on_finished_ok(result):
            try:
                changed = apply_outcome(result)
            except Exception as error:
                logger.warning("配置更新未完成，保留可用配置: {}", error)
                return
            observer.accept()
            self._refresh_installed_configuration(changed)
        syncer.finished_ok.connect(on_finished_ok)
        def completed():
            if getattr(self, "_config_sync_again", False):
                self._config_sync_again = False
                self._start_remote_config_sync_on_startup()
        syncer.finished.connect(completed)
        syncer.start()
        self._startup_remote_config_syncer = syncer

    def _refresh_installed_configuration(self, changed: tuple[str, ...]):
        from loguru import logger

        if not changed:
            return
        failed = []
        def refresh(label, callback):
            try:
                callback()
            except Exception as error:
                failed.append(label)
                logger.warning("配置已安装，{}界面刷新失败，不影响其它配置：{}", label, error)
        if any(name.startswith("workflows/") for name in changed):
            # 不重建当前参数编辑区，保留用户尚未保存的草稿及本次批量顺序。
            refresh("任务列表", self._load_workflow_configs)
        if any(name.startswith("references/") for name in changed):
            refresh("参考图库", self._refresh_reference_space_combo)
        if any(name == "layouts.yaml" or name.startswith("layouts/") for name in changed):
            refresh("布局", lambda: self._refresh_layout_combo(self.layout_combo.currentData() or ""))
        if "app.yaml" in changed:
            from ...core.config import load_user_config
            # 替换主页面默认快照，不原地修改运行中的引擎/批量上下文。
            def refresh_app():
                active_hotkeys = self._user_config.hotkeys
                self._user_config = load_user_config()
                self._user_config.hotkeys = active_hotkeys
                self._refresh_plan_combo()
            refresh("运行方案", refresh_app)
        message = ("官方配置已更新；部分界面刷新失败，请查看日志。" if failed else
                   "官方配置已更新；已打开的编辑器保留草稿，保存前请核对新配置。")
        self.statusBar().showMessage(message, 15000)

    def _start_telemetry_report_on_startup(self):
        """启动期的统计上报：心跳（若今天还没发过）+ 积压的调律批次。

        payload 在主线程构造（build_job() 读 UserConfig/i18n/游戏配置等，
        不该在 worker 线程里首次触发懒加载），worker 线程只做 HTTP，
        节流状态与已发批次的清理放回主线程的 finished 槽——工作线程
        绝对不能写 SessionStore，见 core/telemetry/reporter.py 模块 docstring。
        """
        from ...core.telemetry.reporter import (
            apply_outcome,
            build_job,
        )
        from ..background.telemetry import TelemetryReporter

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
