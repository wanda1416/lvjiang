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
import com.google.android.material.tabs.TabLayout
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/** 浏览、编辑对象均为页面状态；选择执行用户和准备任务是明确的写操作。 */
class WorkbenchUi(
    private val activity: AppCompatActivity,
    private val host: LinearLayout,
    private val openTools: (String) -> Unit,
    private val toggleFloat: () -> Unit,
    private val browseUserChanged: (String) -> Unit,
    private val selectedTaskChanged: (String) -> Unit,
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
    private var userSection = "DATA"
    private var loadoutPlan = ""
    private var loadoutScheme = ""
    private var loadoutBase = ""
    private var loadoutRequest = 0
    private var loadoutAssumptions = JSONObject()
    private var taskSection = "SINGLE"
    private var batchGroup = ""
    private var batchData = JSONObject()
    private var batchProgressText: TextView? = null
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
    private var selectedTaskText: TextView? = null
    private var executionUserText: TextView? = null
    private var executionUserCard: MaterialCardView? = null
    private var floatingButton: MaterialButton? = null
    private var floatingHint: TextView? = null
    private var controls: LinearLayout? = null
    private var controlKey = ""
    private val active = setOf("running", "pausing", "paused", "stopping")

    fun restore(bundle: Bundle?) {
        taskSection = bundle?.getString("task_section") ?: "SINGLE"
        batchGroup = bundle?.getString("batch_group").orEmpty()
        taskUser = bundle?.getString("task_user").orEmpty()
        browseUser = bundle?.getString("browse_user").orEmpty()
        userSection = bundle?.getString("user_section") ?: "DATA"
        loadoutPlan = bundle?.getString("loadout_plan").orEmpty()
        loadoutScheme = bundle?.getString("loadout_scheme").orEmpty()
        loadoutBase = bundle?.getString("loadout_base").orEmpty()
        loadoutAssumptions = runCatching { JSONObject(bundle?.getString("loadout_assumptions") ?: "{}") }.getOrDefault(JSONObject())
        query = bundle?.getString("task_query").orEmpty()
        group = bundle?.getString("task_group").orEmpty()
        logsExpanded = bundle?.getBoolean("logs_expanded") ?: false
    }

    fun save(bundle: Bundle) {
        bundle.putString("task_section", taskSection)
        bundle.putString("batch_group", batchGroup)
        bundle.putString("task_user", taskUser)
        bundle.putString("browse_user", browseUser)
        bundle.putString("user_section", userSection)
        bundle.putString("loadout_plan", loadoutPlan)
        bundle.putString("loadout_scheme", loadoutScheme)
        bundle.putString("loadout_base", loadoutBase)
        bundle.putString("loadout_assumptions", loadoutAssumptions.toString())
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
        executionUserText = null; executionUserCard = null; selectedTaskText = null
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
        val selectedTask = FloatService.selectedTaskLabel(activity)
        selectedTaskChanged(selectedTask)
        selectedTaskText?.text = SettingsUi.shortLabel(selectedTask.ifEmpty { "未选择任务" }, 6)
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
        val batch = status.optJSONObject("batch")
        batchProgressText?.text = batchProgressSummary()
        if (batch?.optString("group_id").orEmpty().isNotEmpty()) {
            runtimeMeta?.text = "${batch?.optString("current_entry").orEmpty()} · ${batch?.optString("current_task").orEmpty()}\n已处理 ${batch?.optInt("processed", 0)} / ${batch?.optInt("total", 0)} 项"
            runtimeMeta?.visibility = View.VISIBLE
            runtimeText?.setOnClickListener { batchProgress() }
        } else runtimeText?.setOnClickListener(null)
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
        val subject = if (status.optString("task_id").startsWith("batch:")) "批量" else "任务"
        val key = "$state/$id/$hasTask/$subject"
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
                    control(if (state == "paused") "继续$subject" else "暂停$subject") {
                        action { if (state == "paused") PyBridge.resumeTask(activity) else PyBridge.pauseTask(activity) }
                    }
                }
                control("结束$subject") { action { PyBridge.stopTask(activity) } }.isEnabled = state != "stopping"
            } else {
                button(area, if (subject == "批量") "查看批次结果" else "查看本次结果") {
                    if (subject == "批量" && status.optJSONObject("result")?.optString("batch_run_id").orEmpty().isNotEmpty()) showBatchResult(status.getJSONObject("result").getString("batch_run_id"))
                    else detail("本次结果", status.optString("message") + "\n" + resultSummary(status.optJSONObject("result")))
                }.isEnabled = status.optString("task_id").isNotEmpty()
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
        val taskChoice = choice("选择任务", R.drawable.ic_nav_tasks,
            SettingsUi.shortLabel(FloatService.selectedTaskLabel(activity).ifEmpty { "未选择任务" }, 6)) {
            taskUser = status.optJSONObject("sync")?.optString("execution_username").orEmpty()
            openTools("TASKS")
        }
        selectedTaskText = taskChoice.second
        button(host, "批量执行") { taskSection = "BATCH"; openTools("TASKS") }
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

    fun isBatchPage() = taskSection == "BATCH"
    fun batchGroupId() = batchGroup

    private fun tasks() {
        val tabs = TabLayout(activity).apply { tabMode = TabLayout.MODE_FIXED }
        host.addView(tabs)
        tabs.addTab(tabs.newTab().setText("日常任务"), taskSection == "SINGLE")
        tabs.addTab(tabs.newTab().setText("批量执行"), taskSection == "BATCH")
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) { taskSection = if (tab.position == 0) "SINGLE" else "BATCH"; activity.invalidateOptionsMenu(); show("TASKS") }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
        if (taskSection == "BATCH") { batchPage(); return }
        singleTasks()
    }

    private fun batchPage() {
        val loading = text(host, "正在读取批量配置…")
        request({ PyBridge.batchView(activity, batchGroup) }) { data ->
            host.removeView(loading)
            if (!data.optBoolean("ok")) { text(host, data.optString("message")); if (taskSection == "BATCH") button(host, "新建批量配置") { activity.startActivity(Intent(activity, BatchConfigActivity::class.java)) }; return@request }
            batchData = data; batchGroup = data.optString("group_id")
            val summary = card()
            val readonly = data.optBoolean("readonly") || AgentServer.pcControlsDevice()
            if (readonly) text(summary, if (AgentServer.pcControlsDevice()) "设备由电脑控制，请先结束电脑任务。" else "执行中，结束后可修改本次选择。", 12f)
            button(summary, data.optString("name") + "  ▾") {
                val groups = objects(data.optJSONArray("groups"))
                MaterialAlertDialogBuilder(activity).setTitle("选择配置组").setItems(groups.map { it.optString("name") }.toTypedArray()) { _, which ->
                    batchGroup = groups[which].optString("id"); show("TASKS")
                }.show()
            }.isEnabled = !readonly
            val draft = data.getJSONObject("draft")
            val unitKey = data.optString("unit_key", "user")
            fun selection(entries: Boolean): JSONObject = if (!entries) draft.getJSONObject("tasks") else if (unitKey == "user") draft.getJSONObject("users") else draft.getJSONObject("units").getJSONObject(unitKey)
            fun line(label: String, value: String, action: () -> Unit) {
                val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL; minimumHeight = (56 * resources.displayMetrics.density).toInt() }
                text(row, label, 16f).layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                text(row, value, 14f)
                text(row, "›", 24f).setPadding((16 * activity.resources.displayMetrics.density).toInt(), 0, 0, 0)
                summary.addView(row)
                row.isEnabled = !readonly; row.isClickable = true; row.isFocusable = true
                val attr = android.util.TypedValue(); activity.theme.resolveAttribute(android.R.attr.selectableItemBackground, attr, true); row.setBackgroundResource(attr.resourceId)
                row.setOnClickListener { action() }
            }
            line("任务", "${selection(false).getJSONArray("checked").length()} 项") { batchSelection(false) }
            line(if (unitKey == "user") "用户" else "单元", "${selection(true).getJSONArray("checked").length()} 项") { batchSelection(true) }
            line("执行", "${draft.optInt("rounds", 1)} 轮 · ${if (draft.optBoolean("unattended")) "无人值守" else "有人值守"}") { batchOptions() }
            button(summary, "生命周期") {
                val names = mapOf("batch_setup" to "批次准备", "prepare_item" to "条目准备", "finish_item" to "条目收尾", "batch_teardown" to "批次收尾", "recover_unattended" to "异常恢复")
                val workflows = data.getJSONObject("workflow_names")
                detail("生命周期", names.entries.joinToString("\n") { "${it.value}：${workflows.optString(it.key).ifEmpty { "未设置" }}" } + "\n\n配置组定义在任务页菜单「批量配置」中编辑。")
            }
            if (data.optInt("unavailable_users") > 0 || (data.optJSONArray("unavailable_tasks")?.length() ?: 0) > 0) text(summary, "部分配置项在手机不可用，当前仅展示可执行项。", 12f)
            val hasSelection = selection(false).getJSONArray("checked").length() > 0 && selection(true).getJSONArray("checked").length() > 0
            if (!hasSelection) text(host, "请至少选择一个任务和一个执行单元。", 12f)
            button(host, "准备批量执行", primary = true) {
                request({ FloatService.prepareBatch(activity, batchGroup) }) { result -> toast(result.optString("message")) }
            }.isEnabled = !readonly && hasSelection
            button(host, "恢复默认选择") { request({ PyBridge.saveBatch(activity, batchGroup, "{}", true) }) { result ->
                if (!result.optBoolean("ok")) toast(result.optString("message")) else show("TASKS")
            } }.isEnabled = !readonly
            button(host, "查看当前进度") { batchProgress() }.visibility = if (status.optJSONObject("batch")?.optString("group_id").orEmpty().isNotEmpty()) View.VISIBLE else View.GONE
            val recent = card(); text(recent, "最近批次", 18f)
            request({ PyBridge.batchHistory(activity) }) { history ->
                if (!history.optBoolean("ok")) text(recent, history.optString("message"))
                val records = objects(history.optJSONArray("records"))
                if (records.isEmpty()) text(recent, "还没有手机批量记录")
                records.forEach { record -> recent.addView(SettingsUi.row(activity, record.optString("config_name"), "${mapOf("completed" to "已完成", "failed" to "失败", "stopped" to "已结束", "running" to "运行中")[record.optString("status")] ?: record.optString("status")} · ${record.optString("started_at")}").apply {
                    setOnClickListener { showBatchResult(record.optString("batch_run_id")) }
                }) }
            }
        }
    }

    private fun showBatchResult(runId: String) {
        request({ PyBridge.batchHistory(activity, runId) }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            val content = SettingsUi.dialogContent(activity)
            val record = result.optJSONObject("record") ?: JSONObject()
            val labels = mapOf("completed" to "已完成", "failed" to "失败", "stopped" to "已结束", "running" to "运行中", "skipped" to "已跳过")
            text(content, labels[record.optString("status")] ?: record.optString("status"), 16f)
            if (record.optString("error_message").isNotEmpty()) text(content, record.optString("error_message"))
            objects(result.optJSONArray("tasks")).groupBy { it.optString("username") }.forEach { (user, tasks) ->
                text(content, SettingsUi.userLabel(user), 16f)
                tasks.forEach { task ->
                    content.addView(SettingsUi.row(activity, task.optString("task_name"), "${labels[task.optString("status")] ?: task.optString("status")} · ${task.optLong("duration_ms") / 1000} 秒").apply {
                        setOnClickListener { request({ PyBridge.taskHistory(activity, task.optString("task_run_id")) }) { detail ->
                            if (!detail.optBoolean("ok")) toast(detail.optString("message")) else this@WorkbenchUi.detail(task.optString("task_name"), task.optString("error_message") + "\n" + detail.optString("log"))
                        } }
                    })
                }
            }
            if (result.optString("report").isNotEmpty()) button(content, "查看完整报告") { detail("批次报告", result.optString("report")) }
            MaterialAlertDialogBuilder(activity).setTitle(record.optString("config_name").ifEmpty { "批次结果" }).setView(SettingsUi.dialogScroll(activity, content)).setPositiveButton("关闭", null).show()
        }
    }

    private fun batchProgressSummary(): String {
        val batch = status.optJSONObject("batch") ?: return "尚未启动批量"
        return objects(batch.optJSONArray("rows")).groupBy { it.optString("entry") }.entries.joinToString("\n\n") { group ->
            group.key + "\n" + group.value.joinToString("\n") { "${it.optString("task_name")}    ${it.optString("status")}" }
        }.ifEmpty { status.optString("message") }
    }

    private fun batchProgress() {
        val content = column().apply { val pad = (24 * resources.displayMetrics.density).toInt(); setPadding(pad, 0, pad, pad) }
        batchProgressText = text(content, batchProgressSummary())
        val scroll = ScrollView(activity).apply { addView(content) }
        val dialog = MaterialAlertDialogBuilder(activity).setTitle("批量进度").setView(scroll).setPositiveButton("关闭", null).create()
        dialog.setOnDismissListener { batchProgressText = null }; dialog.show()
    }

    private fun saveBatchDraft(draft: JSONObject) {
        request({ PyBridge.saveBatch(activity, batchGroup, draft.toString()) }) { result ->
            if (!result.optBoolean("ok")) toast(result.optString("message")) else show("TASKS")
        }
    }

    private fun batchOptions() {
        val draft = JSONObject(batchData.getJSONObject("draft").toString())
        val content = column().apply { setPadding(24, 8, 24, 8) }
        val rounds = TextInputEditText(activity).apply { inputType = android.text.InputType.TYPE_CLASS_NUMBER; setText(draft.optInt("rounds", 1).toString()) }
        val wrapper = TextInputLayout(activity).apply { hint = "执行轮数"; addView(rounds) }; content.addView(wrapper)
        val unattended = com.google.android.material.checkbox.MaterialCheckBox(activity).apply {
            text = "无人值守"; isChecked = draft.optBoolean("unattended"); isEnabled = batchData.optBoolean("can_unattended")
        }; content.addView(unattended)
        if (!unattended.isEnabled) text(content, "配置组未设置异常恢复工作流", 12f)
        val dialog = MaterialAlertDialogBuilder(activity).setTitle("执行").setView(content).setNegativeButton("取消", null).setPositiveButton("保存", null).create()
        dialog.setOnShowListener { dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val value = rounds.text.toString().toIntOrNull()
            if (value == null || value !in 1..999) { rounds.error = "请输入 1–999"; return@setOnClickListener }
            draft.put("rounds", value).put("unattended", unattended.isChecked); saveBatchDraft(draft); dialog.dismiss()
        } }; dialog.show()
    }

    private fun batchSelection(entries: Boolean) {
        val draft = JSONObject(batchData.getJSONObject("draft").toString())
        val unitKey = batchData.optString("unit_key", "user")
        val selection = if (!entries) draft.getJSONObject("tasks") else if (unitKey == "user") draft.getJSONObject("users") else draft.getJSONObject("units").getJSONObject(unitKey)
        val items = objects(batchData.optJSONArray(if (entries) "entries" else "tasks")).toMutableList()
        val checked = (0 until selection.getJSONArray("checked").length()).map { selection.getJSONArray("checked").getString(it) }.toMutableSet()
        val content = column().apply { val pad = (16 * resources.displayMetrics.density).toInt(); setPadding(pad, 0, pad, 0) }
        val list = column(); content.addView(list)
        fun render() {
            list.removeAllViews()
            items.forEachIndexed { index, item ->
                val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
                val check = com.google.android.material.checkbox.MaterialCheckBox(activity).apply { text = item.optString("name"); isChecked = item.optString("id") in checked
                    setOnCheckedChangeListener { _, selected -> if (selected) checked.add(item.optString("id")) else checked.remove(item.optString("id")) }
                }; row.addView(check, LinearLayout.LayoutParams(0, -2, 1f))
                if (!entries) button(row, "参数") {
                    chooseUser(taskUser) { user -> taskUser = user; activity.startActivity(Intent(activity, TaskSettingsActivity::class.java).putExtra("username", user).putExtra("task_id", item.optString("id"))) }
                }.apply { minWidth = 0; minimumWidth = 0; setPadding(0, 0, 0, 0); layoutParams = LinearLayout.LayoutParams((48 * resources.displayMetrics.density).toInt(), -2) }
                fun move(delta: Int) { val next = index + delta; if (next in items.indices) { java.util.Collections.swap(items, index, next); render() } }
                // Accessible ordering controls complement drag reordering.
                button(row, "↑") { move(-1) }.apply { isEnabled = index > 0; contentDescription = "上移"; minWidth = 0; minimumWidth = 0; setPadding(0, 0, 0, 0); layoutParams = LinearLayout.LayoutParams((36 * resources.displayMetrics.density).toInt(), -2) }
                button(row, "↓") { move(1) }.apply { isEnabled = index < items.lastIndex; contentDescription = "下移"; minWidth = 0; minimumWidth = 0; setPadding(0, 0, 0, 0); layoutParams = LinearLayout.LayoutParams((36 * resources.displayMetrics.density).toInt(), -2) }
                row.setOnLongClickListener { view -> view.startDragAndDrop(android.content.ClipData.newPlainText("batch-order", item.optString("id")), View.DragShadowBuilder(view), item.optString("id"), 0); true }
                check.setOnLongClickListener { row.startDragAndDrop(android.content.ClipData.newPlainText("batch-order", item.optString("id")), View.DragShadowBuilder(row), item.optString("id"), 0); true }
                row.setOnDragListener { _, event ->
                    if (event.action == android.view.DragEvent.ACTION_DROP) {
                        val from = items.indexOfFirst { it.optString("id") == event.localState as? String }
                        if (from >= 0 && from != index) { val moved = items.removeAt(from); items.add(index, moved); render() }
                    }; true
                }
                list.addView(row)
            }
        }
        render()
        val scroll = ScrollView(activity).apply { addView(content) }
        val builder = MaterialAlertDialogBuilder(activity).setTitle(if (entries) "执行单元" else "任务").setView(scroll).setNegativeButton("取消", null).setPositiveButton("保存") { _, _ ->
            selection.put("order", JSONArray(items.map { it.optString("id") })).put("checked", JSONArray(items.map { it.optString("id") }.filter { it in checked })); saveBatchDraft(draft)
        }
        val sort = batchData.optJSONObject("profile_sort")
        if (entries && sort != null) builder.setNeutralButton("按指定排序") { _, _ ->
            val selectedGroup = batchGroup
            selection.put("order", JSONArray(items.map { it.optString("id") })).put("checked", JSONArray(items.map { it.optString("id") }.filter { it in checked }))
            request({ PyBridge.saveBatch(activity, selectedGroup, draft.toString()) }) { saved ->
                if (!saved.optBoolean("ok")) toast(saved.optString("message")) else request({ PyBridge.sortBatchUsers(activity, selectedGroup) }) { result -> toast(result.optString("message")); if (result.optBoolean("ok")) show("TASKS") }
            }
        }
        val dialog = builder.create()
        dialog.setOnShowListener { if (entries && sort != null) { dialog.getButton(android.app.AlertDialog.BUTTON_NEUTRAL).isEnabled = sort.optBoolean("enabled"); if (!sort.optBoolean("enabled")) toast(sort.optString("label")) } }
        dialog.show()
    }

    private fun singleTasks() {
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
                        summaryMaxLines = if (reason.isEmpty()) 3 else null, indentSummary = true).apply {
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

    fun browsingUsername(): String = browseUser
    fun parameterUsername(): String = taskUser
    fun switchBrowsingUser() {
        chooseUser(browseUser) {
            if (browseUser != it) { loadoutPlan = ""; loadoutScheme = ""; loadoutBase = ""; loadoutAssumptions = JSONObject() }
            browseUser = it; browseUserChanged(it); show("USERS")
        }
    }

    private fun users() {
        if (browseUser.isEmpty()) browseUser = status.optJSONObject("sync")?.optString("execution_username").orEmpty()
        browseUserChanged(browseUser)
        val tabs = TabLayout(activity).apply {
            addTab(newTab().setText("用户数据"))
            addTab(newTab().setText("备战方案"))
            addTab(newTab().setText("战斗属性"))
            getTabAt(when (userSection) { "LOADOUT" -> 1; "COMBAT" -> 2; else -> 0 })?.select()
        }
        host.addView(tabs, LinearLayout.LayoutParams(-1, -2).apply {
            bottomMargin = (12 * activity.resources.displayMetrics.density).toInt()
        })
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                userSection = when (tab.position) { 1 -> "LOADOUT"; 2 -> "COMBAT"; else -> "DATA" }
                show("USERS")
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
        if (browseUser.isEmpty()) { text(host, "选择用户后查看数据和备战。"); return }
        if (userSection != "DATA") { loadouts(); return }
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
                    val row = LinearLayout(activity).apply {
                        orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL
                        val pad = (10 * resources.displayMetrics.density).toInt()
                        setPadding(0, pad, 0, pad)
                        isClickable = true; isFocusable = true
                        val ripple = android.util.TypedValue()
                        activity.theme.resolveAttribute(android.R.attr.selectableItemBackground, ripple, true)
                        setBackgroundResource(ripple.resourceId)
                        setOnClickListener { profileHistory(username, entry, 0) }
                    }
                    section.addView(row)
                    val body = column()
                    row.addView(body, LinearLayout.LayoutParams(0, -2, 1f))
                    val heading = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
                    body.addView(heading)
                    text(heading, entry.optString("label"), 14f).layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                    text(heading, value, 16f).apply { gravity = android.view.Gravity.END }
                        .layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                    val stamp = entry.optString("updated_at")
                    val timestamp = runCatching {
                        val date = runCatching { java.time.OffsetDateTime.parse(stamp).toLocalDateTime() }
                            .getOrElse { java.time.LocalDateTime.parse(stamp) }
                        date.format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"))
                    }.getOrDefault(stamp)
                    if (timestamp.isNotEmpty()) text(body, timestamp, 12f).apply {
                        setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSurfaceVariant))
                    }
                    text(row, "›", 24f).apply { gravity = android.view.Gravity.CENTER }
                        .layoutParams = LinearLayout.LayoutParams((24 * activity.resources.displayMetrics.density).toInt(), -2)
                }
            }
        }
    }

    private fun loadouts() {
        val username = browseUser
        val loading = text(host, "正在读取备战…")
        request({ PyBridge.loadoutPlans(activity, username) }) { data ->
            host.removeView(loading)
            if (!data.optBoolean("ok")) { text(host, data.optString("message")); return@request }
            val plans = objects(data.optJSONArray("plans"))
            if (plans.none { it.optString("id") == loadoutPlan }) {
                loadoutPlan = data.optString("selected")
                loadoutScheme = ""; loadoutBase = ""
            }
            val header = column().also { host.addView(it) }
            val choices = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
            header.addView(choices)
            val planButton = button(choices, "备战：${plans.firstOrNull { it.optString("id") == loadoutPlan }?.optString("name") ?: "请选择"}") {}
            planButton.layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
            planButton.isEnabled = plans.isNotEmpty()
            planButton.setOnClickListener {
                MaterialAlertDialogBuilder(activity).setTitle("选择备战方案")
                    .setSingleChoiceItems(plans.map { it.optString("name") }.toTypedArray(), plans.indexOfFirst { it.optString("id") == loadoutPlan }) { dialog, index ->
                        dialog.dismiss(); loadoutPlan = plans[index].optString("id"); loadoutScheme = ""; loadoutBase = ""; show("USERS")
                    }.setNegativeButton("取消", null).show()
            }
            button(choices, "扫描装备（${data.optInt("equipment_count")}）") { equipmentPool(username, 0) }
            val content = column().also { host.addView(it) }
            if (loadoutPlan.isEmpty()) { text(content, "尚无备战方案，请先扫描或从电脑同步。"); return@request }
            val token = ++loadoutRequest
            text(content, "正在计算毕业率…")
            request({ PyBridge.loadoutView(activity, username, loadoutPlan, loadoutScheme, loadoutBase, loadoutAssumptions.toString()) }) { result ->
                if (token != loadoutRequest) return@request
                content.removeAllViews()
                if (!result.optBoolean("ok")) { text(content, result.optString("message")); return@request }
                if (userSection == "COMBAT") {
                    val groups = objects(result.optJSONArray("combat_cards"))
                    if (groups.isEmpty()) text(content, "请先在备战方案页选择有效的流派和基础属性。")
                    groups.forEach { group ->
                        val attributes = card(content)
                        text(attributes, group.optString("title"), 18f)
                        objects(group.optJSONArray("items")).forEach { item ->
                            val row = LinearLayout(activity).apply {
                                orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL
                                setPadding(0, (6 * resources.displayMetrics.density).toInt(), 0, (6 * resources.displayMetrics.density).toInt())
                            }
                            attributes.addView(row)
                            text(row, item.optString("label"), 13f).layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                            text(row, if (item.optString("raw") == item.optString("effective")) item.optString("raw") else
                                "${item.optString("raw")} → ${item.optString("effective")}", 13f).apply {
                                gravity = android.view.Gravity.END
                            }.layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                        }
                    }
                    return@request
                }
                val summary = card(content)
                val metrics = result.optJSONObject("result")
                val metricsRow = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
                summary.addView(metricsRow)
                text(metricsRow, if (metrics == null) "DPS —" else String.format(java.util.Locale.getDefault(), "DPS %,.0f", metrics.optDouble("dps")), 16f)
                    .layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                text(metricsRow, if (metrics == null) "毕业率暂不可计算" else String.format(java.util.Locale.getDefault(), "毕业率 %.2f%%", metrics.optDouble("graduation_rate") * 100), 16f)
                    .layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                val assumptions = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
                summary.addView(assumptions)
                objects(result.optJSONArray("assumptions")).forEach { option ->
                    assumptions.addView(MaterialButton(activity).apply {
                        text = option.optString("label"); textSize = 12f; isAllCaps = false
                        isCheckable = true; isChecked = option.optBoolean("checked")
                        gravity = android.view.Gravity.CENTER; minWidth = 0
                        setPadding(0, paddingTop, 0, paddingBottom)
                        strokeWidth = if (isChecked) 0 else (1 * resources.displayMetrics.density).toInt()
                        strokeColor = android.content.res.ColorStateList.valueOf(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOutline))
                        backgroundTintList = android.content.res.ColorStateList.valueOf(if (isChecked)
                            MaterialColors.getColor(this, com.google.android.material.R.attr.colorSecondaryContainer) else android.graphics.Color.TRANSPARENT)
                        setTextColor(MaterialColors.getColor(this, if (isChecked) com.google.android.material.R.attr.colorOnSecondaryContainer else com.google.android.material.R.attr.colorOnSurface))
                        addOnCheckedChangeListener { _, checked ->
                            loadoutAssumptions.put(option.optString("key"), checked); show("USERS")
                        }
                    }, LinearLayout.LayoutParams(0, (48 * activity.resources.displayMetrics.density).toInt(), 1f).apply {
                        if (assumptions.childCount > 0) marginStart = (4 * activity.resources.displayMetrics.density).toInt()
                    })
                }
                if (result.optBoolean("contains_mock")) text(summary, "包含模拟装备，结果为模拟毕业率。", 12f)
                val reasons = result.optJSONArray("reasons") ?: JSONArray()
                if (reasons.length() > 0) text(summary, (0 until reasons.length()).joinToString("\n") { reasons.optString(it) })
                val selectors = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
                summary.addView(selectors)
                fun selector(label: String, current: String, values: JSONArray?, selected: (String) -> Unit) {
                    val names = if (values == null) emptyList() else (0 until values.length()).map { values.optString(it) }
                    button(selectors, current.ifEmpty { "选择$label" }) {
                        MaterialAlertDialogBuilder(activity).setTitle(label).setSingleChoiceItems(names.toTypedArray(), names.indexOf(current)) { dialog, index ->
                            dialog.dismiss(); selected(names[index]); show("USERS")
                        }.setNegativeButton("取消", null).show()
                    }.apply {
                        isEnabled = names.isNotEmpty(); textSize = 12f
                        setPadding((6 * resources.displayMetrics.density).toInt(), paddingTop, (6 * resources.displayMetrics.density).toInt(), paddingBottom)
                        setSingleLine(); setAutoSizeTextTypeUniformWithConfiguration(10, 12, 1, android.util.TypedValue.COMPLEX_UNIT_SP)
                        layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
                    }
                }
                selector("计算方案", result.optString("scheme"), result.optJSONArray("schemes")) { loadoutScheme = it }
                selector("基础属性", result.optString("base_attribute"), result.optJSONArray("base_attributes")) { loadoutBase = it }
                if ((result.optJSONArray("schemes")?.length() ?: 0) == 0) text(summary, "请同步适用计算模型。", 12f)
                val equipment = card(content)
                text(equipment, "方案装备", 18f)
                objects(result.optJSONArray("slots")).forEach { slot ->
                    val item = slot.optJSONObject("equipment")
                    val label = slot.optString("label")
                    equipment.addView(SettingsUi.row(activity,
                        if (item == null) label else "$label · ${item.optString("name")}",
                        item?.optString("summary") ?: "尚未记录装备").apply {
                        if (item != null) setOnClickListener {
                            equipmentDetail(item)
                        }
                    })
                }
            }
        }
    }

    private fun equipmentDetail(item: JSONObject) {
        val content = SettingsUi.dialogContent(activity)
        text(content, item.optString("summary"), 13f).apply {
            setTextColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOnSurfaceVariant))
        }
        objects(item.optJSONArray("sections")).forEachIndexed { index, section ->
            if (index > 0) content.addView(View(activity).apply {
                setBackgroundColor(MaterialColors.getColor(this, com.google.android.material.R.attr.colorOutlineVariant))
            }, LinearLayout.LayoutParams(-1, (1 * activity.resources.displayMetrics.density).toInt()).apply {
                topMargin = (4 * activity.resources.displayMetrics.density).toInt()
                bottomMargin = (4 * activity.resources.displayMetrics.density).toInt()
            })
            objects(section.optJSONArray("items")).forEach { field ->
                val row = LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL
                    val pad = (5 * resources.displayMetrics.density).toInt()
                    setPadding(0, pad, 0, pad)
                }
                content.addView(row)
                val note = field.optString("note")
                val label = field.optString("label") + if (note == "转" || note == "原") " [$note]" else ""
                text(row, label, 14f).layoutParams = LinearLayout.LayoutParams(0, -2, 2f)
                text(row, field.optString("value"), 15f).apply { gravity = android.view.Gravity.END }
                    .layoutParams = LinearLayout.LayoutParams(0, -2, 1f)
            }
        }
        val warnings = item.optJSONArray("warnings") ?: JSONArray()
        if (warnings.length() > 0) text(content, (0 until warnings.length()).joinToString("\n") { warnings.optString(it) }, 12f)
        if (item.optString("recorded_at").isNotEmpty()) text(content, "记录于 ${item.optString("recorded_at")}", 12f)
        MaterialAlertDialogBuilder(activity).setTitle(item.optString("name"))
            .setView(content).setPositiveButton("关闭", null).show()
    }

    private fun equipmentPool(username: String, offset: Int) {
        request({ PyBridge.equipmentPool(activity, username, offset) }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            val content = SettingsUi.dialogContent(activity)
            val items = objects(result.optJSONArray("items"))
            if (items.isEmpty()) text(content, "尚无扫描装备，请先扫描或同步装备数据。")
            items.forEach { item ->
                content.addView(SettingsUi.row(activity, item.optString("name"), item.optString("summary")).apply {
                    setOnClickListener { equipmentDetail(item) }
                })
            }
            val dialog = MaterialAlertDialogBuilder(activity).setTitle("扫描装备 · ${offset / 30 + 1} 页")
                .setView(SettingsUi.dialogScroll(activity, content)).setNegativeButton("关闭", null)
            if (offset > 0) dialog.setNeutralButton("上一页") { _, _ -> equipmentPool(username, maxOf(0, offset - 30)) }
            if (offset + items.size < result.optInt("total")) dialog.setPositiveButton("下一页") { _, _ -> equipmentPool(username, offset + 30) }
            dialog.show()
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
        entry(tools, "高级功能", "悬浮控制、电脑连接与屏幕标定") { openTools("ADVANCED") }
        entry(tools, "自检与诊断", "检查环境、复制与分享报告") { openTools("DIAGNOSTICS") }
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
        entry(about, "反馈联系", "查看联系二维码，可手动刷新") { publicContent("qrcode") }
        entry(about, "关于", "律匠 · ${BuildConfig.VERSION_NAME}") { showAbout() }
    }

    private fun showAbout() {
        request({ PyBridge.publicContent(activity, "about") }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            val content = SettingsUi.dialogContent(activity)
            text(content, "${result.optString("name")}  ${result.optString("version")}", 20f).gravity = android.view.Gravity.CENTER
            text(content, result.optString("description"), 16f).gravity = android.view.Gravity.CENTER
            text(content, result.optString("workflow"), 12f).gravity = android.view.Gravity.CENTER
            text(content, result.optString("technology"), 12f).gravity = android.view.Gravity.CENTER
            text(content, result.optString("source_notice"), 16f).gravity = android.view.Gravity.CENTER
            text(content, result.optString("license") + "\n" + result.optString("license_notice"), 12f).gravity = android.view.Gravity.CENTER
            text(content, result.optString("usage_notice") + result.optString("warranty_notice"), 12f)
            val links = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER }
            content.addView(links)
            for ((label, key) in listOf("检查更新" to "releases_url", "GitHub" to "repository_url", "许可证" to "license_url")) {
                button(links, label) { runCatching { activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(result.optString(key)))) }.onFailure { toast("未找到可打开链接的浏览器") } }
            }
            text(content, result.optString("copyright"), 12f).gravity = android.view.Gravity.CENTER
            MaterialAlertDialogBuilder(activity).setTitle("关于律匠").setView(SettingsUi.dialogScroll(activity, content)).setPositiveButton("关闭", null).show()
        }
    }

    private fun publicContent(kind: String, refresh: Boolean = false) {
        toast(if (kind == "qrcode" && !refresh) "正在读取二维码…" else "正在获取最新内容…")
        request({ PyBridge.publicContent(activity, kind, refresh) }) { result ->
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
                MaterialAlertDialogBuilder(activity).setTitle("反馈联系").setView(SettingsUi.dialogScroll(activity, content))
                    .setNeutralButton("刷新二维码") { _, _ -> publicContent("qrcode", true) }
                    .setPositiveButton("关闭", null).show()
            }
        }
    }
}
