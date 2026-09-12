"""order-screenshot-to-aitable 共用工具。

本模块只封装只读调用；写入动作永远由调用方显式发起，且仅限 record create。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_DIR / "references" / "target-table.json"
EXAMPLE_CONFIG = SKILL_DIR / "references" / "target-table.example.json"

# 服务端生成或关联带出的字段类型，一律不可写
NON_WRITABLE_TYPES = {
    "autoNumber",
    "filterUp",
    "lookup",
    "formula",
    "createdTime",
    "lastModifiedTime",
    "createdBy",
    "lastModifiedBy",
    "creator",
    "lastModifier",
}


def run_dws(args: list[str], timeout: int = 120):
    """执行 dws 命令，返回 (payload, error)。调用方负责保证 argv 只读。"""
    try:
        proc = subprocess.run(
            ["dws", *args, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "未找到 dws 命令：请先安装 DWS CLI 并完成钉钉登录"
    except subprocess.TimeoutExpired:
        return None, "dws 命令超时"

    out = (proc.stdout or "").strip()
    if not out:
        return None, f"dws 无输出（stderr: {(proc.stderr or '').strip()[:200]}）"
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        start = out.find("{")
        if start >= 0:
            try:
                return json.loads(out[start:]), None
            except json.JSONDecodeError:
                pass
        return None, f"无法解析 dws 输出：{out[:200]}"


def load_config(path: str | None = None) -> dict:
    """读取目标表配置。

    配置缺失时给出可操作的指引，而不是抛出堆栈：首次使用需要从示例文件
    复制一份真实配置，填写自己的 baseId / tableId / fieldId。
    """
    target = Path(path) if path else DEFAULT_CONFIG
    if not target.exists():
        hint = (
            f"未找到目标表配置：{target}\n"
            f"请先复制示例配置并填入你自己的信息：\n"
            f"  cp {EXAMPLE_CONFIG} {DEFAULT_CONFIG}\n"
            f"然后按提示填写 baseId、tableId 与各字段 ID。"
            if EXAMPLE_CONFIG.exists() else f"未找到目标表配置：{target}"
        )
        raise SystemExit(hint)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"目标表配置格式错误（{target}）：{exc}")


def fetch_auth() -> tuple[dict | None, str | None]:
    payload, err = run_dws(["auth", "status"])
    if err or not payload or not payload.get("authenticated"):
        return None, err or "当前钉钉登录态不可用"
    return payload, None
