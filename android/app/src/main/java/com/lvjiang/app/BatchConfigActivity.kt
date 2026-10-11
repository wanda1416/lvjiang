package com.lvjiang.app

import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.*
import android.text.Editable
import android.text.TextWatcher
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.OnBackPressedCallback
import com.google.android.material.button.MaterialButton
import com.google.android.material.checkbox.MaterialCheckBox
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/** 编辑配置组定义；活动组和本次执行草稿由工作台独立持有。 */
class BatchConfigActivity : AppCompatActivity() {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var content: LinearLayout
    private var model = JSONObject()
    private var draft = JSONObject()
    private var groupId = ""
    private var token = ""
    private var baseline = ""
    private var busy = false
    private var readonly = false
    private var revision = 0
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    private val poll = object : Runnable {
        override fun run() {
            if (!busy) executor.execute {
                val status = PyBridge.status(this@BatchConfigActivity)
                runOnUiThread { if (!isDestroyed) {
                    val locked = status.optBoolean("busy") || AgentServer.pcControlsDevice()
                    if (locked != readonly) { readonly = locked; render(); applyEnabled(); invalidateOptionsMenu() }
                } }
            }
            handler.postDelayed(this, 1000)
        }
    }
    private val phaseNames = linkedMapOf("batch_setup" to "批次准备", "prepare_item" to "条目准备", "finish_item" to "条目收尾", "batch_teardown" to "批次收尾", "recover_unattended" to "异常恢复")

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        SettingsUi.install(this)
        title = "批量配置"
        content = findViewById(R.id.settings_content)
        findViewById<TextView>(R.id.settings_context).visibility = View.GONE
        findViewById<TextView>(R.id.settings_notice).text = "配置只保存在手机；同步时可选择保留。本次执行选择在任务页调整。"
        groupId = state?.getString("group_id") ?: intent.getStringExtra("group_id").orEmpty()
        if (state?.getString("draft") != null) {
            model = JSONObject(state.getString("model") ?: "{}")
            draft = JSONObject(state.getString("draft") ?: "{}")
            token = state.getString("token").orEmpty(); baseline = state.getString("baseline").orEmpty()
            readonly = state.getBoolean("readonly"); render()
        } else load(groupId)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { discard { finish() } }
        })
    }

    override fun onStart() { super.onStart(); handler.post(poll) }
    override fun onStop() { handler.removeCallbacks(poll); super.onStop() }
    override fun onDestroy() { handler.removeCallbacksAndMessages(null); executor.shutdown(); super.onDestroy() }
    override fun onSaveInstanceState(out: Bundle) {
        out.putString("group_id", groupId); out.putString("model", model.toString()); out.putString("draft", draft.toString())
        out.putString("token", token); out.putString("baseline", baseline); out.putBoolean("readonly", readonly)
        super.onSaveInstanceState(out)
    }
    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, "保存").setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, 2, 1, "新建"); menu.add(0, 3, 2, "删除"); return true
    }
    override fun onPrepareOptionsMenu(menu: Menu): Boolean {
        menu.findItem(1)?.isEnabled = !busy && !readonly && draft.optString("name").trim().isNotEmpty()
        menu.findItem(2)?.isEnabled = !busy && !readonly
        menu.findItem(3)?.isEnabled = !busy && !readonly && groupId.isNotEmpty()
        return super.onPrepareOptionsMenu(menu)
    }
    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            android.R.id.home -> discard { finish() }
            1 -> request({ PyBridge.saveBatchDefinition(this, groupId, token, draft.toString()) }) { result ->
                if (result.optBoolean("ok")) { groupId = result.optString("group_id"); load(groupId) }
                toast(result.optString("message"))
            }
            2 -> discard { groupId = ""; load("") }
            3 -> MaterialAlertDialogBuilder(this).setTitle("删除配置组").setMessage("删除「${model.optJSONObject("definition")?.optString("name").orEmpty()}」及其本次执行选择？任务和用户资料会保留。")
                .setNegativeButton("取消", null).setPositiveButton("删除") { _, _ -> request({ PyBridge.deleteBatchDefinition(this, groupId, token) }) { result ->
                    if (result.optBoolean("ok")) load(""); toast(result.optString("message"))
                } }.show()
            else -> return super.onOptionsItemSelected(item)
        }; return true
    }
    private fun discard(action: () -> Unit) {
        if (busy) { toast("正在处理，请稍候"); return }
        if (draft.toString() == baseline) action() else MaterialAlertDialogBuilder(this).setTitle("放弃未保存的修改？")
            .setNegativeButton("继续编辑", null).setPositiveButton("放弃") { _, _ -> action() }.show()
    }
    private fun request(work: () -> JSONObject, done: (JSONObject) -> Unit) {
        if (busy) return
        busy = true; applyEnabled(); invalidateOptionsMenu(); val current = ++revision
        executor.execute {
            val result = runCatching(work).getOrElse { JSONObject().put("ok", false).put("message", it.message) }
            runOnUiThread { if (!isDestroyed && current == revision) { busy = false; done(result); applyEnabled(); invalidateOptionsMenu() } }
        }
    }
    private fun applyEnabled() {
        fun walk(view: View) {
            view.isEnabled = !busy && !readonly
            if (view is android.view.ViewGroup) for (index in 0 until view.childCount) walk(view.getChildAt(index))
        }
        walk(content)
        findViewById<TextView>(R.id.settings_notice).text = when {
            busy -> "正在处理，请稍候…"
            readonly -> "执行中，结束后可修改配置组。"
            else -> "配置只保存在手机；同步时可选择保留。本次执行选择在任务页调整。"
        }
    }

    private fun load(id: String) {
        request({ PyBridge.batchEditor(this, id) }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            groupId = id; model = result; draft = JSONObject(result.getJSONObject("definition").toString())
            token = result.optString("token"); baseline = draft.toString()
            readonly = result.optBoolean("readonly") || AgentServer.pcControlsDevice(); render()
        }
    }
    private fun toast(value: String) { Toast.makeText(this, value, Toast.LENGTH_SHORT).show() }
    private fun objects(values: JSONArray?): List<JSONObject> = if (values == null) emptyList() else (0 until values.length()).map { values.getJSONObject(it) }
    private fun strings(values: JSONArray?): List<String> = if (values == null) emptyList() else (0 until values.length()).map { values.getString(it) }
    private fun label(parent: LinearLayout, value: String, size: Float = 14f) = SettingsUi.label(this, value, size).also { parent.addView(it) }
    private fun row(parent: LinearLayout, title: String, value: String, action: () -> Unit) {
        parent.addView(SettingsUi.row(this, title, value).apply { isEnabled = !readonly && !busy; setOnClickListener { action() } })
    }
    private fun toggle(parent: LinearLayout, title: String, value: Boolean, changed: (Boolean) -> Unit) {
        parent.addView(MaterialCheckBox(this).apply { text = title; isChecked = value; isEnabled = !readonly; setOnCheckedChangeListener { _, checked -> changed(checked) } })
    }
    private fun picker(title: String, choices: List<JSONObject>, chosen: (String) -> Unit) {
        MaterialAlertDialogBuilder(this).setTitle(title).setItems(choices.map { it.optString("name") }.toTypedArray()) { _, index -> chosen(choices[index].getString("id")); render() }.setNegativeButton("取消", null).show()
    }
    private fun nameOf(array: String, key: String): String = objects(model.optJSONArray(array)).firstOrNull { it.optString("id") == key }?.optString("name") ?: if (key.isEmpty()) "未设置" else "当前项不可用"
    private fun render() {
        content.removeAllViews()
        if (readonly) label(content, "执行中，结束后可修改配置组。")
        row(content, "编辑配置组", draft.optString("name").ifEmpty { "新建配置组" }) {
            discard { picker("选择编辑配置组", objects(model.optJSONArray("groups"))) { load(it) } }
        }
        row(content, "名称", draft.optString("name").ifEmpty { "请输入名称" }) { input("名称", draft.optString("name"), false) { draft.put("name", it); render() } }
        row(content, "任务", "${strings(draft.optJSONArray("task_ids")).size} 项可见 · ${strings(draft.optJSONArray("default_task_ids")).size} 项默认") { definitions(false) }
        row(content, "用户", "${strings(draft.optJSONArray("usernames")).size} 人可见 · ${strings(draft.optJSONArray("default_usernames")).size} 人默认") { definitions(true) }
        val unit = draft.optString("execution_unit_key", "user")
        row(content, "调度单元", nameOf("unit_keys", unit)) { picker("调度单元", objects(model.optJSONArray("unit_keys"))) { draft.put("execution_unit_key", it) } }
        if (unit != "user") row(content, "默认单元", "选择首次加载时勾选的单元") { unitDefaults(unit) }
        if (unit == "user") {
            row(content, "指定排序", nameOf("profile_keys", draft.optString("profile_sort_key"))) {
                picker("指定排序", listOf(JSONObject().put("id", "").put("name", "不指定")) + objects(model.optJSONArray("profile_keys"))) { draft.put("profile_sort_key", it) }
            }
            row(content, "排序方向", if (draft.optString("profile_sort_direction") == "desc") "降序" else "升序") {
                picker("排序方向", listOf(JSONObject().put("id", "asc").put("name", "升序"), JSONObject().put("id", "desc").put("name", "降序"))) { draft.put("profile_sort_direction", it) }
            }
        }
        val workflows = draft.getJSONObject("workflows")
        for ((phase, title) in phaseNames) {
            row(content, title, nameOf("workflows", workflows.optString(phase))) {
                picker(title, listOf(JSONObject().put("id", "").put("name", "不设置")) + objects(model.optJSONArray("workflows"))) { workflows.put(phase, it); draft.getJSONObject("workflow_params").remove(phase) }
            }
            if (workflows.optString(phase).isNotEmpty()) row(content, "$title 参数", "查看与修改") { parameters(phase) }
        }
        if (unit == "user") toggle(content, "单个用户时跳过生命周期", draft.optBoolean("skip_lifecycle_for_single_item", true)) { draft.put("skip_lifecycle_for_single_item", it) }
        label(content, "属性单元始终执行生命周期。保存定义不会改变活动组或本次执行顺序。", 12f)
    }
    private fun input(title: String, value: String, number: Boolean, changed: (String) -> Unit) {
        val body = SettingsUi.dialogContent(this)
        val box = TextInputLayout(this).apply { hint = title }
        val edit = TextInputEditText(this).apply { setText(value); inputType = if (number) android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_SIGNED else android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE }
        box.addView(edit); body.addView(box)
        MaterialAlertDialogBuilder(this).setTitle(title).setView(body).setNegativeButton("取消", null).setPositiveButton("确定") { _, _ -> changed(edit.text.toString()) }.show()
    }
    private fun definitions(users: Boolean) {
        val orderKey = if (users) "usernames" else "task_ids"
        val defaultKey = if (users) "default_usernames" else "default_task_ids"
        val visible = strings(draft.optJSONArray(orderKey)).toMutableSet(); val defaults = strings(draft.optJSONArray(defaultKey)).toMutableSet()
        val known = objects(model.optJSONArray(if (users) "users" else "tasks"))
        val ids = strings(draft.optJSONArray(orderKey)) + known.map { it.optString("id") }.filter { it !in visible }
        val items = ids.map { id -> known.firstOrNull { it.optString("id") == id } ?: JSONObject().put("id", id).put("name", if (users) id else "不可用任务") }.toMutableList()
        val body = SettingsUi.dialogContent(this); label(body, "可见项按列表顺序展示；默认勾选仅用于首次加载或恢复默认。", 12f)
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }; body.addView(list)
        fun draw() {
            list.removeAllViews()
            items.forEachIndexed { index, item ->
                val id = item.optString("id")
                val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
                val shown = MaterialCheckBox(this).apply { text = item.optString("name"); isChecked = id in visible }
                row.addView(shown, LinearLayout.LayoutParams(0, -2, 1f))
                val selected = MaterialCheckBox(this).apply { text = "默认"; isChecked = id in defaults; isEnabled = id in visible
                    setOnCheckedChangeListener { _, checked -> if (checked) defaults.add(id) else defaults.remove(id) }
                }; row.addView(selected)
                shown.setOnCheckedChangeListener { _, checked -> if (checked) visible.add(id) else { visible.remove(id); defaults.remove(id); selected.isChecked = false }; selected.isEnabled = checked }
                for ((arrow, delta) in listOf("↑" to -1, "↓" to 1)) row.addView(MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
                    text = arrow; minWidth = 0; minimumWidth = 0; setPadding(0, 0, 0, 0); layoutParams = LinearLayout.LayoutParams((36 * resources.displayMetrics.density).toInt(), -2)
                    isEnabled = index + delta in items.indices; setOnClickListener { java.util.Collections.swap(items, index, index + delta); draw() }
                })
                shown.setOnLongClickListener { row.startDragAndDrop(android.content.ClipData.newPlainText("batch-definition", id), View.DragShadowBuilder(row), id, 0); true }
                row.setOnDragListener { _, event -> if (event.action == android.view.DragEvent.ACTION_DROP) { val from = items.indexOfFirst { it.optString("id") == event.localState as? String }; if (from >= 0 && from != index) { items.add(index, items.removeAt(from)); draw() } }; true }
                list.addView(row)
            }
        }; draw()
        MaterialAlertDialogBuilder(this).setTitle(if (users) "初始用户" else "初始任务").setView(SettingsUi.dialogScroll(this, body)).setNegativeButton("取消", null).setPositiveButton("确定") { _, _ ->
            val ids = items.map { it.optString("id") }; draft.put(orderKey, JSONArray(ids.filter { it in visible })).put(defaultKey, JSONArray(ids.filter { it in defaults && it in visible })); render()
        }.show()
    }
    private fun unitDefaults(unit: String) {
        val names = strings(draft.optJSONArray("usernames")).toSet()
        val values = objects(model.optJSONArray("users")).filter { it.optString("id") in names }.map { it.optJSONObject("attributes")?.optString(unit).orEmpty() }.filter { it.isNotEmpty() }.distinct()
        val defaults = draft.getJSONObject("default_units")
        val checked = strings(defaults.optJSONArray(unit)).takeIf { defaults.has(unit) } ?: values
        val flags = values.map { it in checked }.toBooleanArray()
        MaterialAlertDialogBuilder(this).setTitle("默认单元").setMultiChoiceItems(values.toTypedArray(), flags) { _, index, selected -> flags[index] = selected }
            .setNegativeButton("取消", null).setPositiveButton("确定") { _, _ -> defaults.put(unit, JSONArray(values.filterIndexed { index, _ -> flags[index] })); render() }.show()
    }
    private fun parameters(phase: String) {
        val original = draft.getJSONObject("workflow_params")
        val values = JSONObject(original.toString())
        request({ PyBridge.batchLifecycle(this, draft.getJSONObject("workflows").toString(), values.toString()) }) { result ->
            if (!result.optBoolean("ok")) { toast(result.optString("message")); return@request }
            val phaseView = result.getJSONObject("phases").getJSONObject(phase)
            val params = JSONObject(phaseView.getJSONObject("values").toString())
            val body = SettingsUi.dialogContent(this)
            val rows = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }; body.addView(rows)
            val dialog = MaterialAlertDialogBuilder(this).setTitle(phaseNames[phase]).setView(SettingsUi.dialogScroll(this, body)).setNegativeButton("取消", null).setPositiveButton("确定") { _, _ -> original.put(phase, params); render() }.create()
            var definitions = objects(phaseView.getJSONArray("definitions")); var pending: Runnable? = null
            fun draw() {
                rows.removeAllViews()
                if (definitions.isEmpty()) label(rows, "没有可配置参数")
                fun update() {
                    values.put(phase, params)
                    pending?.let { rows.removeCallbacks(it) }
                    val action = Runnable { request({ PyBridge.batchLifecycle(this, draft.getJSONObject("workflows").toString(), values.toString()) }) { preview ->
                        if (dialog.isShowing && preview.optBoolean("ok")) {
                            val fresh = objects(preview.getJSONObject("phases").getJSONObject(phase).getJSONArray("definitions"))
                            if (fresh.map { it.optString("name") } != definitions.map { it.optString("name") }) { definitions = fresh; draw() }
                        }
                    } }; pending = action; rows.postDelayed(action, 250)
                }
                for (definition in definitions) {
                    val name = definition.optString("name"); val heading = definition.optString("label", name)
                    when (definition.optString("type", "select")) {
                        "bool" -> toggle(rows, heading, params.optBoolean(name)) { params.put(name, it); update() }
                        "number", "text" -> {
                            val number = definition.optString("type") == "number"
                            val box = TextInputLayout(this).apply { hint = heading; if (number) helperText = "范围：${definition.optInt("min", 0)}～${definition.optInt("max", 999999)}" }
                            val edit = TextInputEditText(this).apply { setText(params.opt(name)?.toString().orEmpty()); setSingleLine(!definition.optBoolean("multiline")); inputType = if (number) android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_SIGNED else android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE }
                            box.addView(edit); rows.addView(box)
                            edit.addTextChangedListener(object : TextWatcher {
                                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                                override fun afterTextChanged(s: Editable?) {}
                                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { params.put(name, s?.toString().orEmpty()); update() }
                            })
                        }
                        else -> {
                            val options = definition.optJSONArray("options") ?: JSONArray()
                            val pairs = (0 until options.length()).map { index -> val value = options.get(index); if (value is JSONObject) value.optString("value") to value.optString("label", value.optString("value")) else value.toString() to value.toString() }
                            val multiple = definition.optString("type") == "checkgroup"
                            row(rows, heading, if (multiple) "选择项目" else pairs.firstOrNull { it.first == params.optString(name) }?.second ?: "请选择") {
                                if (multiple) {
                                    val flags = pairs.map { params.optJSONObject(name)?.optBoolean(it.first) == true }.toBooleanArray()
                                    MaterialAlertDialogBuilder(this).setTitle(heading).setMultiChoiceItems(pairs.map { it.second }.toTypedArray(), flags) { _, index, selected -> flags[index] = selected }.setPositiveButton("确定") { _, _ -> params.put(name, JSONObject().apply { pairs.forEachIndexed { index, pair -> put(pair.first, flags[index]) } }); update(); draw() }.setNegativeButton("取消", null).show()
                                } else MaterialAlertDialogBuilder(this).setTitle(heading).setItems(pairs.map { it.second }.toTypedArray()) { _, index -> params.put(name, pairs[index].first); update(); draw() }.show()
                            }
                        }
                    }
                }
            }; draw(); dialog.setOnDismissListener { pending?.let { rows.removeCallbacks(it) } }; dialog.show()
        }
    }
}
