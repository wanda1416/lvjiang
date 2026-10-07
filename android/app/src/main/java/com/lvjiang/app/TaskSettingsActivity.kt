package com.lvjiang.app

import android.os.Bundle
import android.content.res.Configuration
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/** 标准列表/详情编辑器。编辑用户不是执行用户，所有子页共享同一份未保存草稿。 */
class TaskSettingsActivity : AppCompatActivity() {
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var content: LinearLayout
    private var username = ""
    private var taskId = ""
    private var page = "list"
    private var viewModel = JSONObject()
    private var draft = JSONObject()
    private var baseline = ""
    private var readonly = false
    private var busy = false
    private var revision = 0
    private var previewPending: Runnable? = null
    private val fields = mutableMapOf<String, View>()
    private val inputs = mutableMapOf<String, TextInputLayout>()
    private var users = emptyList<String>()
    private val poll = object : Runnable {
        override fun run() {
            if (isFinishing || isDestroyed) return
            executor.execute {
                val status = PyBridge.status(this@TaskSettingsActivity)
                val locked = status.optString("state") in setOf("running", "pausing", "paused", "stopping") ||
                    AgentServer.pcControlsDevice()
                runOnUiThread {
                    if (!isDestroyed) {
                        readonly = locked
                        applyReadOnly()
                    }
                }
            }
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_task_settings)
        val root = findViewById<View>(R.id.settings_root)
        // SDK 35 强制 edge-to-edge；边距来自系统，旋转、刘海和键盘均重新计算。
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, keyboard))
            WindowInsetsCompat.CONSUMED
        }
        val light = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK != Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(window, root).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
        ViewCompat.requestApplyInsets(root)
        setSupportActionBar(findViewById(R.id.settings_toolbar))
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        content = findViewById(R.id.settings_content)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { back() }
        })
        username = savedInstanceState?.getString("username") ?: intent.getStringExtra("username").orEmpty()
        taskId = savedInstanceState?.getString("task_id") ?: intent.getStringExtra("task_id").orEmpty()
        page = savedInstanceState?.getString("page") ?: if (taskId.isEmpty()) "list" else "detail"
        val restored = savedInstanceState?.getString("model")
        if (restored != null && restored != "{}") {
            viewModel = JSONObject(restored)
            draft = JSONObject(savedInstanceState.getString("draft") ?: "{}")
            baseline = savedInstanceState.getString("baseline").orEmpty()
            readonly = savedInstanceState.getBoolean("readonly", true)
            render()
        }
        request({ PyBridge.listUsers(this) }) { result ->
            if (!result.optBoolean("ok")) return@request showError(result)
            users = strings(result.optJSONArray("users"))
            if (username.isEmpty()) username = result.optString("selected")
            if (username.isEmpty()) username = users.firstOrNull().orEmpty()
            if (restored == null || restored == "{}") {
                if (page == "list") loadList() else loadDetail()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        handler.post(poll)
    }

    override fun onStop() {
        handler.removeCallbacks(poll)
        super.onStop()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        executor.shutdown()
        super.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("username", username)
        outState.putString("task_id", taskId)
        outState.putString("page", page)
        outState.putString("model", viewModel.toString())
        outState.putString("draft", draft.toString())
        outState.putString("baseline", baseline)
        outState.putBoolean("readonly", readonly)
        super.onSaveInstanceState(outState)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        if (page != "list") {
            menu.add(0, 1, 0, "保存").apply {
                setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
                isEnabled = !busy && !readonly && dirty()
            }
            menu.add(0, 2, 1, "重新加载").isEnabled = !busy
            if (viewModel.optString("kind") == "parameters") {
                menu.add(0, 3, 2, "恢复通用配置").isEnabled = !busy && !readonly && viewModel.optString("source") == "user"
            }
        }
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            android.R.id.home -> back()
            1 -> save()
            2 -> leaveDraft { loadDetail() }
            3 -> AlertDialog.Builder(this).setTitle("恢复通用配置？")
                .setMessage("将删除该用户对本任务的独立参数，并放弃未保存修改。不影响其他用户。")
                .setPositiveButton("恢复") { _, _ -> save(reset = true) }
                .setNegativeButton("保留配置", null).show()
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun request(work: () -> JSONObject, done: (JSONObject) -> Unit) {
        busy = true
        applyReadOnly()
        executor.execute {
            val result = work()
            runOnUiThread {
                if (!isDestroyed && !isFinishing) {
                    busy = false
                    done(result)
                    applyReadOnly()
                }
            }
        }
    }

    private fun loadList() {
        page = "list"
        taskId = ""
        viewModel = JSONObject()
        content.removeAllViews()
        title = "任务设置"
        contextLine()
        if (username.isEmpty()) {
            content.addView(label("暂无用户，请先从 PC 同步配置。"))
            return
        }
        request({ PyBridge.listTaskSettings(this, username) }) { result ->
            if (!result.optBoolean("ok")) {
                content.addView(label(result.optString("message")))
            } else {
                readonly = result.optBoolean("readonly") || AgentServer.pcControlsDevice()
                content.removeAllViews()
                row("配置用户", username) { chooseUser() }
                val tasks = result.optJSONArray("tasks") ?: JSONArray()
                if (tasks.length() == 0) content.addView(label("没有可配置参数的任务。"))
                objects(tasks).forEach { task ->
                    if (task.has("error")) {
                        content.addView(settingRow(task.optString("name"), task.optString("error")).apply { isEnabled = false })
                        return@forEach
                    }
                    row(task.optString("name"), if (task.optBoolean("dedicated")) "用户专属配置"
                        else if (task.optString("source") == "user") "用户独立配置" else "使用通用配置") {
                        if (!busy) {
                            taskId = task.optString("id")
                            page = "detail"
                            loadDetail()
                        }
                    }
                }
                applyReadOnly()
            }
        }
    }

    private fun chooseUser() {
        if (busy) return
        AlertDialog.Builder(this).setTitle("选择配置用户")
            .setSingleChoiceItems(users.toTypedArray(), users.indexOf(username)) { dialog, index ->
                username = users[index]
                dialog.dismiss()
                loadList()
            }.setNegativeButton("返回", null).show()
    }

    private fun loadDetail() {
        revision++
        viewModel = JSONObject()
        draft = JSONObject()
        baseline = ""
        val user = username
        val id = taskId
        content.removeAllViews()
        content.addView(label("正在加载参数…"))
        request({ PyBridge.getTaskSettings(this, user, id) }) { result ->
            if (!result.optBoolean("ok")) {
                content.removeAllViews()
                content.addView(label(result.optString("message")))
            } else {
                viewModel = result
                draft = JSONObject(result.optJSONObject("values")?.toString() ?: "{}")
                baseline = canonical(draft)
                readonly = result.optBoolean("readonly") || AgentServer.pcControlsDevice()
                page = "detail"
                render()
            }
        }
    }

    private fun render() {
        content.removeAllViews()
        fields.clear()
        inputs.clear()
        title = when (page) {
            "base" -> "基础规则"
            "slots" -> "调律部位"
            "rules" -> "调律规则"
            "options" -> "执行选项"
            else -> viewModel.optString("name", "任务设置")
        }
        contextLine()
        if (viewModel.optString("kind") == "tuning") renderTuning() else renderParameters()
        findViewById<ScrollView>(R.id.settings_scroll).scrollTo(0, 0)
        applyReadOnly()
        invalidateOptionsMenu()
    }

    private fun renderParameters() {
        if (viewModel.optJSONArray("definitions")?.length() == 0) {
            content.addView(label("该任务没有可配置参数，请返回选择其他任务。"))
            return
        }
        if (viewModel.optString("note").isNotEmpty()) content.addView(label(viewModel.optString("note")))
        objects(viewModel.optJSONArray("definitions")).forEach { def ->
            val name = def.optString("name")
            val heading = def.optString("label", name)
            val value = draft.opt(name)
            val field = when (def.optString("type", "select")) {
                "bool" -> toggle(heading, value == true) { draft.put(name, it); changed() }
                "number", "text" -> {
                    val number = def.optString("type") == "number"
                    val box = TextInputLayout(this).apply {
                        hint = heading
                        defaultHintTextColor = ContextCompat.getColorStateList(context, R.color.settings_secondary_text)
                        setHelperTextColor(ContextCompat.getColorStateList(context, R.color.settings_secondary_text))
                        if (number) helperText = "范围：${def.optInt("min", 0)}～${def.optInt("max", 999999)}"
                        layoutParams = LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(12) }
                    }
                    val edit = TextInputEditText(box.context).apply {
                        setTextColor(ContextCompat.getColorStateList(context, R.color.settings_primary_text))
                        inputType = if (number) InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_SIGNED
                            else InputType.TYPE_CLASS_TEXT or if (def.optBoolean("multiline")) InputType.TYPE_TEXT_FLAG_MULTI_LINE else 0
                        setSingleLine(number || !def.optBoolean("multiline"))
                        hint = def.optString("placeholder")
                        setText(if (value == null || value == JSONObject.NULL) "" else value.toString())
                        tag = "editable"
                    }
                    box.addView(edit)
                    edit.addTextChangedListener(object : TextWatcher {
                        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                            draft.put(name, s?.toString().orEmpty())
                            box.error = null
                            changed()
                        }
                        override fun afterTextChanged(s: Editable?) {}
                    })
                    inputs[name] = box
                    box
                }
                else -> {
                    val options = objectsOrStrings(def.optJSONArray("options"))
                    val multiple = def.optString("type") == "checkgroup"
                    val summary = if (multiple) "已选 ${options.count { draft.optJSONObject(name)?.optBoolean(it.first) == true }} 项"
                        else options.firstOrNull { it.first == draft.optString(name) }?.second ?: "请选择"
                    settingRow(heading, summary).apply {
                        tag = "editable"
                        setOnClickListener {
                            if (readonly || busy) return@setOnClickListener
                            if (multiple) multiChoice(heading, options, options.map {
                                draft.optJSONObject(name)?.optBoolean(it.first) == true
                            }.toBooleanArray()) { flags ->
                                draft.put(name, JSONObject().apply { options.forEachIndexed { i, option -> put(option.first, flags[i]) } })
                                changed(); render()
                            } else AlertDialog.Builder(this@TaskSettingsActivity).setTitle(heading)
                                .setSingleChoiceItems(options.map { it.second }.toTypedArray(), options.indexOfFirst { it.first == draft.optString(name) }) { dialog, index ->
                                    draft.put(name, options[index].first)
                                    dialog.dismiss()
                                    changed(); render()
                                }.setNegativeButton("返回", null).show()
                        }
                    }
                }
            }
            fields[name] = field
            content.addView(field)
        }
        preview()
    }

    private fun renderTuning() {
        val schema = viewModel.getJSONObject("schema")
        when (page) {
            "detail" -> {
                row("基础规则", objects(schema.optJSONArray("base_groups")).firstOrNull {
                    it.optString("key") == draft.optString("base_group")
                }?.optString("name") ?: "请选择") { navigate("base") }
                row("调律部位", "已选 ${draft.optJSONArray("selected_slots")?.length() ?: 0} 个部位") { navigate("slots") }
                row("调律规则", "已启用 ${objects(schema.optJSONArray("rules")).count {
                    draft.optJSONObject("rules")?.optJSONObject(it.optString("key"))?.optBoolean("enabled") == true
                }} 项") { navigate("rules") }
                row("执行选项", "配置保留与判断选项") { navigate("options") }
            }
            "base" -> objects(schema.optJSONArray("base_groups")).forEach { group ->
                val radio = androidx.appcompat.widget.AppCompatRadioButton(this).apply {
                    setTextColor(ContextCompat.getColorStateList(context, R.color.settings_primary_text))
                    text = group.optString("name")
                    isChecked = group.optString("key") == draft.optString("base_group")
                    minHeight = dp(56)
                    tag = "editable"
                    setOnClickListener { draft.put("base_group", group.optString("key")); changed(); render() }
                }
                content.addView(radio)
                if (group.optString("description").isNotEmpty()) content.addView(label(group.optString("description")))
            }
            "slots" -> objects(schema.optJSONArray("slot_groups")).forEach { group ->
                content.addView(label(group.optString("name")))
                objects(group.optJSONArray("slots")).forEach { slot ->
                    val key = slot.optString("key")
                    val locked = slot.optBoolean("locked")
                    val check = androidx.appcompat.widget.AppCompatCheckBox(this).apply {
                        setTextColor(ContextCompat.getColorStateList(context, R.color.settings_primary_text))
                        text = slot.optString("label") + if (locked) "（无需单独调律）" else ""
                        isChecked = strings(draft.optJSONArray("selected_slots")).contains(key)
                        minHeight = dp(56)
                        tag = if (locked) "locked" else "editable"
                        setOnCheckedChangeListener { _, selected ->
                            val values = strings(draft.optJSONArray("selected_slots")).toMutableList()
                            if (selected && key !in values) values.add(key) else if (!selected) values.remove(key)
                            draft.put("selected_slots", JSONArray(values)); changed()
                        }
                    }
                    content.addView(check)
                }
            }
            "rules" -> objects(schema.optJSONArray("rules")).forEach { rule ->
                val values = draft.getJSONObject("rules").getJSONObject(rule.getString("key"))
                content.addView(toggle(rule.optString("name"), values.optBoolean("enabled")) {
                    values.put("enabled", it); changed()
                })
                if (objects(rule.optJSONArray("playstyles")).isNotEmpty()) {
                    row("适用玩法", "已选 ${values.optJSONArray("playstyles")?.length() ?: 0} 项") {
                        navigate("rule:${rule.getString("key")}")
                    }
                }
            }
            "options" -> objects(schema.optJSONArray("switches")).forEach { option ->
                val values = draft.getJSONObject("switches")
                content.addView(toggle(option.optString("name"), values.optBoolean(option.optString("key"))) {
                    values.put(option.optString("key"), it); changed()
                })
            }
            else -> {
                val key = page.removePrefix("rule:")
                val rule = objects(schema.optJSONArray("rules")).first { it.optString("key") == key }
                title = rule.optString("name")
                val values = draft.getJSONObject("rules").getJSONObject(key)
                objects(rule.optJSONArray("playstyles")).forEach { option ->
                    val name = option.optString("name")
                    val check = androidx.appcompat.widget.AppCompatCheckBox(this).apply {
                        setTextColor(ContextCompat.getColorStateList(context, R.color.settings_primary_text))
                        text = name
                        isChecked = strings(values.optJSONArray("playstyles")).contains(name)
                        minHeight = dp(56)
                        tag = "editable"
                        setOnCheckedChangeListener { _, selected ->
                            val selectedValues = strings(values.optJSONArray("playstyles")).toMutableList()
                            if (selected && name !in selectedValues) selectedValues.add(name) else if (!selected) selectedValues.remove(name)
                            values.put("playstyles", JSONArray(selectedValues)); changed()
                        }
                    }
                    content.addView(check)
                    if (option.optString("summary").isNotEmpty()) content.addView(label(option.optString("summary")))
                }
            }
        }
    }

    private fun navigate(next: String) { page = next; render() }

    private fun changed() {
        revision++
        invalidateOptionsMenu()
        if (viewModel.optString("kind") == "parameters") preview()
    }

    private fun preview() {
        previewPending?.let { handler.removeCallbacks(it) }
        val expected = revision
        val user = username
        val id = taskId
        val values = draft.toString()
        val pending = Runnable {
            executor.execute {
                val result = PyBridge.previewTaskSettings(this, user, id, values)
                runOnUiThread {
                    if (!isDestroyed && expected == revision && id == taskId && user == username && page == "detail" && result.optBoolean("ok")) {
                        val visible = strings(result.optJSONArray("visible")).toSet()
                        fields.forEach { (key, field) -> field.visibility = if (key in visible) View.VISIBLE else View.GONE }
                    }
                }
            }
        }
        previewPending = pending
        handler.postDelayed(pending, 150)
    }

    private fun save(reset: Boolean = false, after: (() -> Unit)? = null) {
        if (busy || readonly) return
        val values = draft.toString()
        val user = username
        val id = taskId
        val token = viewModel.optString("token")
        request({ PyBridge.saveTaskSettings(this, user, id, token, values, reset) }) { result ->
            if (result.optBoolean("ok")) {
                baseline = canonical(draft)
                Toast.makeText(this, result.optString("message"), Toast.LENGTH_SHORT).show()
                if (after != null) after() else loadDetail()
            } else if (result.optBoolean("conflict")) {
                AlertDialog.Builder(this).setTitle("配置已更新").setMessage(result.optString("message"))
                    .setPositiveButton("重新加载") { _, _ -> loadDetail() }
                    .setNegativeButton("保留草稿", null).show()
            } else {
                val errors = result.optJSONObject("errors")
                inputs.forEach { (name, field) -> field.error = errors?.optString(name)?.takeIf { it.isNotEmpty() } }
                inputs.values.firstOrNull { !it.error.isNullOrEmpty() }?.let { field ->
                    field.requestFocus()
                    findViewById<ScrollView>(R.id.settings_scroll).smoothScrollTo(0, field.top)
                }
                showError(result)
            }
        }
    }

    private fun leaveDraft(action: () -> Unit) {
        if (busy) return
        if (!dirty()) return action()
        val dialog = AlertDialog.Builder(this).setTitle("尚未保存修改")
            .setMessage("是否保存该用户的任务参数？")
            .setPositiveButton("保存并返回") { _, _ -> save(after = action) }
            .setNegativeButton("放弃修改") { _, _ -> action() }
            .setNeutralButton("继续编辑", null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled = !readonly }
        dialog.show()
    }

    private fun back() {
        when {
            page.startsWith("rule:") -> navigate("rules")
            page != "detail" && page != "list" -> navigate("detail")
            page == "detail" -> leaveDraft { loadList() }
            else -> finish()
        }
    }

    private fun dirty() = page != "list" && baseline.isNotEmpty() && canonical(draft) != baseline

    private fun applyReadOnly() {
        fun update(view: View) {
            if (view.tag == "editable") view.isEnabled = !readonly && !busy
            if (view.tag == "locked") view.isEnabled = false
            if (view is ViewGroup) for (i in 0 until view.childCount) update(view.getChildAt(i))
        }
        update(content)
        contextLine()
        invalidateOptionsMenu()
    }

    private fun contextLine() {
        findViewById<TextView>(R.id.settings_context).apply {
            text = (if (page != "list") "配置用户：$username" else "") +
                if (readonly) (if (page != "list") "\n" else "") + "停止任务后可修改参数" else ""
            visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
        }
    }

    private fun row(title: String, summary: String, action: () -> Unit) {
        content.addView(settingRow(title, summary).apply { setOnClickListener { if (!busy) action() } })
    }

    private fun settingRow(title: String, summary: String): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = android.view.Gravity.CENTER_VERTICAL
        minimumHeight = dp(72)
        val labels = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(label(title, 16f))
            addView(label(summary, 14f).apply {
                setTextColor(ContextCompat.getColorStateList(context, R.color.settings_secondary_text))
            })
        }
        addView(labels, LinearLayout.LayoutParams(0, -2, 1f))
        addView(label("›", 24f).apply { setPadding(dp(16), 0, 0, 0) })
        isClickable = true
        isFocusable = true
        val attr = android.util.TypedValue()
        theme.resolveAttribute(android.R.attr.selectableItemBackground, attr, true)
        setBackgroundResource(attr.resourceId)
    }

    private fun toggle(title: String, checked: Boolean, action: (Boolean) -> Unit): SwitchCompat = SwitchCompat(this).apply {
        setTextColor(ContextCompat.getColorStateList(context, R.color.settings_primary_text))
        text = title
        isChecked = checked
        minHeight = dp(56)
        tag = "editable"
        setOnCheckedChangeListener { _, value -> action(value) }
    }

    private fun multiChoice(title: String, options: List<Pair<String, String>>, initial: BooleanArray, done: (BooleanArray) -> Unit) {
        val flags = initial.clone()
        AlertDialog.Builder(this).setTitle(title)
            .setMultiChoiceItems(options.map { it.second }.toTypedArray(), flags) { _, index, checked -> flags[index] = checked }
            .setPositiveButton("确定") { _, _ -> done(flags) }
            .setNegativeButton("放弃修改", null).show()
    }

    private fun label(value: String, size: Float = 14f) = TextView(this).apply {
        setTextColor(ContextCompat.getColorStateList(context, R.color.settings_primary_text))
        text = value
        textSize = size
        setPadding(0, dp(4), 0, dp(4))
    }

    private fun showError(result: JSONObject) {
        Toast.makeText(this, result.optString("message").ifEmpty { "配置加载失败" }, Toast.LENGTH_LONG).show()
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun strings(array: JSONArray?) = (0 until (array?.length() ?: 0)).map { array!!.getString(it) }
    private fun objects(array: JSONArray?) = (0 until (array?.length() ?: 0)).map { array!!.getJSONObject(it) }
    private fun objectsOrStrings(array: JSONArray?) = (0 until (array?.length() ?: 0)).map {
        val value = array!!.get(it)
        if (value is JSONObject) value.getString("value") to value.optString("label", value.getString("value")) else value.toString() to value.toString()
    }

    private fun canonical(value: Any?): String = when (value) {
        is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(",", "{", "}") { JSONObject.quote(it) + ":" + canonical(value.opt(it)) }
        is JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { canonical(value.opt(it)) }
        is String -> JSONObject.quote(value)
        else -> value.toString()
    }
}
