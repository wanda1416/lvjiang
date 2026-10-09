"""Build an explicit, closed release document bundle and its public tool schemas."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang._version import __version__  # noqa: E402
from lvjiang.apps.yysls.core.agent.catalog import tool_catalog  # noqa: E402
from lvjiang.apps.yysls.core.agent.service import AgentService  # noqa: E402
from lvjiang.core.agent_docs import AgentDocuments  # noqa: E402
from lvjiang.core.agent_mcp import LocalMCPServer  # noqa: E402

# No directory globs: adding a document requires an explicit public boundary decision.
DOCUMENTS = {
    "entry": ("docs/70-agent/README.md", "README.md"),
    "permissions": ("docs/70-agent/10-contracts/01-context-and-permissions.md", "docs/contracts/permissions.md"),
    "analysis": ("docs/70-agent/20-operations/01-analysis.md", "docs/operations/analysis.md"),
    "generation": ("docs/70-agent/20-operations/02-generation.md", "docs/operations/generation.md"),
    "workbuddy": ("docs/70-agent/30-examples/01-workbuddy.md", "docs/examples/workbuddy.md"),
    "equipment": ("docs/10-game/10-public/01-equipment-system.md", "docs/domain/equipment.md"),
    "schools": ("docs/10-game/10-public/02-school-system.md", "docs/domain/schools.md"),
    "damage": ("docs/10-game/10-public/03-damage-mechanics.md", "docs/domain/damage.md"),
    "domain": ("docs/10-game/10-public/README.md", "docs/domain/README.md"),
    "rules": ("docs/10-game/10-public/10-tuning-rules/README.md", "docs/domain/rules.md"),
    "huiyi": ("docs/10-game/10-public/10-tuning-rules/01-huiyi.md", "docs/domain/huiyi.md"),
    "huixin-big": ("docs/10-game/10-public/10-tuning-rules/02-huixin-big.md", "docs/domain/huixin-big.md"),
    "huixin-small": ("docs/10-game/10-public/10-tuning-rules/03-huixin-small.md", "docs/domain/huixin-small.md"),
    "heal-pure": ("docs/10-game/10-public/10-tuning-rules/04-heal-pure.md", "docs/domain/heal-pure.md"),
    "heal-fire": ("docs/10-game/10-public/10-tuning-rules/05-heal-fire.md", "docs/domain/heal-fire.md"),
    "modao": ("docs/10-game/10-public/10-tuning-rules/06-weiwei-dawang.md", "docs/domain/modao.md"),
}


def build_bundle(output: Path, *, archive: Path | None = None) -> dict:
    import os
    mapping = {(ROOT / source).resolve(): target for source, target in DOCUMENTS.values()}
    documents = []
    output.mkdir(parents=True, exist_ok=True)
    for doc_id, (source_name, target_name) in DOCUMENTS.items():
        source = ROOT / source_name
        target = output / target_name

        def rewrite(match, source=source, target=target):
            label, url = match.groups()
            if "://" in url or url.startswith("#"):
                return match.group(0)
            name, sep, anchor = url.partition("#")
            destination = mapping.get((source.parent / name).resolve())
            if destination is None:
                # Maintenance references are never a reason to expand the bundle.
                return label + "（维护参考，不随包提供）"
            relative = os.path.relpath(output / destination, target.parent).replace(os.sep, "/")
            return f"[{label}]({relative}{sep}{anchor})"

        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", rewrite, source.read_text(encoding="utf-8"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        documents.append({"id": doc_id, "path": target_name,
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    service = AgentService(ROOT, dispatch=lambda *_args: {})
    server = LocalMCPServer(tool_catalog(service, AgentDocuments(output)), instructions="律匠公开工具")
    tools = asyncio.run(server.mcp.list_tools())
    schemas = json.dumps([tool.model_dump(mode="json") for tool in tools], ensure_ascii=False, indent=2)
    schema_path = output / "schemas/tools.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(schemas + "\n", encoding="utf-8")
    documents.append({"id": "tool-schema", "path": "schemas/tools.json",
                      "sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest()})
    manifest = {"version": __version__, "api_version": 1, "documents": documents}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    allowed = {d["path"] for d in documents} | {"manifest.json"}
    unexpected = [p for p in output.rglob("*") if p.is_file() and p.relative_to(output).as_posix() not in allowed]
    if unexpected:
        raise ValueError("输出目录含清单外文件，请使用新的空目录")
    if archive is not None:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            for name in sorted(allowed):
                package.write(output / name, "agent/" + name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", dest="archive", type=Path)
    args = parser.parse_args()
    result = build_bundle(args.output, archive=args.archive)
    print(f"Agent bundle: v{result['version']}, {len(result['documents'])} documents/schemas")


if __name__ == "__main__":
    main()
