import os, pathlib

exts = ['.py', '.md']
skip = {'__pycache__', '.idea', '.qoder', '.agents', 'node_modules', '.venv', 'logs', '.tooling', 'android', 'tests'}
results = {e: {'files': 0, 'lines': 0, 'chars': 0} for e in exts}
details = {e: [] for e in exts}

for root, dirs, files in os.walk('.'):
    if any(s in root for s in skip):
        continue
    for f in files:
        p = pathlib.Path(root) / f
        if p.suffix in exts:
            try:
                content = p.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            lines = len(content.splitlines())
            chars = len(content)
            results[p.suffix]['files'] += 1
            results[p.suffix]['lines'] += lines
            results[p.suffix]['chars'] += chars
            details[p.suffix].append((str(p), lines, chars))

for e in exts:
    print(f"\n{'='*60}")
    print(f"  {e} 文件统计")
    print(f"{'='*60}")
    sorted_files = sorted(details[e], key=lambda x: x[1], reverse=True)
    print(f"  {'文件':<50} {'行数':>6} {'字符':>8}")
    print(f"  {'-'*50} {'-'*6} {'-'*8}")
    for path, lines, chars in sorted_files:
        print(f"  {path:<50} {lines:>6} {chars:>8}")
    print(f"  {'-'*50} {'-'*6} {'-'*8}")
    print(f"  {'合计':<50} {results[e]['lines']:>6} {results[e]['chars']:>8}")
    print(f"  文件数: {results[e]['files']}")

total_files = sum(r['files'] for r in results.values())
total_lines = sum(r['lines'] for r in results.values())
total_chars = sum(r['chars'] for r in results.values())
print(f"\n{'='*60}")
print(f"  总计: {total_files} 个文件, {total_lines} 行, {total_chars} 字符")
print(f"{'='*60}")
