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


def resolve_assistant(name: str, corp_id: str | None = None,
                      expect_org: str | None = None,
                      require_exact: bool = True):
    """把姓名字符串解析为当前组织内唯一人员，返回 (identity, error)。

    默认要求姓名完全一致：语义检索会把简称模糊匹配到某个全名，
    若直接采信就可能把订单记到错误的人名下。不完全一致时返回错误，
    由调用方追问使用者确认。
    """
    keyword = " ".join(str(name).split())
    payload, err = run_dws(
        ["aisearch", "person", "--keyword", keyword, "--dimension", "name"]
    )
    if err or not payload:
        return None, f"解析人员「{keyword}」失败：{err}"

    uniq = {c["userId"]: c for c in payload.get("result", []) if c.get("userId")}
    if not uniq:
        return None, f"当前组织中未找到「{keyword}」"
    if len(uniq) > 1:
        names = [
            f"{c.get('author')}（{(c.get('meta') or {}).get('position', '')}）"
            for c in uniq.values()
        ]
        return None, f"「{keyword}」匹配到多个人员，需要人工指定：{names}"

    if require_exact:
        exact = [
            c for c in uniq.values()
            if " ".join(str(c.get("author") or "").split()) == keyword
        ]
        if not exact:
            found = [
                f"{c.get('author')}（{(c.get('meta') or {}).get('position', '')}）"
                for c in uniq.values()
            ]
            return None, (
                f"没有找到与「{keyword}」完全同名的助教。"
                f"语义检索给出的是：{found}。"
                f"请与使用者确认准确的助教姓名后再执行。"
            )
        uniq = {c["userId"]: c for c in exact}

    person = next(iter(uniq.values()))
    user_id = person["userId"]
    org_name = None
    detail, derr = run_dws(["contact", "user", "get", "--ids", user_id])
    if not derr and detail:
        for row in detail.get("result", []):
            model = row.get("orgEmployeeModel") or {}
            org_name = model.get("orgName") or org_name

    if expect_org and org_name and org_name != expect_org:
        return None, f"「{keyword}」属于「{org_name}」，与当前组织「{expect_org}」不一致"

    return {
        "name": person.get("author"),
        "userId": user_id,
        "corpId": corp_id,
        "orgName": org_name,
    }, None
