"""Build an explicit, closed release document bundle and its public tool schemas."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lvjiang.apps.yysls.core.agent.catalog import tool_catalog  # noqa: E402
from lvjiang.apps.yysls.core.agent.service import AgentService  # noqa: E402
from lvjiang.core.agent_docs import AGENT_DOCUMENTS, AgentDocuments  # noqa: E402
from lvjiang.core.agent_mcp import LocalMCPServer  # noqa: E402


def build_bundle(output: Path, *, archive: Path | None = None) -> dict:
    source_root = ROOT / "docs"
    output.mkdir(parents=True, exist_ok=True)
    service = AgentService(ROOT, dispatch=lambda *_args: {})
    server = LocalMCPServer(tool_catalog(service, AgentDocuments(output)), instructions="律匠公开工具")
    tools = asyncio.run(server.mcp.list_tools())
    schemas = json.dumps([tool.model_dump(mode="json") for tool in tools], ensure_ascii=False, indent=2)
    schema_path = output / AGENT_DOCUMENTS["tool-schema"]
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    # The manifest hashes these exact UTF-8 bytes; Windows text I/O must not add CRLF.
    schema_path.write_bytes((schemas + "\n").encode("utf-8"))
    source = AgentDocuments(source_root, source_mode=True,
                            schema_provider=lambda: schemas + "\n")
    manifest = source.list_docs()
    documents = manifest["documents"]
    for entry in documents:
        if entry["id"] == "tool-schema":
            continue
        target = output / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / entry["path"]).read_bytes())
    (output / "70-agent/manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    allowed = {d["path"] for d in documents} | {"70-agent/manifest.json"}
    unexpected = [p for p in output.rglob("*") if p.is_file() and p.relative_to(output).as_posix() not in allowed]
    if unexpected:
        raise ValueError("输出目录含清单外文件，请使用新的空目录")
    if archive is not None:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            for name in sorted(allowed):
                package.write(output / name, "docs/" + name)
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
