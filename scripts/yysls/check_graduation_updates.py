"""检查 / 同步 yyslsxzs.cn 上的毕业率计算器表格。

站点 ``/api/files`` 提供文件名、版本、更新时间、大小和下载地址；Excel 自身的
核心属性（title / version 等）由作者填写且基本为空，不能作为版本依据。因此本
工具用「远端元数据 + SHA-256」判断 ``data/excel`` 下的表格是否最新，并把最近
一次核对结果写入 ``data/excel/manifest.json``，作为 Excel 之外补充的元数据。

用法::

    python scripts/check_graduation_updates.py                 # 只比对元数据
    python scripts/check_graduation_updates.py --verify         # 下载远端表做 SHA-256 校验
    python scripts/check_graduation_updates.py --update         # 下载较新表，旧表移入「过期版本」

``--update`` 只替换 Excel 源表，不会重建 ``config/system/yysls/graduation``
下的 JSON 模型；源表更新后仍需重新导入毕业率方案。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import yaml

ROOT = Path(__file__).resolve().parents[2]
EXCEL_DIR = ROOT / "data" / "excel"
SCHOOLS_FILE = ROOT / "config/system/yysls/game_config/schools.yaml"
MANIFEST_FILE = EXCEL_DIR / "manifest.json"
ARCHIVE_DIRNAME = "过期版本"

API_URL = "https://yyslsxzs.cn/api/files"
SITE_ORIGIN = "https://yyslsxzs.cn"
USER_AGENT = "lvjiang-graduation-update-check/1.0"
MANIFEST_SCHEMA_VERSION = 1

STATUS_UP_TO_DATE = "最新"
STATUS_STALE = "过期"
STATUS_MISSING = "本地缺失"
STATUS_AMBIGUOUS = "本地多份"
STATUS_LOCAL_MODIFIED = "本地已改"
STATUS_UNKNOWN = "待校验"

#: 这些状态表示本地还没有跟上站点，退出码为 1。
_FAILED_STATUSES = {
    STATUS_STALE, STATUS_MISSING, STATUS_AMBIGUOUS, STATUS_LOCAL_MODIFIED,
}


@dataclass(frozen=True)
class RemoteWorkbook:
    file_id: int
    name: str
    version: str
    updated_at: str
    size: int
    download_url: str
    school: str
    level: int


@dataclass(frozen=True)
class LocalWorkbook:
    school: str
    level: int
    path: Path
    version: str


@dataclass
class CheckResult:
    remote: RemoteWorkbook
    locals: list[LocalWorkbook] = field(default_factory=list)
    local_sha256: str | None = None
    remote_sha256: str | None = None
    payload: bytes | None = None
    status: str = STATUS_UNKNOWN
    note: str = ""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_name(remote: RemoteWorkbook) -> str:
    """给下载文件补上版本后缀，保持 data/excel 既有的自描述命名。

    站点上不少文件叫「…计算器.xlsx」，版本只存在于 API 字段里；直接落盘会让
    转换器 ``_version()`` 只能得到 unknown。这里按远端版本补成
    ``…计算器4.0.xlsx``；文件名已带同一版本时不重复追加。
    """
    lowered = remote.name.lower()
    if lowered.endswith(".xlsx"):
        stem = remote.name[:-5]
    elif lowered.endswith("xlsx"):
        stem = remote.name[:-4]
    else:
        stem = remote.name
    stem = stem.rstrip(". ")
    if not remote.version:
        return f"{stem}.xlsx"
    if re.search(rf"{re.escape(remote.version)}$", stem):
        return f"{stem}.xlsx"
    return f"{stem}{remote.version}.xlsx"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_school_map() -> list[tuple[str, str]]:
    """返回 (文件名里使用的紧凑名, 标准流派名)，长名优先。"""
    document = yaml.safe_load(SCHOOLS_FILE.read_text(encoding="utf-8")) or {}
    schools = document.get("schools") or {}
    if not isinstance(schools, dict) or not schools:
        raise RuntimeError(f"游戏配置缺少 schools 定义：{SCHOOLS_FILE}")
    pairs = [
        (str(name).replace("·", ""), str(name))
        for name in schools
    ]
    return sorted(pairs, key=lambda item: len(item[0]), reverse=True)


def identify_school(name: str, school_map: list[tuple[str, str]]) -> str | None:
    matches = [full for compact, full in school_map if compact in name]
    return matches[0] if len(matches) == 1 else None


def level_of(name: str) -> int | None:
    match = re.search(r"(\d+)阶", name)
    return int(match.group(1)) if match else None


def version_of_filename(name: str) -> str:
    match = re.search(r"(\d+\.\d+)\.xlsx$", name, re.IGNORECASE)
    return match.group(1) if match else ""


def _request(url: str, *, timeout: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_remote(
    school_map: list[tuple[str, str]],
) -> tuple[list[RemoteWorkbook], list[str]]:
    """拉取站点文件清单；返回毕业率表格与已跳过的非毕业率条目。"""
    payload = json.loads(_request(API_URL, timeout=30).decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("站点 /api/files 返回的不是文件数组")
    workbooks: list[RemoteWorkbook] = []
    skipped: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("deleted_at") or item.get("visible") is False:
            continue
        if item.get("kind") != "file":
            continue
        name = str(item.get("name") or "")
        # 站点上的文件名并不总是规范（如「…计算器xlsx」少一个点），
        # 以 extension 字段为准，文件名后缀只做兜底。
        if (str(item.get("extension") or "").lower() != "xlsx"
                and not name.lower().endswith("xlsx")):
            continue
        school = identify_school(name, school_map)
        level = level_of(name)
        if school is None or level is None:
            skipped.append(name)
            continue
        workbooks.append(RemoteWorkbook(
            file_id=int(item.get("id") or 0),
            name=name,
            version=str(item.get("version") or ""),
            updated_at=str(item.get("updated_at") or ""),
            size=int(item.get("size_bytes") or 0),
            download_url=str(item.get("download_url") or ""),
            school=school,
            level=level,
        ))
    return workbooks, skipped


def download(remote: RemoteWorkbook) -> bytes:
    url = remote.download_url
    if url.startswith("/"):
        url = SITE_ORIGIN + url
    data = _request(url, timeout=120)
    if remote.size and len(data) != remote.size:
        raise RuntimeError(
            f"{remote.name} 下载大小 {len(data)} 与站点登记 {remote.size} 不一致")
    return data


def scan_local(
    school_map: list[tuple[str, str]], levels: set[int],
) -> tuple[dict[tuple[str, int], list[LocalWorkbook]], dict[int, int]]:
    """扫描 data/excel/<等级>级/*.xlsx，跳过「过期版本」。"""
    index: dict[tuple[str, int], list[LocalWorkbook]] = {}
    counts: dict[int, int] = {}
    for directory in sorted(EXCEL_DIR.glob("*级")):
        if not directory.is_dir():
            continue
        match = re.fullmatch(r"(\d+)级", directory.name)
        if match is None:
            continue
        level = int(match.group(1))
        counts[level] = len(list(directory.glob("*.xlsx")))
        if levels and level not in levels:
            continue
        for path in sorted(directory.glob("*.xlsx")):
            school = identify_school(path.name, school_map)
            if school is None:
                continue
            index.setdefault((school, level), []).append(LocalWorkbook(
                school=school,
                level=level,
                path=path,
                version=version_of_filename(path.name),
            ))
    return index, counts


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_FILE.is_file():
        return {"schema_version": MANIFEST_SCHEMA_VERSION, "entries": []}
    try:
        document = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{MANIFEST_FILE} 不是合法 JSON：{exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise RuntimeError(f"{MANIFEST_FILE} 结构不正确，缺少 entries 数组")
    return document


def manifest_index(manifest: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    index: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in manifest.get("entries", []):
        if not isinstance(entry, dict):
            continue
        school = entry.get("school")
        level = entry.get("level")
        if isinstance(school, str) and isinstance(level, int):
            index[(school, level)] = entry
    return index


def _status_from_metadata(
    result: CheckResult, entry: dict[str, Any],
) -> tuple[str, str]:
    remote = result.remote
    local = result.locals[0]
    local_size = local.path.stat().st_size
    reasons: list[str] = []
    if remote.version and local.version and remote.version != local.version:
        reasons.append(f"版本 {local.version} → {remote.version}")
    if remote.size and remote.size != local_size:
        reasons.append(f"大小 {local_size} → {remote.size}")
    if (entry.get("remote_updated_at")
            and entry["remote_updated_at"] != remote.updated_at):
        reasons.append("远端更新时间变化")
    if reasons:
        return STATUS_STALE, "；".join(reasons)
    if entry.get("local_sha256") and entry["local_sha256"] != result.local_sha256:
        return STATUS_LOCAL_MODIFIED, "本地文件与上次记录不同"
    if (entry.get("local_sha256")
            and entry.get("remote_sha256")
            and entry["local_sha256"] == entry["remote_sha256"]):
        return STATUS_UP_TO_DATE, "元数据与上次校验记录一致"
    return STATUS_UNKNOWN, "元数据一致，但尚未做 SHA-256 校验"


def check(
    remotes: list[RemoteWorkbook],
    local_index: dict[tuple[str, int], list[LocalWorkbook]],
    entries: dict[tuple[str, int], dict[str, Any]],
    *,
    verify: bool,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for remote in sorted(remotes, key=lambda item: (item.level, item.school)):
        result = CheckResult(remote=remote, locals=list(
            local_index.get((remote.school, remote.level), [])))
        entry = entries.get((remote.school, remote.level), {})
        if not result.locals:
            result.status = STATUS_MISSING
            result.note = "站点有表，data/excel 下没有对应文件"
            results.append(result)
            continue
        if len(result.locals) > 1:
            result.status = STATUS_AMBIGUOUS
            result.note = "、".join(item.path.name for item in result.locals)
            results.append(result)
            continue
        result.local_sha256 = _sha256_file(result.locals[0].path)
        if verify:
            result.payload = download(remote)
            result.remote_sha256 = _sha256_bytes(result.payload)
            if result.remote_sha256 == result.local_sha256:
                result.status = STATUS_UP_TO_DATE
                result.note = "SHA-256 一致"
            elif (entry.get("local_sha256")
                  and entry["local_sha256"] != result.local_sha256):
                result.status = STATUS_LOCAL_MODIFIED
                result.note = "本地文件被手工修改，且与远端不同"
            else:
                result.status = STATUS_STALE
                result.note = "SHA-256 不一致"
        else:
            result.status, result.note = _status_from_metadata(result, entry)
        results.append(result)
    return results


def _archive(path: Path) -> Path:
    """把旧表移进同级的「过期版本」，重名时按内容去重或追加序号。"""
    archive_dir = path.parent / ARCHIVE_DIRNAME
    archive_dir.mkdir(exist_ok=True)
    source_sha = _sha256_file(path)
    target = archive_dir / path.name
    if target.exists():
        if _sha256_file(target) == source_sha:
            path.unlink()
            return target
        index = 2
        while True:
            candidate = archive_dir / f"{path.stem}-{index}{path.suffix}"
            if not candidate.exists():
                target = candidate
                break
            if _sha256_file(candidate) == source_sha:
                path.unlink()
                return candidate
            index += 1
    shutil.move(str(path), str(target))
    return target


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".xlsx.tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def update(
    results: list[CheckResult], *, force: bool,
    progress: TextIO | None = None,
) -> list[str]:
    """下载较新的表并归档旧表；返回更新过的流派列表。"""
    if progress is None:
        progress = sys.stdout
    changed: list[str] = []
    for result in results:
        if result.status == STATUS_UP_TO_DATE:
            continue
        if result.status == STATUS_AMBIGUOUS:
            print(f"跳过 {result.remote.school} {result.remote.level}级："
                  "本地存在多份同流派表格，请先手动清理", file=sys.stderr)
            continue
        if result.status == STATUS_LOCAL_MODIFIED and not force:
            print(f"跳过 {result.remote.school} {result.remote.level}级："
                  "本地文件被手工修改过，确认要覆盖时加 --force", file=sys.stderr)
            continue
        payload = result.payload if result.payload is not None else download(result.remote)
        for local in result.locals:
            archived = _archive(local.path)
            print(f"归档 {local.path.name} → {archived.relative_to(EXCEL_DIR)}",
                  file=progress)
        target = EXCEL_DIR / f"{result.remote.level}级" / local_name(result.remote)
        _write_atomic(target, payload)
        result.locals = [LocalWorkbook(
            school=result.remote.school,
            level=result.remote.level,
            path=target,
            version=result.remote.version or version_of_filename(target.name),
        )]
        result.payload = payload
        result.local_sha256 = _sha256_bytes(payload)
        result.remote_sha256 = result.local_sha256
        result.status = STATUS_UP_TO_DATE
        result.note = f"已下载 {result.remote.version or '最新版本'}"
        changed.append(f"{result.remote.school} {result.remote.level}级")
        print(f"写入 {target.relative_to(EXCEL_DIR)}", file=progress)
    return changed


def write_manifest(
    results: list[CheckResult], previous: dict[str, Any],
) -> None:
    previous_entries = manifest_index(previous)
    recorded_at = _now()
    entries: list[dict[str, Any]] = []
    for result in results:
        remote = result.remote
        local = result.locals[0] if len(result.locals) == 1 else None
        old = previous_entries.get((remote.school, remote.level), {})
        # 本地多份时无法确定该记录哪一份，保留上次记录，避免元数据被清空。
        keep_old = len(result.locals) > 1
        file_name = (local.path.name if local
                     else old.get("file") if keep_old else None)
        rel_path = (str(local.path.relative_to(EXCEL_DIR)).replace("\\", "/")
                    if local else old.get("path") if keep_old else None)
        local_version = (local.version if local
                         else old.get("local_version") if keep_old else None)
        local_sha = (result.local_sha256 if local
                     else old.get("local_sha256") if keep_old else None)
        remote_sha = result.remote_sha256
        if remote_sha is None and (
            old.get("remote_sha256")
            and old.get("remote_updated_at") == remote.updated_at
            and old.get("remote_size") == remote.size
        ):
            # 本次只比对了元数据；远端没有变化，沿用上次记录的哈希。
            remote_sha = old["remote_sha256"]
        entries.append({
            "school": remote.school,
            "level": remote.level,
            "file": file_name,
            "path": rel_path,
            "local_version": local_version,
            "local_sha256": local_sha,
            "remote_id": remote.file_id,
            "remote_name": remote.name,
            "remote_version": remote.version,
            "remote_updated_at": remote.updated_at,
            "remote_size": remote.size,
            "remote_sha256": remote_sha,
        })
    document = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": API_URL,
        "recorded_at": recorded_at,
        "entries": sorted(entries, key=lambda item: (item["level"], item["school"])),
    }
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1
               for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _print_table(results: list[CheckResult]) -> None:
    headers = ("流派", "等级", "本地版本", "远端版本", "远端更新时间", "状态", "说明")
    rows = [
        (
            result.remote.school,
            str(result.remote.level),
            (result.locals[0].version or "—") if len(result.locals) == 1 else "—",
            result.remote.version or "—",
            result.remote.updated_at.replace("T", " ") or "—",
            result.status,
            result.note,
        )
        for result in results
    ]
    widths = [
        max(_display_width(row[column]) for row in (headers, *rows))
        for column in range(len(headers))
    ]
    print("  ".join(_pad(value, width)
                    for value, width in zip(headers, widths, strict=True)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(_pad(value, width)
                        for value, width in zip(row, widths, strict=True)))


def _print_summary(
    results: list[CheckResult], skipped: list[str],
    local_counts: dict[int, int], remote_levels: set[int], verified: bool,
) -> None:
    counts = Counter(result.status for result in results)
    parts = [
        f"{status} {counts[status]}"
        for status in (
            STATUS_UP_TO_DATE, STATUS_STALE, STATUS_MISSING,
            STATUS_AMBIGUOUS, STATUS_LOCAL_MODIFIED, STATUS_UNKNOWN,
        )
        if counts[status]
    ]
    print(("汇总：" + "，".join(parts)) if parts
          else "汇总：站点没有可比对的毕业率表格")
    if skipped:
        print("已跳过非毕业率条目：" + "、".join(skipped))
    for level in sorted(set(local_counts) - remote_levels):
        print(f"提示：data/excel/{level}级 有 {local_counts[level]} 份本地表格，"
              "站点未提供该等级，未纳入比对。")
    if not verified and counts[STATUS_UNKNOWN]:
        print("提示：加 --verify 下载远端表格并做 SHA-256 校验，可消除「待校验」。")
    if counts[STATUS_STALE] or counts[STATUS_MISSING]:
        print("提示：加 --update 下载较新表格；旧表会移入同级「过期版本」目录。")


def _result_payload(result: CheckResult) -> dict[str, Any]:
    local = result.locals[0] if len(result.locals) == 1 else None
    return {
        "school": result.remote.school,
        "level": result.remote.level,
        "local_file": local.path.name if local else None,
        "local_version": local.version if local else None,
        "local_sha256": result.local_sha256,
        "remote_id": result.remote.file_id,
        "remote_name": result.remote.name,
        "remote_version": result.remote.version,
        "remote_updated_at": result.remote.updated_at,
        "remote_size": result.remote.size,
        "remote_sha256": result.remote_sha256,
        "status": result.status,
        "note": result.note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查 data/excel 下的毕业率表格是否与 yyslsxzs.cn 一致",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="下载远端文件做 SHA-256 校验（默认只比对元数据）")
    parser.add_argument(
        "--update", action="store_true",
        help="下载较新的表格，旧表移入同级「过期版本」目录")
    parser.add_argument(
        "--force", action="store_true",
        help="本地文件被手工修改过时仍然覆盖")
    parser.add_argument(
        "--write-manifest", action="store_true",
        help="把本次核对结果写入 data/excel/manifest.json")
    parser.add_argument(
        "--level", type=int, action="append",
        help="只处理指定等级，可重复，例如 --level 115")
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出，便于脚本消费")
    args = parser.parse_args()

    verify = args.verify or args.update
    try:
        school_map = load_school_map()
        remotes, skipped = fetch_remote(school_map)
        all_remote_levels = {item.level for item in remotes}
        levels = set(args.level) if args.level else all_remote_levels
        local_index, local_counts = scan_local(school_map, levels)
        manifest = load_manifest()
        results = check(
            [item for item in remotes if item.level in levels],
            local_index, manifest_index(manifest), verify=verify,
        )
        changed: list[str] = []
        if args.update:
            changed = update(
                results, force=args.force,
                progress=sys.stderr if args.json else sys.stdout)
        if args.write_manifest or args.update:
            write_manifest(results, manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"检查失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        counts = Counter(result.status for result in results)
        print(json.dumps({
            "checked_at": _now(),
            "source": API_URL,
            "verified": verify,
            "entries": [_result_payload(result) for result in results],
            "summary": dict(counts),
            "updated": changed,
            "skipped": skipped,
        }, ensure_ascii=False, indent=2))
    else:
        _print_table(results)
        _print_summary(results, skipped, local_counts, all_remote_levels, verify)
        if changed:
            print("已更新：" + "、".join(changed))
            print("注意：Excel 源表已替换，毕业率 JSON 模型尚未重建；"
                  "请到「游戏设置 → 对应流派 → 导入毕业率方案」重新导入。")
        if args.write_manifest or args.update:
            print(f"元数据已写入 {MANIFEST_FILE.relative_to(ROOT)}")

    failed = any(result.status in _FAILED_STATUSES for result in results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
