package com.lvjiang.app

import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * TuningConfigActivity — 调律参数配置页（对应桌面调律 Tab 的三页配置面）。
 *
 * Kotlin 侧不写死任何规则/玩法/部位清单：onCreate 时调
 * PyBridge.getTuningConfig 拿「可配置项 + 当前保存值」的合并视图，
 * 按 JSON 渲染四个区块（基础规则 / 调律规则 / 调律部位 / 全局开关）；
 * 保存时把控件状态组装回 JSON 交 saveTuningConfig，校验（部位非空 /
 * 启用规则非空 / 启用规则玩法非空）在 Python 侧做，失败原因直接 toast。
 *
 * 表单条目全部代码生成（与 FloatService 面板同思路）：条目数量与文案
 * 都来自运行时数据，XML 布局只搭「标题 + 滚动区 + 保存按钮」的骨架。
 * 返回键直接退出，不提示未保存改动（开发期从简）。
 */
class TuningConfigActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()

    private lateinit var container: LinearLayout
    private lateinit var loadingText: TextView
    private lateinit var saveButton: Button

    /** 规则 key → (启用开关, 玩法名 → 复选框)。保存时按此收集 */
    private val ruleBindings = mutableListOf<RuleBinding>()

    /** 基础规则组 key → 单选按钮（单选，保存时收集选中 key） */
    private val groupRadios = linkedMapOf<String, RadioButton>()

    /** 部位 key → 复选框（禁用项也在内，收集时 isChecked 自然为 false） */
    private val slotChecks = linkedMapOf<String, CheckBox>()

    /** 开关 key → Switch */
    private val switchBindings = linkedMapOf<String, Switch>()

    private data class RuleBinding(
        val key: String,
        val enabled: Switch,
        val playstyles: Map<String, CheckBox>,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        // 透明主题叠在游戏画面上，底色恒为暗色 → 本页强制深色模式，
        // 让 CheckBox/Switch/TextView 的默认文字色走浅色系（仅本页生效）
        delegate.localNightMode = AppCompatDelegate.MODE_NIGHT_YES
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_tuning_config)

        container = findViewById(R.id.content_container)
        loadingText = findViewById(R.id.loading_text)
        saveButton = findViewById(R.id.btn_save)
        saveButton.setOnClickListener { save() }

        executor.execute {
            val config = PyBridge.getTuningConfig(this)
            runOnUiThread { render(config) }
        }
    }

    override fun onDestroy() {
        executor.shutdown()
        super.onDestroy()
    }

    // ── 渲染 ──────────────────────────────────────────────

    private fun render(config: JSONObject) {
        if (!config.optBoolean("ok", false)) {
            loadingText.text = config.optString("error")
                .ifEmpty { config.optString("message", "配置加载失败") }
            return
        }
        loadingText.visibility = View.GONE
        ruleBindings.clear()
        groupRadios.clear()
        slotChecks.clear()
        switchBindings.clear()

        container.addView(sectionTitle("基础规则"))
        val radioGroup = RadioGroup(this)
        val baseGroups = config.optJSONArray("base_groups") ?: JSONArray()
        for (i in 0 until baseGroups.length()) {
            val item = baseGroups.optJSONObject(i) ?: continue
            val rb = RadioButton(this).apply {
                text = item.optString("name")
                isChecked = item.optBoolean("checked", false)
            }
            groupRadios[item.optString("key")] = rb
            radioGroup.addView(rb)
        }
        if (radioGroup.checkedRadioButtonId == View.NO_ID
            && radioGroup.childCount > 0) {
            // 后端未给出选中项时兜底选第一项（通常是默认规则组）
            (radioGroup.getChildAt(0) as RadioButton).isChecked = true
        }
        container.addView(radioGroup)

        container.addView(sectionTitle("调律规则"))
        val rules = config.optJSONArray("rules") ?: JSONArray()
        for (i in 0 until rules.length()) {
            val rule = rules.optJSONObject(i) ?: continue
            container.addView(buildRuleCard(rule))
        }

        container.addView(sectionTitle("调律部位"))
        val slotGroups = config.optJSONArray("slot_groups") ?: JSONArray()
        for (i in 0 until slotGroups.length()) {
            val group = slotGroups.optJSONObject(i) ?: continue
            container.addView(subTitle(group.optString("name")))
            val slots = group.optJSONArray("slots") ?: continue
            for (j in 0 until slots.length()) {
                val slot = slots.optJSONObject(j) ?: continue
                val cb = CheckBox(this).apply {
                    text = slot.optString("label")
                    isChecked = slot.optBoolean("checked", false)
                    // 禁用部位（副武器）：主武器槽已展示全部武器，无需遍历
                    isEnabled = !slot.optBoolean("locked", false)
                }
                slotChecks[slot.optString("key")] = cb
                container.addView(cb)
            }
        }

        container.addView(sectionTitle("全局开关"))
        val switches = config.optJSONArray("switches") ?: JSONArray()
        for (i in 0 until switches.length()) {
            val item = switches.optJSONObject(i) ?: continue
            val sw = Switch(this).apply {
                text = item.optString("name")
                isChecked = item.optBoolean("checked", false)
            }
            switchBindings[item.optString("key")] = sw
            container.addView(sw)
        }

        saveButton.isEnabled = true
    }

    /** 规则卡片：首行「规则名 + 启用 Switch」，下方玩法复选框组。
     *  Switch 关闭时玩法整组置灰但保留勾选状态，与桌面折叠语义一致 */
    private fun buildRuleCard(rule: JSONObject): View {
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(8), dp(12), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(10).toFloat()
                setColor(0x14808080)
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(8) }
        }

        val enabledSwitch = Switch(this).apply {
            text = rule.optString("name")
            isChecked = rule.optBoolean("enabled", false)
        }
        card.addView(enabledSwitch)

        val playstyleChecks = linkedMapOf<String, CheckBox>()
        val playstyles = rule.optJSONArray("playstyles") ?: JSONArray()
        for (i in 0 until playstyles.length()) {
            val ps = playstyles.optJSONObject(i) ?: continue
            val name = ps.optString("name")
            val summary = ps.optString("summary")
            val cb = CheckBox(this).apply {
                text = if (summary.isEmpty()) name else "$name（$summary）"
                isChecked = ps.optBoolean("checked", false)
                isEnabled = enabledSwitch.isChecked
            }
            playstyleChecks[name] = cb
            card.addView(cb)
        }
        enabledSwitch.setOnCheckedChangeListener { _, checked ->
            playstyleChecks.values.forEach { it.isEnabled = checked }
        }

        ruleBindings.add(
            RuleBinding(rule.optString("key"), enabledSwitch, playstyleChecks)
        )
        return card
    }

    private fun sectionTitle(text: String): TextView =
        TextView(this).apply {
            this.text = text
            textSize = 16f
            setTypeface(null, Typeface.BOLD)
            setPadding(0, dp(16), 0, dp(4))
        }

    private fun subTitle(text: String): TextView =
        TextView(this).apply {
            this.text = text
            textSize = 13f
            setPadding(0, dp(6), 0, 0)
        }

    // ── 保存 ──────────────────────────────────────────────

    private fun save() {
        val payload = JSONObject().apply {
            groupRadios.entries.firstOrNull { it.value.isChecked }
                ?.let { (key, _) -> put("base_group", key) }
            put("selected_slots", JSONArray().also { arr ->
                slotChecks.forEach { (key, cb) -> if (cb.isChecked) arr.put(key) }
            })
            put("rules", JSONObject().also { obj ->
                for (binding in ruleBindings) {
                    obj.put(binding.key, JSONObject().apply {
                        put("enabled", binding.enabled.isChecked)
                        put("playstyles", JSONArray().also { arr ->
                            binding.playstyles.forEach { (name, cb) ->
                                if (cb.isChecked) arr.put(name)
                            }
                        })
                    })
                }
            })
            put("switches", JSONObject().also { obj ->
                switchBindings.forEach { (key, sw) -> obj.put(key, sw.isChecked) }
            })
        }

        saveButton.isEnabled = false
        executor.execute {
            val result = PyBridge.saveTuningConfig(this, payload.toString())
            runOnUiThread {
                saveButton.isEnabled = true
                val ok = result.optBoolean("ok", false)
                toast(result.optString("message", if (ok) "已保存" else "保存失败"))
                if (ok) finish()
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()
}
