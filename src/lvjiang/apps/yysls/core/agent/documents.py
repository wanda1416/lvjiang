"""智能调律 Agent 发布的文档布局（目录层与稳定别名由本插件声明）。"""
from __future__ import annotations

from .....core.ai.agent_docs import DocumentLayout

# Publish complete layers without relocating their body text or changing their relative paths.
# 目录编号化后各层落位（10-domain/30-design/40-reference/60-guide/70-agent），
# 逻辑分类键仍用旧编号（10-game/30-architecture/60-userguide/70-agent，见 categories），对外契约不变。
DOCUMENT_DIRECTORIES = ("10-domain", "30-design", "40-reference", "60-guide", "70-agent")

# Keep convenient stable IDs for the Agent's entry and most-used business documents.
AGENT_DOCUMENTS = {
    "entry": "70-agent/README.md",
    "permissions": "70-agent/10-contracts/01-context-and-permissions.md",
    "analysis": "70-agent/20-operations/01-analysis.md",
    "generation": "70-agent/20-operations/02-generation.md",
    "workbuddy": "70-agent/30-examples/01-workbuddy.md",
    "equipment": "10-domain/01-yysls/01-equipment-system.md",
    "schools": "10-domain/01-yysls/02-school-system.md",
    "damage": "10-domain/01-yysls/03-damage-mechanics.md",
    "domain": "10-domain/01-yysls/README.md",
    "rules": "10-domain/01-yysls/10-tuning-rules/README.md",
    "huiyi": "10-domain/01-yysls/10-tuning-rules/01-huiyi.md",
    "huixin-big": "10-domain/01-yysls/10-tuning-rules/02-huixin-big.md",
    "huixin-small": "10-domain/01-yysls/10-tuning-rules/03-huixin-small.md",
    "heal-pure": "10-domain/01-yysls/10-tuning-rules/04-heal-pure.md",
    "heal-fire": "10-domain/01-yysls/10-tuning-rules/05-heal-fire.md",
    "modao": "10-domain/01-yysls/10-tuning-rules/06-weiwei-dawang.md",
    "userguide": "60-guide/README.md",
    "architecture": "30-design/01-architecture/README.md",
    "dsl": "40-reference/02-dsl/README.md",
    "tool-schema": "70-agent/40-schemas/tools.json",
}

# 旧编号目录（10-game/30-architecture/60-userguide 等）与旧派生 ID 已随目录编号化
# 一次性切换，不做兼容保留：外部 Agent 用 list_docs 重新获取现行 ID 即可。

AGENT_DOCUMENT_LAYOUT = DocumentLayout(
    directories=DOCUMENT_DIRECTORIES,
    manifest="70-agent/00-manifest.json",
    aliases=AGENT_DOCUMENTS,
    categories={"10-domain": "10-game", "30-design": "30-architecture",
                "40-reference": "30-architecture", "60-guide": "60-userguide",
                "70-agent": "70-agent"},
    schema_id="tool-schema",
)
