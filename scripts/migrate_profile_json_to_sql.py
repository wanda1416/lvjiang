"""从 users/*.json 迁移 profile 数据到 SQLite

用法:
    python scripts/migrate_profile_json_to_sql.py

功能:
    - 扫描 config/session/users/ 下所有 JSON 文件
    - 提取每个文件的 profile 节点
    - 写入 config/session/profile.db（使用 ProfileDB）

幂等性:
    - 使用 INSERT OR IGNORE，重复执行不会覆盖已有数据
    - 如果 profile.db 不存在，会自动创建
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root / "src") not in sys.path:
    sys.path.insert(0, str(_project_root / "src"))

from lvjiang.apps.yysls.profile.profile_db import ProfileDB  # noqa: E402
from lvjiang.constants import SESSION_CONFIG_DIR, USERS_DIR  # noqa: E402

# 旧 JSON 中的 model type → 新 DB 中的 model type
_TYPE_RENAME = {
    "daily": "quota",
    "realtime": "regen",
    "resource": "stock",
}


def migrate(users_dir: Path | None = None, db_path: Path | None = None) -> int:
    """执行 JSON → SQLite 迁移，返回导入的 entry 数量"""
    users_dir = users_dir or USERS_DIR
    db_path = db_path or SESSION_CONFIG_DIR / "profile.db"

    if not users_dir.exists():
        print(f"错误: users 目录不存在: {users_dir}")
        return 0

    db = ProfileDB(db_path)

    imported = 0
    for user_file in sorted(users_dir.glob("*.json")):
        username = user_file.stem
        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  跳过 {user_file.name}: {e}")
            continue

        profile = data.get("profile")
        if not isinstance(profile, dict):
            print(f"  跳过 {user_file.name}: 无 profile 节点")
            continue

        for type_, keys in profile.items():
            if not isinstance(keys, dict):
                continue
            # 映射旧类型名到新类型名
            db_type = _TYPE_RENAME.get(type_, type_)
            for key, entry in keys.items():
                if not isinstance(entry, dict):
                    continue
                value = entry.get("value", 0)
                if value is None:
                    value = 0
                updated_at = entry.get("updated_at", "")
                if updated_at is None:
                    updated_at = ""

                # 直接写入 DB（INSERT OR IGNORE 保证幂等）
                conn = db._connect()
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO profile_entries "
                        "(username, type, key, value, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (username, db_type, key, float(value), updated_at),
                    )
                    conn.commit()
                finally:
                    conn.close()
                imported += 1

        print(f"  已导入: {user_file.name} (用户: {username})")

    return imported


def main() -> None:
    print("Profile 数据迁移: JSON → SQLite")
    print(f"  来源: {USERS_DIR}")
    print(f"  目标: {SESSION_CONFIG_DIR / 'profile.db'}")
    print()

    imported = migrate()

    print()
    if imported > 0:
        print(f"迁移完成: 共导入 {imported} 条 profile entry")
    else:
        print("迁移完成: 无数据需要导入")


if __name__ == "__main__":
    main()
