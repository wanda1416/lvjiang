package com.lvjiang.app

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.Editable
import android.text.TextWatcher
import android.util.Base64
import android.view.View
import android.widget.*
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.view.ContextThemeWrapper
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.chip.Chip
import com.google.android.material.color.MaterialColors
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/** 浏览、编辑对象均为页面状态；选择执行用户和准备任务是明确的写操作。 */
class WorkbenchUi(
    private val activity: AppCompatActivity,
    private val host: LinearLayout,
    private val openTools: (String) -> Unit,
    private val toggleFloat: () -> Unit,
) {
    private val executor = Executors.newFixedThreadPool(2)
    private val controlExecutor = Executors.newSingleThreadExecutor()
    private var historyRequest = 0
    private var recentHistory: LinearLayout? = null
    private var lastHistoryState = ""
    private var generation = 0
    private var closed = false
    private var page = ""
    private var taskUser = ""
    private var browseUser = ""
    private var query = ""
    private var group = ""
    private var status = JSONObject()
    private var runtimeText: TextView? = null
    private var runtimeMeta: TextView? = null
    private var runtimeMessage: TextView? = null
    private var runtimeStatus: Chip? = null
    private var runtimeLog: TextView? = null
    private var logsButton: Button? = null
    private var logsExpanded = false
    private var executionUserText: TextView? = null
    private var executionUserCard: MaterialCardView? = null
    private var floatingButton: MaterialButton? = null
    private var floatingHint: TextView? = null
    private var controls: LinearLayout? = null
    private var controlKey = ""
    private val active = setOf("running", "pausing", "paused", "stopping")

    fun restore(bundle: Bundle?) {
        taskUser = bundle?.getString("task_user").orEmpty()
        browseUser = bundle?.getString("browse_user").orEmpty()
        query = bundle?.getString("task_query").orEmpty()
        group = bundle?.getString("task_group").orEmpty()
        logsExpanded = bundle?.getBoolean("logs_expanded") ?: false
    }

    fun save(bundle: Bundle) {
        bundle.putString("task_user", taskUser)
        bundle.putString("browse_user", browseUser)
        bundle.putString("task_query", query)
        bundle.putString("task_group", group)
        bundle.putBoolean("logs_expanded", logsExpanded)
    }

    fun close() { closed = true; generation++; executor.shutdownNow(); controlExecutor.shutdown() }
    private fun column() = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
    private fun text(parent: LinearLayout, value: String, size: Float = 14f) =
        SettingsUi.label(activity, value, size).apply {
            if (size >= 18f) setTypeface(null, android.graphics.Typeface.BOLD)
        }.also { parent.addView(it) }
    private fun button(parent: LinearLayout, value: String, primary: Boolean = false, action: () -> Unit) =
        MaterialButton(if (primary) activity else ContextThemeWrapper(activity, R.style.ThemeOverlay_Lvjiang_TextButton)).apply {
            text = value; isAllCaps = false; textSize = 14f
            setOnClickListener { action() }
        }.also {
            parent.addView(it, LinearLayout.LayoutParams(if (primary) -1 else -2, -2).apply {
                topMargin = if (parent.orientation == LinearLayout.HORIZONTAL) 0 else
                    (4 * activity.resources.displayMetrics.density).toInt()
            })
        }
    private fun card(parent: LinearLayout = host): LinearLayout = column().apply {
        val pad = (16 * resources.displayMetrics.density).toInt()
        setPadding(pad, pad, pad, pad)
        val content = this
        parent.addView(MaterialCardView(activity).apply {
            radius = pad.toFloat(); cardElevation = 0f; strokeWidth = 0
            setCardBackgroundColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorSurfaceContainerLow))
            addView(content, FrameLayout.LayoutParams(-1, -2))
        }, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = (12 * resources.displayMetrics.density).toInt() })
    }
    private fun toast(message: String) { Toast.makeText(activity, message, Toast.LENGTH_LONG).show() }
    private fun request(load: () -> JSONObject, control: Boolean = false, done: (JSONObject) -> Unit) {
        val token = generation
        (if (control) controlExecutor else executor).execute {
            val result = try { load() } catch (e: Exception) {
                JSONObject().put("ok", false).put("message", e.message ?: "操作失败")
            }
            activity.runOnUiThread {
                if (!closed && !activity.isDestroyed && token == generation) done(result)
            }
        }
    }
    private fun action(load: () -> JSONObject) = request(load, control = true) { toast(it.optString("message")) }
    private fun objects(array: JSONArray?): List<JSONObject> =
        if (array == null) emptyList() else (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    private fun detail(title: String, body: String) {
        val content = SettingsUi.dialogContent(activity)
        content.addView(SettingsUi.label(activity, body).apply { setTextIsSelectable(true) })
        MaterialAlertDialogBuilder(activity).setTitle(title).setView(SettingsUi.dialogScroll(activity, content))
            .setPositiveButton("关闭", null).show()
    }

    fun show(next: String) {
        generation++
        page = next
        host.removeAllViews()
        runtimeText = null; runtimeLog = null; controls = null; controlKey = ""; recentHistory = null
        runtimeMeta = null; runtimeMessage = null; runtimeStatus = null; logsButton = null
        executionUserText = null; executionUserCard = null
        floatingButton = null; floatingHint = null
        host.visibility = if (next in setOf("HOME", "TASKS", "USERS", "SETTINGS")) View.VISIBLE else View.GONE
        when (next) {
            "HOME" -> home()
            "TASKS" -> tasks()
            "USERS" -> users()
            "SETTINGS" -> settings()
        }
    }

    fun updateStatus(value: JSONObject) {
        status = value
        if (page != "HOME") return
        val state = status.optString("state", "idle")
        val user = status.optString("username").ifEmpty {
            status.optJSONObject("sync")?.optString("execution_username").orEmpty()
        }
        val title = mapOf("idle" to "待开始", "running" to "运行中", "pausing" to "正在暂停", "paused" to "等待操作",
            "stopping" to "正在结束", "done" to "流程结束", "failed" to "执行失败", "stopped" to "已停止")[state] ?: "状态不可用"
        val interaction = status.optJSONObject("interaction")
        val id = interaction?.optString("id").orEmpty()
        val message = if (id.isNotEmpty()) "等待确认" else status.optString("message").lineSequence().firstOrNull().orEmpty()
        val hasTask = status.optString("task_id").isNotEmpty()
        val runtime = status.optString("task_name").ifEmpty { "暂无运行任务" }
        runtimeText?.let { if (it.text.toString() != runtime) it.text = runtime }
        runtimeStatus?.apply {
            text = title
            val background = if (state == "failed") com.google.android.material.R.attr.colorErrorContainer else com.google.android.material.R.attr.colorSecondaryContainer
            val foreground = if (state == "failed") com.google.android.material.R.attr.colorOnErrorContainer else com.google.android.material.R.attr.colorOnSecondaryContainer
            chipBackgroundColor = android.content.res.ColorStateList.valueOf(MaterialColors.getColor(this, background))
            setTextColor(MaterialColors.getColor(this, foreground))
        }
        val elapsed = status.optDouble("elapsed", 0.0).toLong()
        val metadata = if (hasTask) "${SettingsUi.userLabel(user).ifEmpty { "未选择用户" }} · ${elapsed / 60}分${elapsed % 60}秒" else ""
        runtimeMeta?.let { it.text = metadata; it.visibility = if (metadata.isEmpty()) View.GONE else View.VISIBLE }
        runtimeMessage?.let {
            it.text = message.ifEmpty { if (hasTask) "" else "先选择用户和任务，再回到游戏启动。" }
            it.visibility = if (it.text.isEmpty()) View.GONE else View.VISIBLE
        }
        val canSelectUser = !status.optBoolean("busy") && !AgentServer.pcControlsDevice()
        executionUserCard?.isEnabled = canSelectUser
        executionUserText?.text = SettingsUi.userLabel(status.optJSONObject("sync")?.optString("execution_username").orEmpty())
            .ifEmpty { "尚未选择" } + if (canSelectUser) "" else "\n执行中不可切换"
        floatingButton?.apply {
            val started = FloatService.isRunning
            text = if (started) "关闭悬浮窗" else "启动悬浮窗"
            isEnabled = canSelectUser
            val background = if (!canSelectUser) com.google.android.material.R.attr.colorSurfaceContainerHighest
                else if (started) com.google.android.material.R.attr.colorSecondaryContainer else com.google.android.material.R.attr.colorPrimary
            val foreground = if (!canSelectUser) com.google.android.material.R.attr.colorOnSurfaceVariant
                else if (started) com.google.android.material.R.attr.colorOnSecondaryContainer else com.google.android.material.R.attr.colorOnPrimary
            backgroundTintList = android.content.res.ColorStateList.valueOf(MaterialColors.getColor(this, background))
            setTextColor(MaterialColors.getColor(this, foreground))
        }
        floatingHint?.text = when {
            AgentServer.pcControlsDevice() -> "设备正由电脑控制，请在电脑端暂停或结束任务。"
            !canSelectUser -> "执行中请使用上方的暂停或结束操作。"
            !Settings.canDrawOverlays(activity) -> "需要悬浮窗权限，点击后按系统提示授权。"
            FloatService.isRunning -> "悬浮窗已开启，回到游戏后点击图标启动任务。"
            else -> "开启后回到游戏，从悬浮图标启动任务。"
        }
        val logs = status.optJSONArray("logs")
        val logText = if (logs == null || logs.length() == 0) "暂无运行日志" else
            (maxOf(0, logs.length() - 12) until logs.length()).joinToString("\n") { logs.optString(it) }
        runtimeLog?.let { if (it.text.toString() != logText) it.text = logText }
        val hasLogs = logs != null && logs.length() > 0
        runtimeLog?.visibility = if (logsExpanded && hasLogs) View.VISIBLE else View.GONE
        logsButton?.apply {
            visibility = if (hasLogs) View.VISIBLE else View.GONE
            text = if (logsExpanded) "收起日志" else "查看实时日志"
        }
        val historyState = "${status.optLong("log_generation")}/$state"
        if (state in setOf("done", "failed", "stopped") && !status.optBoolean("busy") && lastHistoryState != historyState) {
            lastHistoryState = historyState
            recentHistory?.let { loadHistory(it) }
        }
        val key = "$state/$id/$hasTask"
        if (key == controlKey) return
        controlKey = key
        controls?.let { area ->
            area.removeAllViews()
            area.visibility = if (state in active || hasTask) View.VISIBLE else View.GONE
            if (state in active) {
                val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
                area.addView(row)
                fun control(label: String, action: () -> Unit): Button = button(row, label, action = action).apply {
                    layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                }
                if (id.isNotEmpty() && state != "stopping") {
                    control("确认") { action { PyBridge.respondInteraction(activity, id, true) } }
                    control("拒绝") { action { PyBridge.respondInteraction(activity, id, false) } }
                    text(area, interaction?.optString("message").orEmpty(), 16f)
                } else if (state == "running" || state == "paused") {
                    control(if (state == "paused") "继续任务" else "暂停任务") {
                        action { if (state == "paused") PyBridge.resumeTask(activity) else PyBridge.pauseTask(activity) }
                    }
                }
                control("结束任务") { action { PyBridge.stopTask(activity) } }.isEnabled = state != "stopping"
            } else {
                button(area, "查看本次结果") { detail("本次结果", status.optString("message") + "\n" + resultSummary(status.optJSONObject("result"))) }.isEnabled = status.optString("task_id").isNotEmpty()
            }
        }
    }

    private fun resultSummary(result: JSONObject?): String {
        val summary = listOf("summary", "message", "error").mapNotNull { key ->
            (result?.opt(key) as? String)?.takeIf { it.isNotBlank() }
        }.joinToString("\n")
        return summary.ifEmpty { "任务未提供业务结果摘要，请结合日志和游戏内结果核对。" }
    }

    private fun home() {
        val run = card()
        val heading = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
        run.addView(heading)
        text(heading, "当前任务", 18f).layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        runtimeStatus = Chip(activity).apply {
            text = "待开始"; textSize = 12f; isClickable = false; isCheckable = false; isFocusable = false
            chipStrokeWidth = 0f
            setEnsureMinTouchTargetSize(false)
            chipBackgroundColor = android.content.res.ColorStateList.valueOf(MaterialColors.getColor(this, com.google.android.material.R.attr.colorSecondaryContainer))
            setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSecondaryContainer))
        }.also { heading.addView(it) }
        runtimeText = text(run, "正在读取运行状态…", 20f)
        runtimeMeta = text(run, "").apply { setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSurfaceVariant)) }
        runtimeMessage = text(run, "").apply { setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSurfaceVariant)) }
        controls = column().also { run.addView(it) }
        logsButton = button(run, "查看实时日志") { logsExpanded = !logsExpanded; updateStatus(status) }
        runtimeLog = text(run, "").apply { setTextIsSelectable(true) }
        val choices = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; isBaselineAligned = false }
        host.addView(choices, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = (12 * activity.resources.displayMetrics.density).toInt() })
        fun choice(title: String, icon: Int, description: String, action: () -> Unit): Pair<MaterialCardView, TextView> {
            val tile = MaterialCardView(activity).apply {
                radius = 16 * resources.displayMetrics.density; cardElevation = 0f; strokeWidth = 0
                isClickable = true; isFocusable = true
                setCardBackgroundColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorSurfaceContainerLow))
                setOnClickListener { action() }
            }
            val body = column().apply { val pad = (16 * resources.displayMetrics.density).toInt(); setPadding(pad, pad, pad, pad) }
            body.addView(ImageView(activity).apply {
                setImageResource(icon); imageTintList = android.content.res.ColorStateList.valueOf(MaterialColors.getColor(this, com.google.android.material.R.attr.colorPrimary))
            }, LinearLayout.LayoutParams((24 * activity.resources.displayMetrics.density).toInt(), (24 * activity.resources.displayMetrics.density).toInt()))
            text(body, title, 16f).setTypeface(null, android.graphics.Typeface.BOLD)
            val subtitle = text(body, description).apply { setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSurfaceVariant)) }
            tile.addView(body, FrameLayout.LayoutParams(-1, -2))
            choices.addView(tile, LinearLayout.LayoutParams(0, -1, 1f).apply { if (choices.childCount > 0) marginStart = (12 * activity.resources.displayMetrics.density).toInt() })
            return tile to subtitle
        }
        val userChoice = choice("选择用户", R.drawable.ic_nav_users, "正在读取用户…") { selectExecutionUser() }
        executionUserCard = userChoice.first; executionUserText = userChoice.second
        choice("选择任务", R.drawable.ic_nav_tasks, "浏览任务与准备参数") {
            taskUser = status.optJSONObject("sync")?.optString("execution_username").orEmpty()
            openTools("TASKS")
        }
        floatingButton = button(host, "启动悬浮窗", primary = true) { toggleFloat() }
        floatingHint = text(host, "开启后回到游戏，从悬浮图标启动任务。", 12f).apply {
            setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSurfaceVariant))
            layoutParams = LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = (12 * activity.resources.displayMetrics.density).toInt() }
        }
        val recent = card()
        recentHistory = recent
        lastHistoryState = "${status.optLong("log_generation")}/${status.optString("state")}"
        val recentHeading = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
        recent.addView(recentHeading)
        text(recentHeading, "最新执行", 18f).layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        button(recentHeading, "刷新") { loadHistory(recent) }
        loadHistory(recent)
        updateStatus(status)
    }

    private fun loadHistory(parent: LinearLayout) {
        val requestId = ++historyRequest
        while (parent.childCount > 1) parent.removeViewAt(1)
        val loading = text(parent, "正在读取最近 100 条记录…")
        request({ PyBridge.taskHistory(activity) }) { result ->
            if (requestId != historyRequest) return@request
            parent.removeView(loading)
            if (!result.optBoolean("ok")) { text(parent, result.optString("message")); return@request }
            val records = objects(result.optJSONArray("records"))
            if (records.isEmpty()) text(parent, "还没有手机执行记录")
            records.forEach { record ->
                val state = mapOf("completed" to "流程结束", "failed" to "失败 / 中断", "stopped" to "已停止", "running" to "执行中")[record.optString("status")].orEmpty()
                parent.addView(SettingsUi.row(activity, record.optString("task_name"),
                    "${SettingsUi.userLabel(record.optString("username"))} · $state\n${record.optString("started_at")} · ${record.optLong("duration_ms") / 1000} 秒").apply {
                    setOnClickListener {
                        request({ PyBridge.taskHistory(activity, record.optString("task_run_id")) }) { data ->
                            if (!data.optBoolean("ok")) toast(data.optString("message")) else {
                                val row = data.optJSONObject("record") ?: JSONObject()
                                detail(record.optString("task_name"), "$state · ${SettingsUi.userLabel(row.optString("username"))}\n${row.optString("error_message")}\n${resultSummary(runCatching { JSONObject(data.optString("result")) }.getOrNull())}\n\n${data.optString("log")}")
                            }
                        }
                    }
                })
            }
        }
    }

    private fun chooseUser(current: String = "", selected: (String) -> Unit) {
        request({ PyBridge.listUsers(activity) }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            val array = result.optJSONArray("users") ?: JSONArray()
            val names = (0 until array.length()).map { array.getString(it) }
            if (names.isEmpty()) { toast("请先在用户管理中创建或同步用户"); return@request }
            MaterialAlertDialogBuilder(activity).setTitle("选择用户")
                .setSingleChoiceItems(names.map { SettingsUi.userLabel(it) }.toTypedArray(), names.indexOf(current)) { dialog, index ->
                    dialog.dismiss(); selected(names[index])
                }
                .setNegativeButton("取消", null).show()
        }
    }

    private fun selectExecutionUser() {
        if (status.optBoolean("busy") || AgentServer.pcControlsDevice()) { toast("请先结束当前任务再选择用户"); return }
        chooseUser(status.optJSONObject("sync")?.optString("execution_username").orEmpty()) { username ->
            request({
                PyBridge.selectUser(activity, username).apply {
                    if (optBoolean("ok")) put("status", PyBridge.status(activity))
                }
            }, control = true) { result ->
                toast(result.optString("message"))
                if (result.optBoolean("ok")) taskUser = username
                result.optJSONObject("status")?.let { updateStatus(it) }
            }
        }
    }

    private fun tasks() {
        val header = card()
        text(header, "先准备任务，再回到游戏通过悬浮图标启动。")
        val choices = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        header.addView(choices)
        button(choices, "参数用户：${SettingsUi.userLabel(taskUser).ifEmpty { "请选择" }}") {
            chooseUser(taskUser) { taskUser = it; show("TASKS") }
        }.layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        if (taskUser.isEmpty()) {
            request({ PyBridge.listUsers(activity) }) { data ->
                taskUser = data.optString("selected")
                if (taskUser.isNotEmpty()) show("TASKS") else text(header, "请先创建用户或从 PC 同步用户资料。")
            }
            return
        }
        button(choices, "刷新") { show("TASKS") }
        val loading = text(host, "正在读取任务…")
        request({ PyBridge.taskCatalog(activity, taskUser) }) { result ->
            host.removeView(loading)
            if (!result.optBoolean("ok")) { text(host, result.optString("message")); return@request }
            val all = objects(result.optJSONArray("tasks"))
            val searchBox = TextInputLayout(activity).apply {
                hint = "搜索名称或说明"
                boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_OUTLINE
                val radius = 12 * resources.displayMetrics.density
                setBoxCornerRadii(radius, radius, radius, radius)
                setStartIconDrawable(R.drawable.ic_search)
            }
            val search = TextInputEditText(searchBox.context).apply { setSingleLine(); textSize = 16f; setText(query) }
            searchBox.addView(search, LinearLayout.LayoutParams(-1, -2))
            header.addView(searchBox, LinearLayout.LayoutParams(-1, -2).apply { topMargin = (8 * activity.resources.displayMetrics.density).toInt() })
            val filters = listOf("") + all.map { it.optString("group") }.distinct()
            if (group !in filters) group = ""
            val groupButton = button(header, "分组：${group.ifEmpty { "全部" }}") {}
            val list = column().also { host.addView(it) }
            fun render() {
                list.removeAllViews()
                val found = all.filter { (group.isEmpty() || it.optString("group") == group) &&
                    (it.optString("name") + it.optString("note")).contains(query, ignoreCase = true) }
                if (found.isEmpty()) text(list, "没有符合条件的任务")
                found.forEach { task ->
                    val reason = task.optString("reason")
                    list.addView(SettingsUi.row(activity, task.optString("name"),
                        "${task.optString("group")} · ${if (task.optBoolean("configurable")) "可配置" else "无需参数"}\n${reason.ifEmpty { task.optString("note") }}",
                        summaryMaxLines = if (reason.isEmpty()) 3 else null).apply {
                        setOnClickListener { taskDetail(task) }
                    })
                }
            }
            groupButton.setOnClickListener {
                MaterialAlertDialogBuilder(activity).setTitle("任务分组").setItems(filters.map { it.ifEmpty { "全部" } }.toTypedArray()) { _, index ->
                    group = filters[index]; groupButton.text = "分组：${group.ifEmpty { "全部" }}"; render()
                }.show()
            }
            search.addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { query = s.toString(); render() }
                override fun afterTextChanged(s: Editable?) {}
            })
            render()
        }
    }

    private fun taskDetail(task: JSONObject) {
        val user = taskUser
        var taskDialog: AlertDialog? = null
        val reason = when {
            status.optBoolean("busy") || AgentServer.pcControlsDevice() -> "已有任务在执行，请先结束"
            !A11yBridge.isReady() -> "请先在权限设置中开启无障碍服务"
            !Settings.canDrawOverlays(activity) -> "请先在权限设置中授予悬浮窗权限"
            else -> task.optString("reason")
        }
        val content = SettingsUi.dialogContent(activity)
        text(content, "用户：${SettingsUi.userLabel(user)}\n${task.optString("note")}\n" +
            "参数：${if (task.optString("source") == "user") "用户配置" else "默认配置"}\n${reason.ifEmpty { "已具备准备条件，请确认游戏角色与所选用户一致。" }}")
        if (task.optBoolean("configurable")) button(content, "编辑参数") {
            taskDialog?.dismiss()
            activity.startActivity(Intent(activity, TaskSettingsActivity::class.java).putExtra("username", user).putExtra("task_id", task.optString("id")))
        }
        if (!A11yBridge.isReady() || !Settings.canDrawOverlays(activity)) button(content, "权限设置") { taskDialog?.dismiss(); openTools("PERMISSIONS") }
        val dialog = MaterialAlertDialogBuilder(activity).setTitle(task.optString("name")).setView(SettingsUi.dialogScroll(activity, content))
            .setPositiveButton("准备任务", null).setNegativeButton("关闭", null).create()
        taskDialog = dialog
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).apply {
                isEnabled = reason.isEmpty()
                setOnClickListener {
                    isEnabled = false
                    request({ FloatService.prepareTask(activity, task.optString("id"), task.optString("name"), user) }) {
                        toast(it.optString("message")); dialog.dismiss()
                    }
                }
            }
        }
        dialog.show()
    }

    private fun users() {
        val header = card()
        text(header, "用户资料", 20f)
        text(header, "此处只浏览已保存的数据，切换浏览用户不会改变执行用户。")
        button(header, "用户管理与属性编辑") { activity.startActivity(Intent(activity, UserSettingsActivity::class.java)) }
        val choices = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        header.addView(choices)
        button(choices, "浏览用户：${SettingsUi.userLabel(browseUser).ifEmpty { "请选择" }}") {
            chooseUser(browseUser) { browseUser = it; show("USERS") }
        }.layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
        button(choices, "刷新数据") { show("USERS") }.isEnabled = browseUser.isNotEmpty()
        if (browseUser.isEmpty()) return
        val username = browseUser
        request({ PyBridge.profileView(activity, username) }) { result ->
            if (!result.optBoolean("ok")) { text(host, result.optString("message")); return@request }
            val items = objects(result.optJSONArray("items"))
            if (items.isEmpty()) text(host, "暂无可展示的数据项")
            items.groupBy { "${it.optString("model_label")} · ${it.optString("group")}" }.forEach { (groupName, entries) ->
                val section = card()
                text(section, groupName, 18f)
                entries.forEach { entry ->
                    val value = if (!entry.optBoolean("collected")) "未采集" else entry.optString("value") +
                        if (entry.isNull("cap")) "" else "/${entry.optString("cap") }"
                    section.addView(SettingsUi.row(activity, entry.optString("label"), "$value\n${entry.optString("updated_at")}").apply {
                        setOnClickListener { profileHistory(username, entry, 0) }
                    })
                }
            }
        }
    }

    private fun profileHistory(user: String, entry: JSONObject, offset: Int) {
        request({ PyBridge.profileHistory(activity, user, entry.optString("model"), entry.optString("key"), offset) }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            val rows = objects(result.optJSONArray("records"))
            val body = rows.joinToString("\n\n") { row ->
                val old = if (entry.optString("model") == "note") row.optString("old_value_text") else row.optString("old_value")
                val new = if (entry.optString("model") == "note") row.optString("new_value_text") else row.optString("new_value")
                "${row.optString("ts")}\n$old → $new\n${row.optString("source")} ${row.optString("reason")}".trim()
            }.ifEmpty { "暂无变更记录" }
            val content = SettingsUi.dialogContent(activity)
            content.addView(SettingsUi.label(activity, body).apply { setTextIsSelectable(true) })
            val dialog = MaterialAlertDialogBuilder(activity).setTitle("${entry.optString("label")} · ${offset / 50 + 1} 页")
                .setView(SettingsUi.dialogScroll(activity, content)).setNegativeButton("关闭", null)
            if (offset > 0) dialog.setNeutralButton("上一页") { _, _ -> profileHistory(user, entry, maxOf(0, offset - 50)) }
            if (offset + rows.size < result.optInt("total")) dialog.setPositiveButton("下一页") { _, _ -> profileHistory(user, entry, offset + 50) }
            dialog.show()
        }
    }

    private fun settings() {
        val tools = card()
        text(tools, "运行与维护", 18f)
        fun entry(parent: LinearLayout, title: String, summary: String, action: () -> Unit) {
            parent.addView(SettingsUi.row(activity, title, summary).apply { setOnClickListener { action() } })
        }
        entry(tools, "权限", "无障碍、悬浮窗与通知") { openTools("PERMISSIONS") }
        entry(tools, "运行工具", "悬浮控制、电脑连接与屏幕标定") { openTools("ADVANCED") }
        entry(tools, "运行诊断", "检查环境、复制与分享报告") { openTools("DIAGNOSTICS") }
        val about = card()
        text(about, "关于与反馈", 18f)
        val versionRow = SettingsUi.row(activity, "版本与发布说明", "正在读取版本…")
        about.addView(versionRow)
        versionRow.isEnabled = false
        val version = (versionRow.getChildAt(0) as LinearLayout).getChildAt(1) as TextView
        request({ PyBridge.publicContent(activity, "version") }) { result ->
            version.text = if (result.optBoolean("ok")) "当前版本 ${result.optString("version")}" else result.optString("message")
            if (result.optBoolean("ok")) {
                versionRow.isEnabled = true
                versionRow.setOnClickListener {
                    runCatching { activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(result.optString("url")))) }
                        .onFailure { toast("未找到可打开链接的浏览器") }
                }
            }
        }
        entry(about, "公告", "查看适用于当前版本的公告") { publicContent("notices") }
        entry(about, "反馈联系", "获取最新联系二维码") { publicContent("qrcode") }
        text(host, "独立执行为 Beta。电脑同步会覆盖用户数据库，手机结果暂不自动回传。", 12f)
    }

    private fun publicContent(kind: String) {
        toast("正在获取最新内容…")
        request({ PyBridge.publicContent(activity, kind) }) { result ->
            if (!result.optBoolean("ok")) { toast("刷新失败：${result.optString("message")}"); return@request }
            if (kind == "notices") {
                val notices = objects(result.optJSONArray("notices")).joinToString("\n\n") {
                    "${it.optString("title")}\n${it.optString("published_at")}\n${it.optString("body")}"
                }.ifEmpty { "暂无适用于当前版本的公告" }
                detail("公告", listOf(result.optString("message"), notices).filter { it.isNotBlank() }.joinToString("\n\n"))
            } else {
                val bytes = Base64.decode(result.optString("image"), Base64.DEFAULT)
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                if (bitmap == null) { toast("二维码图片无法读取，请重试"); return@request }
                val content = SettingsUi.dialogContent(activity)
                if (result.optString("message").isNotEmpty()) text(content, result.optString("message"))
                content.addView(ImageView(activity).apply { setImageBitmap(bitmap); adjustViewBounds = true; contentDescription = "反馈联系二维码" })
                MaterialAlertDialogBuilder(activity).setTitle("反馈联系").setView(SettingsUi.dialogScroll(activity, content)).setPositiveButton("关闭", null).show()
            }
        }
    }
}
