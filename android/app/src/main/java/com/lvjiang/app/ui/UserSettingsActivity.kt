package com.lvjiang.app

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONObject
import java.util.concurrent.Executors

/** 用户列表/资料详情。只编辑资料，不替代悬浮窗的执行用户选择。 */
class UserSettingsActivity : AppCompatActivity() {
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var content: LinearLayout
    private var username = ""
    private var attributes = JSONObject()
    private var baseline = JSONObject()
    private var readonly = false
    private var busy = false
    private val poll = object : Runnable {
        override fun run() {
            if (isFinishing || isDestroyed) return
            executor.execute {
                val result = PyBridge.status(this@UserSettingsActivity)
                val locked = result.optString("state") in setOf("running", "pausing", "paused", "stopping") || AgentServer.pcControlsDevice()
                runOnUiThread {
                    if (!isDestroyed) {
                        readonly = locked
                        contextLine()
                        invalidateOptionsMenu()
                    }
                }
            }
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SettingsUi.install(this)
        content = findViewById(R.id.settings_content)
        findViewById<TextView>(R.id.settings_notice).text = "用户资料仅在手机保存，不回传电脑。编辑资料不会切换执行用户或游戏角色。"
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { back() }
        })
        username = savedInstanceState?.getString("username").orEmpty()
        if (username.isNotEmpty() && savedInstanceState != null) {
            attributes = JSONObject(savedInstanceState.getString("attributes") ?: "{}")
            baseline = JSONObject(savedInstanceState.getString("baseline") ?: "{}")
            renderAttributes()
        } else loadUsers()
    }

    override fun onStart() { super.onStart(); handler.post(poll) }
    override fun onStop() { handler.removeCallbacks(poll); super.onStop() }
    override fun onDestroy() { handler.removeCallbacksAndMessages(null); executor.shutdown(); super.onDestroy() }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("username", username)
        outState.putString("attributes", attributes.toString())
        outState.putString("baseline", baseline.toString())
        super.onSaveInstanceState(outState)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, if (username.isEmpty()) "新增用户" else "保存").apply {
            setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
            isEnabled = !busy && !readonly && (username.isEmpty() || dirty())
        }
        if (username.isNotEmpty()) menu.add(0, 2, 1, "添加属性").isEnabled = !busy && !readonly
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            android.R.id.home -> back()
            1 -> if (username.isEmpty()) createUser() else save()
            2 -> editAttribute()
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun request(work: () -> JSONObject, done: (JSONObject) -> Unit) {
        busy = true
        invalidateOptionsMenu()
        executor.execute {
            val result = work()
            runOnUiThread {
                if (!isDestroyed && !isFinishing) {
                    busy = false
                    done(result)
                    invalidateOptionsMenu()
                }
            }
        }
    }

    private fun loadUsers() {
        username = ""
        title = "用户管理"
        contextLine()
        content.removeAllViews()
        request({ PyBridge.listUsers(this) }) { result ->
            if (!result.optBoolean("ok")) return@request error(result)
            val users = result.optJSONArray("users")
            for (i in 0 until (users?.length() ?: 0)) {
                val name = users!!.getString(i)
                content.addView(SettingsUi.row(this, SettingsUi.userLabel(name), "点击编辑用户资料").apply {
                    setOnClickListener {
                        if (!busy) loadUser(name)
                    }
                })
            }
        }
    }

    private fun loadUser(name: String) {
        request({ PyBridge.getUserSettings(this, name) }) { result ->
            if (!result.optBoolean("ok")) return@request error(result)
            username = name
            attributes = JSONObject(result.optJSONObject("attributes")?.toString() ?: "{}")
            baseline = JSONObject(attributes.toString())
            readonly = result.optBoolean("readonly") || AgentServer.pcControlsDevice()
            renderAttributes()
        }
    }

    private fun renderAttributes() {
        title = "用户资料"
        contextLine()
        content.removeAllViews()
        if (attributes.length() == 0) content.addView(SettingsUi.label(this, "暂无属性。只在任务需要时填写，与电脑端通用用户属性一致。"))
        attributes.keys().asSequence().toList().sorted().forEach { key ->
            content.addView(SettingsUi.row(this, key, attributes.optString(key)).apply {
                setOnClickListener { editAttribute(key) }
            })
        }
        invalidateOptionsMenu()
    }

    private fun input(label: String, initial: String = ""): Pair<TextInputLayout, TextInputEditText> {
        val box = TextInputLayout(this).apply {
            hint = label
            layoutParams = LinearLayout.LayoutParams(-1, -2)
        }
        val edit = TextInputEditText(box.context).apply { setSingleLine(); setText(initial) }
        box.addView(edit)
        return box to edit
    }

    private fun createUser() {
        if (busy || readonly) return
        val (box, edit) = input("用户名称")
        val wrapper = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(12), dp(24), 0)
            addView(box)
        }
        val dialog = AlertDialog.Builder(this).setTitle("新增用户").setView(wrapper)
            .setPositiveButton("创建", null).setNegativeButton("返回", null).create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (readonly || busy) return@setOnClickListener
                val name = edit.text?.toString()?.trim().orEmpty()
                if (name.isEmpty()) { box.error = "请输入用户名称"; return@setOnClickListener }
                request({ PyBridge.createUser(this, name) }) { result ->
                    if (!result.optBoolean("ok")) box.error = result.optString("message")
                    else { dialog.dismiss(); loadUsers() }
                }
            }
        }
        dialog.show()
    }

    private fun editAttribute(oldName: String = "") {
        if (busy || readonly) return
        val (nameBox, name) = input("属性名称", oldName)
        val (valueBox, value) = input("属性内容", attributes.optString(oldName))
        val wrapper = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(12), dp(24), 0)
            addView(nameBox); addView(valueBox)
        }
        val builder = AlertDialog.Builder(this).setTitle(if (oldName.isEmpty()) "添加属性" else "编辑属性").setView(wrapper)
            .setPositiveButton("确定", null).setNegativeButton("放弃修改", null)
        if (oldName.isNotEmpty()) builder.setNeutralButton("移除属性") { _, _ ->
            if (!readonly) { attributes.remove(oldName); renderAttributes() }
        }
        val dialog = builder.create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (readonly || busy) return@setOnClickListener
                val key = name.text?.toString()?.trim().orEmpty()
                if (key.isEmpty() || key != oldName && attributes.has(key)) {
                    nameBox.error = "属性名称不能为空或重复"; return@setOnClickListener
                }
                if (oldName.isNotEmpty()) attributes.remove(oldName)
                attributes.put(key, value.text?.toString().orEmpty())
                dialog.dismiss(); renderAttributes()
            }
        }
        dialog.show()
    }

    private fun save(after: (() -> Unit)? = null) {
        if (busy || readonly) return
        val name = username
        val payload = attributes.toString()
        val original = baseline.toString()
        request({ PyBridge.saveUserAttributes(this, name, payload, original) }) { result ->
            if (!result.optBoolean("ok")) error(result)
            else {
                baseline = JSONObject(payload)
                Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show()
                if (after != null) after() else loadUser(name)
            }
        }
    }

    private fun dirty() = attributes.keys().asSequence().toSet() != baseline.keys().asSequence().toSet() ||
        attributes.keys().asSequence().any { attributes.optString(it) != baseline.optString(it) }

    private fun back() {
        if (busy) return
        if (username.isEmpty()) { finish(); return }
        if (!dirty()) { loadUsers(); return }
        val dialog = AlertDialog.Builder(this).setTitle("尚未保存修改")
            .setPositiveButton("保存并返回") { _, _ -> save { loadUsers() } }
            .setNegativeButton("放弃修改") { _, _ -> loadUsers() }
            .setNeutralButton("继续编辑", null).create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled = !readonly }
        dialog.show()
    }

    private fun contextLine() {
        findViewById<TextView>(R.id.settings_context).apply {
            text = (if (username.isEmpty()) "" else "用户：${SettingsUi.userLabel(username)}") +
                if (readonly) (if (username.isEmpty()) "" else "\n") + "停止任务后可修改资料" else ""
            visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
        }
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun error(result: JSONObject) { Toast.makeText(this, result.optString("message"), Toast.LENGTH_LONG).show() }
}
