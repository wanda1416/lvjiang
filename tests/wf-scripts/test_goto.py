import sys
sys.path.insert(0, '.')
from lvjiang.workflows.grammar import parse_text

code = """
$var = 1
@label1
log concat("var = ", $var)
if $var == 1
   $var = 2
   goto label1
else
   goto label2
end
@label2
collect $var
"""

try:
    p = parse_text(code)
    print(f"OK: {len(p.body)} statements")
except Exception as e:
    print(f"Error: {e}")
