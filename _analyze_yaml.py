"""分析 YAML 文件结构"""
import yaml, sys
sys.stdout.reconfigure(encoding='utf-8')

for fname in ['config/i18n/zh_CN.yaml', 'config/i18n/en_US.yaml']:
    with open(fname, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    print(f"\n=== {fname} ===")
    print(f"Top-level keys: {list(data.keys())}")
    
    for key in data:
        section = data[key]
        if isinstance(section, dict):
            # 计算叶子节点数
            def count_leaves(d):
                n = 0
                for v in d.values():
                    if isinstance(v, dict):
                        n += count_leaves(v)
                    else:
                        n += 1
                return n
            leaves = count_leaves(section)
            print(f"  {key}: {leaves} entries, sub-keys: {list(section.keys())[:10]}")
        else:
            print(f"  {key}: {type(section).__name__}")
