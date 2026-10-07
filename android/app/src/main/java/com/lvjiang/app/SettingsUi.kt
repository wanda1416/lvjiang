package com.lvjiang.app

import android.content.Context
import android.content.res.Configuration
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

/** 设置页面共用安全区和文字层级，不包含用户选择或业务配置。 */
object SettingsUi {
    fun install(activity: AppCompatActivity) {
        WindowCompat.setDecorFitsSystemWindows(activity.window, false)
        activity.setContentView(R.layout.activity_task_settings)
        val root = activity.findViewById<View>(R.id.settings_root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, insets.getInsets(WindowInsetsCompat.Type.ime()).bottom))
            WindowInsetsCompat.CONSUMED
        }
        val light = activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK != Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(activity.window, root).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
        ViewCompat.requestApplyInsets(root)
        activity.setSupportActionBar(activity.findViewById(R.id.settings_toolbar))
        activity.supportActionBar?.setDisplayHomeAsUpEnabled(true)
    }

    fun userLabel(username: String) = if (username == "default") "默认用户" else username

    fun label(context: Context, value: String, size: Float = 14f, secondary: Boolean = false) = TextView(context).apply {
        text = value
        textSize = size
        setTextColor(ContextCompat.getColorStateList(context, if (secondary) R.color.settings_secondary_text else R.color.settings_primary_text))
        val pad = (4 * resources.displayMetrics.density).toInt()
        setPadding(0, pad, 0, pad)
    }

    fun row(context: Context, title: String, summary: String): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = android.view.Gravity.CENTER_VERTICAL
        minimumHeight = (72 * resources.displayMetrics.density).toInt()
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(label(context, title, 16f))
            addView(label(context, summary, 14f, secondary = true))
        }, LinearLayout.LayoutParams(0, -2, 1f))
        addView(label(context, "›", 24f).apply { setPadding((16 * resources.displayMetrics.density).toInt(), 0, 0, 0) })
        isClickable = true
        isFocusable = true
        val attr = android.util.TypedValue()
        context.theme.resolveAttribute(android.R.attr.selectableItemBackground, attr, true)
        setBackgroundResource(attr.resourceId)
    }
}
