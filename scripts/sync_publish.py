#!/usr/bin/env python3
"""从本地安装目录生成可发布的 skill 副本，并自动脱敏。

用法：
  python3 scripts/sync_publish.py            # 同步 + 脱敏 + 全仓库自检
  python3 scripts/sync_publish.py --check    # 只自检，不写入

为什么需要它：本地安装的 skill 里有真实的 baseId / tableId / fieldId
和同事姓名，手工复制到仓库极易漏删。

设计要点：脚本自身**不包含**任何真实标识。脱敏规则在运行时从两处推导：
  1. 本地私有配置 references/target-table.json（baseId、tableId、视图、字段 ID）
  2. 本地私有黑名单 references/local-denylist.txt（姓名、组织等）
两个文件都在排除项中，不会进入发布副本。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLISH_DIR = REPO_ROOT / "skills" / "order-screenshot-to-aitable"
DEFAULT_SOURCE = Path.home() / ".codex" / "skills" / "order-screenshot-to-aitable"

# 复制时排除的本地私有文件与缓存
EXCLUDE_NAMES = {
    "target-table.json",
    "local-denylist.txt",
    "__pycache__",
    ".DS_Store",
}

TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".sh"}

# 至少这么长的十六进制/小写字母数字串，视为疑似 corpId 之类标识
GENERIC_ID_PATTERN = re.compile(r"\bding[a-z0-9]{16,}\b")


def load_config(source: Path) -> dict | None:
    cfg = source / "references" / "target-table.json"
    if not cfg.exists():
        return None
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"本地配置不是有效 JSON：{cfg}：{exc}")


def field_placeholder(key: str) -> str:
    return "YOUR_" + re.sub(r"[^A-Z0-9]+", "_", key.upper()) + "_FIELD_ID"


def build_rules(source: Path) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """运行时推导脱敏规则。返回 (规则, 必须消失的值, 提示)。"""
    rules: list[tuple[str, str]] = []
    secrets: list[str] = []
    notes: list[str] = []

    config = load_config(source)
    if config is None:
        notes.append(
            "未找到本地私有配置 references/target-table.json，"
            "只能依赖黑名单脱敏；建议先配置后再同步。"
        )
    else:
        target = config.get("target", {}) or {}
        pairs = [
            (target.get("baseId"), "YOUR_BASE_ID"),
            (target.get("tableId"), "YOUR_TABLE_ID"),
            (target.get("viewId"), "YOUR_VIEW_ID"),
        ]
        for key, spec in (config.get("fields") or {}).items():
            fid = (spec or {}).get("fieldId")
            if fid:
                pairs.append((fid, field_placeholder(key)))
        for index, spec in enumerate(config.get("readOnlyFields") or [], start=1):
            fid = (spec or {}).get("fieldId")
            if fid:
                pairs.append((fid, f"YOUR_READONLY_FIELD_{index}_ID"))

        for value, replacement in pairs:
            if not value or not isinstance(value, str):
                continue
            # 占位符本身不需要替换
            if value.startswith("YOUR_"):
                continue
            rules.append((re.escape(value), replacement))
            secrets.append(value)

    deny = source / "references" / "local-denylist.txt"
    if deny.exists():
        for raw in deny.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            value, replacement = (part.strip() for part in line.split("|", 1))
            if not value:
                continue
            rules.append((re.escape(value), replacement))
            secrets.append(value)
    else:
        notes.append("未找到本地黑名单 references/local-denylist.txt，姓名类信息不会被替换。")

    return rules, secrets, notes


def copy_tree(source: Path, dest: Path) -> list[Path]:
    copied: list[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.rglob("*")):
        if any(part in EXCLUDE_NAMES for part in item.relative_to(source).parts):
            continue
        rel = item.relative_to(source)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(target)
    return copied


def sanitize_file(path: Path, rules: list[tuple[str, str]]) -> int:
    if path.suffix not in TEXT_SUFFIXES:
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return 0
    original = text
    for pattern, replacement in rules:
        text = re.sub(pattern, replacement, text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return 1
    return 0


def scan_repo(secrets: list[str]) -> tuple[list[str], list[str]]:
    """扫描整个仓库（不只 skill 目录）。返回 (命中, 通用模式命中)。"""
    hits: list[str] = []
    generic: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(REPO_ROOT)
        for needle in secrets:
            if needle and needle in text:
                hits.append(f"{rel}: 命中「{needle}」")
        for match in GENERIC_ID_PATTERN.finditer(text):
            generic.append(f"{rel}: 疑似组织标识 {match.group(0)}")
    return hits, generic


def validate_example() -> list[str]:
    problems: list[str] = []
    example = PUBLISH_DIR / "references" / "target-table.example.json"
    if not example.exists():
        problems.append("缺少 references/target-table.example.json")
        return problems
    try:
        data = json.loads(example.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problems.append(f"示例配置不是有效 JSON：{exc}")
        return problems
    if data.get("target", {}).get("baseId") != "YOUR_BASE_ID":
        problems.append("示例配置的 baseId 不是占位符")
    leaked = PUBLISH_DIR / "references" / "target-table.json"
    if leaked.exists():
        problems.append("发布副本里存在真实配置 references/target-table.json")
    leaked2 = PUBLISH_DIR / "references" / "local-denylist.txt"
    if leaked2.exists():
        problems.append("发布副本里存在本地黑名单 references/local-denylist.txt")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="生成脱敏后的发布副本")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE),
                        help="本地已安装的 skill 目录")
    parser.add_argument("--check", action="store_true",
                        help="只检查当前仓库，不重新同步")
    args = parser.parse_args()

    source = Path(args.source)
    if args.check:
        if not source.is_dir():
            print(f"源目录不存在，无法推导脱敏规则：{source}", file=sys.stderr)
            return 2
        rules, secrets, notes = build_rules(source)
        hits, generic = scan_repo(secrets)
        problems = validate_example()
        ok = not hits and not generic and not problems
        print(json.dumps({
            "ok": ok,
            "checked_secrets": len(secrets),
            "leaks": hits,
            "generic_hits": generic,
            "problems": problems,
            "notes": notes,
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    if not source.is_dir():
        print(f"源目录不存在：{source}", file=sys.stderr)
        return 2

    rules, secrets, notes = build_rules(source)
    if PUBLISH_DIR.exists():
        shutil.rmtree(PUBLISH_DIR)
    copied = copy_tree(source, PUBLISH_DIR)
    changed = sum(sanitize_file(p, rules) for p in copied)

    hits, generic = scan_repo(secrets)
    problems = validate_example()
    if not secrets:
        problems.append("没有推导出任何脱敏规则，请检查本地配置与黑名单")

    result = {
        "ok": not hits and not generic and not problems,
        "copied_files": len(copied),
        "sanitized_files": changed,
        "checked_secrets": len(secrets),
        "leaks": hits,
        "generic_hits": generic,
        "problems": problems,
        "notes": notes,
        "publish_dir": str(PUBLISH_DIR),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
