"""智能调律 Agent 发布的文档布局（目录层与稳定别名由本插件声明）。"""
from __future__ import annotations

from .....core.ai.agent_docs import DocumentLayout

# Publish complete layers without relocating their body text or changing their relative paths.
# 用户指南正文已迁至 guide/（desktop/android/accounts/automation 子目录），
# 逻辑分类键仍叫 60-userguide（见 categories），对外契约不变。
DOCUMENT_DIRECTORIES = ("10-game", "30-architecture", "guide", "70-agent")

# Keep convenient stable IDs for the Agent's entry and most-used business documents.
AGENT_DOCUMENTS = {
    "entry": "70-agent/README.md",
    "permissions": "70-agent/10-contracts/01-context-and-permissions.md",
    "analysis": "70-agent/20-operations/01-analysis.md",
    "generation": "70-agent/20-operations/02-generation.md",
    "workbuddy": "70-agent/30-examples/01-workbuddy.md",
    "equipment": "10-game/01-equipment-system.md",
    "schools": "10-game/02-school-system.md",
    "damage": "10-game/03-damage-mechanics.md",
    "domain": "10-game/README.md",
    "rules": "10-game/10-tuning-rules/README.md",
    "huiyi": "10-game/10-tuning-rules/01-huiyi.md",
    "huixin-big": "10-game/10-tuning-rules/02-huixin-big.md",
    "huixin-small": "10-game/10-tuning-rules/03-huixin-small.md",
    "heal-pure": "10-game/10-tuning-rules/04-heal-pure.md",
    "heal-fire": "10-game/10-tuning-rules/05-heal-fire.md",
    "modao": "10-game/10-tuning-rules/06-weiwei-dawang.md",
    "userguide": "guide/README.md",
    "architecture": "30-architecture/README.md",
    "dsl": "30-architecture/32-grammar/README.md",
    "tool-schema": "70-agent/schemas/tools.json",
}

# 用户指南迁移后旧 ID 继续可读：旧派生 ID → 迁移后的现行派生 ID。
_USERGUIDE_MOVES = {
    "01-quick-start": "desktop",
    "02-main-interface": "desktop",
    "03.01-base-config": "desktop",
    "03.02-scene-management": "desktop",
    "03.03-gallery-management": "desktop",
    "03.04-batch-tasks": "desktop",
    "03.05-visual-recognition-troubleshooting": "desktop",
    "03.06-world-map-management": "desktop",
    "08-script-authoring": "desktop",
    "09-faq-and-feedback": "desktop",
    "appendix-b-command-line": "desktop",
    "07-mobile-device": "android",
    "appendix-a-online-config": "accounts",
    "04.01-tuning-config-overview": "automation",
    "04.02-default-config": "automation",
    "04.03-behavior-config": "automation",
    "04.04-school-rules": "automation",
    "04.05-game-config": "automation",
    "04.06-running-tuning": "automation",
    "04.07-agent-tuning": "automation",
    "05.01-equipment-data-overview": "automation",
    "05.02-equipment-data-acquisition": "automation",
    "05.03-equipment-management": "automation",
    "05.04-base-attributes-and-graduation": "automation",
    "05.05-school-rule-filtering": "automation",
    "05.06-optimal-equipment-combination": "automation",
    "05.07-build-calculator": "automation",
    "06-workflows": "automation",
    "06.07-system-scripts": "automation",
    "appendix-c-preset-gallery": "automation",
}
DOCUMENT_COMPAT = {
    **{f"60-userguide:{name}": f"guide:{subdir}:{name}" for name, subdir in _USERGUIDE_MOVES.items()},
    "60-userguide:README": "userguide",
}

AGENT_DOCUMENT_LAYOUT = DocumentLayout(
    directories=DOCUMENT_DIRECTORIES,
    manifest="70-agent/manifest.json",
    aliases=AGENT_DOCUMENTS,
    compat=DOCUMENT_COMPAT,
    categories={"guide": "60-userguide"},
    schema_id="tool-schema",
)
