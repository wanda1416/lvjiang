"""阶段 3 验证脚本"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.workflows.grammar import parse_text

src = 'log "hello"\nwait 0.1\n'
p = parse_text(src)
print("parse OK, body:", len(p.body))
for stmt in p.body:
    print("  -", type(stmt).__name__)

from src.workflows.builtins import list_functions
print("builtin funcs:", sorted(list_functions()))

from src.workflows.implementations import list_workflows
print("workflows:", list_workflows())
