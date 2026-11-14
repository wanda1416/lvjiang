"""智能调律 Agent 发布的文档布局（目录层与稳定别名由本插件声明）。"""
from __future__ import annotations

from .....core.ai.agent_docs import DocumentLayout

# Publish complete layers without relocating their body text or changing their relative paths.
DOCUMENT_DIRECTORIES = ("10-game", "30-architecture", "60-userguide", "70-agent")

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
    "userguide": "60-userguide/README.md",
    "architecture": "30-architecture/README.md",
    "dsl": "30-architecture/32-grammar/README.md",
    "tool-schema": "70-agent/schemas/tools.json",
}

AGENT_DOCUMENT_LAYOUT = DocumentLayout(
    directories=DOCUMENT_DIRECTORIES,
    manifest="70-agent/manifest.json",
    aliases=AGENT_DOCUMENTS,
    schema_id="tool-schema",
)
