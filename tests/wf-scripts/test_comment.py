import sys
sys.path.insert(0, '.')
from lvjiang.workflows.grammar import parse_text

code = """loop 100
   scan [scene].[field] as $result
   if $result.[field] contains "test"
       # 这是注释
       goto label1
   end
end
@label1
collect $result
"""

try:
    p = parse_text(code)
    print(f"OK: {len(p.body)} statements")
except Exception as e:
    print(f"Error: {e}")
