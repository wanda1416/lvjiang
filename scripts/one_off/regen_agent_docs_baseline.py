"""一次性迁移：按当前 docs 重新生成 Agent 发行文档基线（tests/yysls/data）。

`90-develop/` 退场、`06-workflow-simulation.md` 迁入 30-design 以及活跃文档剥离
`> 最后更新` 都会改变发布文档集合与 sha256，基线必须同步，否则
`tests/yysls/test_agent_bundle.py` 的 ID 契约门禁会红灯。

版本字段沿用 fixture 现值（版本号属发布动作，不在文档迁移里改）。

用法（仓库根、仓库 .venv）：

    python scripts/one_off/regen_agent_docs_baseline.py --check
    python scripts/one_off/regen_agent_docs_baseline.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/yysls/data/agent_docs_baseline.json"


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from lvjiang.apps.yysls.core.agent.documents import (
        AGENT_DOCUMENT_LAYOUT,
        DOCUMENT_DIRECTORIES,
    )
    from lvjiang.core.ai.agent_docs import AgentDocuments

    parser = argparse.ArgumentParser(description="重生成 Agent 文档基线")
    parser.add_argument("--check", action="store_true", help="只报告差异")
    args = parser.parse_args()

    current = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source = AgentDocuments(REPO / "docs", AGENT_DOCUMENT_LAYOUT, source_mode=True,
                            schema_provider=lambda: "{}\n")
    documents = source.list_docs()["documents"]

    old = {entry["id"]: entry for entry in current["documents"]}
    new = {entry["id"]: entry for entry in documents}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(doc_id for doc_id in set(old) & set(new)
                     if old[doc_id].get("sha256") != new[doc_id].get("sha256")
                     or old[doc_id].get("path") != new[doc_id].get("path"))
    for label, items in (("新增", added), ("移除", removed), ("正文变化", changed)):
        print(f"{label} {len(items)}")
        for doc_id in items:
            print(f"  {doc_id}: {new.get(doc_id, old.get(doc_id))['path']}")
    if args.check:
        return 1 if (added or removed or changed) else 0

    payload = {"version": current["version"], "api_version": current["api_version"],
               "documents": documents}
    # 仓库内 JSON 一律 LF；Windows 默认换行会让 .gitattributes 的 eol=lf 每次报警。
    FIXTURE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8", newline="\n")
    print(f"已写入 {FIXTURE.relative_to(REPO).as_posix()}（{len(documents)} 篇，"
          f"发布层 {len(DOCUMENT_DIRECTORIES)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
