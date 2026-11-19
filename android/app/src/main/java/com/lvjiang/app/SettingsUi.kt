package com.lvjiang.app

import android.content.Context
import android.content.res.Configuration
import android.graphics.Typeface
import android.util.TypedValue
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.AppCompatImageButton
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

/** 设置页面共用安全区和文字层级，不包含用户选择或业务配置。 */
object SettingsUi {
    fun shortLabel(value: String, limit: Int): String =
        if (value.codePointCount(0, value.length) <= limit) value
        else value.substring(0, value.offsetByCodePoints(0, limit)) + "..."

    fun dialogContent(context: Context) = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        val horizontal = resources.getDimensionPixelSize(R.dimen.dialog_content_horizontal_padding)
        val vertical = resources.getDimensionPixelSize(R.dimen.dialog_content_vertical_padding)
        setPadding(horizontal, vertical, horizontal, vertical)
    }

    fun dialogScroll(context: Context, content: View) = ScrollView(context).apply {
        addView(content, FrameLayout.LayoutParams(-1, -2))
    }

    fun applySystemBarAppearance(activity: AppCompatActivity, root: View) {
        val light = activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK != Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(activity.window, root).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
    }

    fun install(activity: AppCompatActivity) {
        WindowCompat.setDecorFitsSystemWindows(activity.window, false)
        activity.setContentView(R.layout.activity_task_settings)
        val root = activity.findViewById<View>(R.id.settings_root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, insets.getInsets(WindowInsetsCompat.Type.ime()).bottom))
            WindowInsetsCompat.CONSUMED
        }
        applySystemBarAppearance(activity, root)
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

    /** 卡片 / 分组标题：M3 Title Medium（16sp、字重 500），不是 18sp 粗体。 */
    fun heading(context: Context, value: String) = label(context, value, 16f).apply {
        // Typeface 没有 MEDIUM 常量：Medium 是 Roboto 的字体族名，不能当样式传
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    }

    /**
     * 行尾图标按钮：48dp×48dp 触控目标 + 无边界涟漪（M3 IconButton 的最小要求）。
     * 纯图标必须带 contentDescription，否则读屏只会念「按钮」。
     */
    fun iconButton(context: Context, iconRes: Int, description: String, action: () -> Unit) =
        AppCompatImageButton(context).apply {
            setImageResource(iconRes)
            contentDescription = description
            scaleType = ImageView.ScaleType.CENTER_INSIDE
            setPadding(0, 0, 0, 0)
            val attr = TypedValue()
            context.theme.resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, attr, true)
            setBackgroundResource(attr.resourceId)
            val side = (48 * resources.displayMetrics.density).toInt()
            layoutParams = LinearLayout.LayoutParams(side, side)
            setOnClickListener { action() }
        }

    fun row(context: Context, title: String, summary: String, summaryMaxLines: Int? = null, indentSummary: Boolean = false): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = android.view.Gravity.CENTER_VERTICAL
        minimumHeight = (72 * resources.displayMetrics.density).toInt()
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(label(context, title, 16f))
            addView(label(context, summary, 14f, secondary = true).apply {
                if (indentSummary) setPadding(textSize.toInt(), 0, 0, 0)
                summaryMaxLines?.let {
                    maxLines = it
                    ellipsize = android.text.TextUtils.TruncateAt.END
                }
            })
        }, LinearLayout.LayoutParams(0, -2, 1f))
        addView(label(context, "›", 24f).apply { setPadding((16 * resources.displayMetrics.density).toInt(), 0, 0, 0) })
        isClickable = true
        isFocusable = true
        val attr = android.util.TypedValue()
        context.theme.resolveAttribute(android.R.attr.selectableItemBackground, attr, true)
        setBackgroundResource(attr.resourceId)
    }
}
