#!/usr/bin/env python3
"""校验截图提取结果，并生成可直接写入的 cells 结构。

只读：仅在传入 --check-table 时调用只读查询接口做重复检测。
退出码：0 = 存在可写入记录且无致命错误；1 = 存在致命错误。

输入 JSON（数组或 {"records": [...]}），每条：
{
  "orderNo": "6917731421701746343",
  "productOrderId": "6917731421701746343",   // 可选，用于交叉核对
  "orderTime": "2026-09-11 16:05:04",
  "phoneWithSuffix": "17851404569 [2079]",   // 或分开给 phone / suffix
  "recipientName": "杨先生",                  // 可选，仅用于核对
  "assistant": "张老师"                       // 可选，逐条覆盖
}

承接助教优先级：逐条 assistant > --assistant > 配置默认值。
使用配置默认值时会给出明确告警，避免静默错配。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from _common import (
    DEFAULT_CONFIG,
    fetch_auth,
    load_config,
    resolve_assistant,
    run_dws,
)

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d",
    "%Y-%m-%d",
)

# dws record create 单次上限
MAX_RECORDS_PER_CALL = 100


def normalize_time(raw: str):
    """把多种写法的下单时间统一成 YYYY-MM-DD HH:mm:ss，失败返回 None。"""
    text = str(raw).strip().replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("T", " ").split("+")[0].strip()
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return None


def split_phone(record: dict):
    """返回 (phone, suffix, error)。"""
    combined = str(record.get("phoneWithSuffix") or "").strip()
    if combined:
        match = re.search(r"(\d{11})\D*(\d{2,6})", combined)
        if match:
            return match.group(1), match.group(2), None
        match = re.search(r"(\d{11})", combined)
        if match:
            return match.group(1), None, "缺少虚拟号后缀，请从浮层补全"
        return None, None, f"无法从「{combined}」解析出手机号"

    phone = str(record.get("phone") or "").strip()
    suffix = str(record.get("suffix") or "").strip()
    if not phone:
        return None, None, "缺少手机号"
    return phone, suffix or None, None


def find_existing(base_id: str, table_id: str, field_id: str,
                  order_nos: list[str], chunk: int = 20):
    """批量查询已存在的订单编号。返回 (existing_set, failures)。"""
    existing: set[str] = set()
    failures: list[tuple[str, str]] = []

    for start in range(0, len(order_nos), chunk):
        group = order_nos[start:start + chunk]
        filt = {
            "operator": "or",
            "operands": [
                {"operator": "eq", "operands": [field_id, no]} for no in group
            ],
        }
        payload, err = run_dws([
            "aitable", "record", "query",
            "--base-id", base_id, "--table-id", table_id,
            "--filters", json.dumps(filt, ensure_ascii=False),
            "--field-ids", field_id,
            "--limit", "100",
        ])
        if err or not payload:
            # 批量失败时逐条回退，避免误判为"不存在"而重复写入
            for no in group:
                one, oerr = run_dws([
                    "aitable", "record", "query",
                    "--base-id", base_id, "--table-id", table_id,
                    "--filters", json.dumps({
                        "operator": "and",
                        "operands": [
                            {"operator": "eq", "operands": [field_id, no]}
                        ],
                    }, ensure_ascii=False),
                    "--field-ids", field_id,
                    "--limit", "2",
                ])
                if oerr or not one:
                    failures.append((no, oerr or "查询返回为空"))
                    continue
                if one.get("data", {}).get("records"):
                    existing.add(no)
            continue

        for rec in payload.get("data", {}).get("records", []):
            value = rec.get("cells", {}).get(field_id)
            if value is None:
                continue
            existing.add(value if isinstance(value, str) else str(value))

    return existing, failures


def resolve_all(names: set[str], corp_id: str | None, expect_org: str | None):
    """批量解析人员，返回 (identities, errors)。"""
    identities: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for name in sorted(names):
        if not name:
            continue
        identity, err = resolve_assistant(name, corp_id=corp_id, expect_org=expect_org)
        if err:
            errors[name] = err
        else:
            identities[name] = identity
    return identities, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验订单提取结果")
    parser.add_argument("--input", required=True, help="提取结果 JSON 文件")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="目标表配置 JSON")
    parser.add_argument("--assistant", help="本次承接助教姓名；省略则用配置默认值")
    parser.add_argument("--check-table", action="store_true",
                        help="调用只读查询接口，检查订单编号是否已存在")
    parser.add_argument("--require-all", action="store_true",
                        help="严格模式：只要有一条被阻断就不允许写入")
    parser.add_argument("--respect-record-assistant", action="store_true",
                        help="允许逐条 assistant 字段覆盖本次指定；默认忽略以免误配")
    parser.add_argument("--out", help="可写入记录的输出文件前缀")
    parser.add_argument("--max-per-file", type=int, default=MAX_RECORDS_PER_CALL,
                        help=f"每个写入文件最大条数，默认 {MAX_RECORDS_PER_CALL}")
    args = parser.parse_args()

    config = load_config(args.config)
    fields_cfg = config["fields"]
    defaults = config.get("defaults", {})
    base_id = config["target"]["baseId"]
    table_id = config["target"]["tableId"]
    expect_len = int(defaults.get("orderNoLength", 19))
    config_default_assistant = defaults.get("assistantName", "")

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        print(json.dumps({"ok": False, "fatal": ["输入 JSON 不是记录数组"]},
                         ensure_ascii=False, indent=2))
        return 1

    auth, auth_err = fetch_auth()
    if auth_err:
        print(json.dumps({"ok": False, "fatal": [f"钉钉登录态不可用：{auth_err}"]},
                         ensure_ascii=False, indent=2))
        return 1
    corp_id = auth.get("corp_id")
    corp_name = auth.get("corp_name")

    # 收集本次涉及的全部助教姓名。助教必须由本次使用者显式给出，
    # 不允许静默落到配置默认值，否则会把订单记到错误的人名下。
    batch_assistant = args.assistant or ""
    assistant_source = "cli" if args.assistant else "unset"
    record_level = {
        str(r.get("assistant")).strip() for r in records
        if r.get("assistant") and args.respect_record_assistant
    }
    if not batch_assistant and not record_level:
        print(json.dumps({
            "ok": False,
            "next_action": "ask_user_for_assistant",
            "fatal": [
                "缺少承接助教姓名，已停止，未写入任何数据。"
                "请先向使用者追问本次订单分配给哪位助教，拿到姓名后再执行。"
                + (
                    f"若使用者确认仍是常用助教，也要显式写作 --assistant "
                    f"{config_default_assistant}"
                    if config_default_assistant else ""
                )
            ],
        }, ensure_ascii=False, indent=2))
        return 1

    wanted: set[str] = set()
    if batch_assistant:
        wanted.add(batch_assistant)
    wanted.update(record_level)

    identities, resolve_errors = resolve_all(wanted, corp_id, corp_name)

    fatal: list[str] = []
    warnings: list[str] = []
    ask_user = False

    if batch_assistant and batch_assistant in resolve_errors:
        ask_user = True
        fatal.append(
            f"承接助教「{batch_assistant}」无法使用：{resolve_errors[batch_assistant]}"
        )

    items: list[dict] = []
    seen: dict[str, int] = {}
    blocked: list[dict] = []

    for index, record in enumerate(records):
        problems: list[str] = []
        order_no = str(record.get("orderNo") or "").strip()

        if not order_no:
            problems.append("缺少订单编号")
        elif not order_no.isdigit():
            problems.append(f"订单编号含非数字字符：{order_no}")
        elif len(order_no) != expect_len:
            problems.append(
                f"订单编号为 {len(order_no)} 位，期望 {expect_len} 位，疑似识别错误"
            )
        elif order_no in seen:
            problems.append(f"批内重复，与第 {seen[order_no] + 1} 条相同")

        product_id = str(record.get("productOrderId") or "").strip()
        if product_id and order_no and product_id != order_no:
            problems.append(f"商品单ID（{product_id}）与订单编号不一致，交叉核对失败")

        order_time = normalize_time(record.get("orderTime") or "")
        if not order_time:
            problems.append(f"下单时间无法识别：{record.get('orderTime')}")

        phone, suffix, perr = split_phone(record)
        if perr:
            problems.append(perr)
        else:
            if not re.fullmatch(r"1\d{10}", phone or ""):
                problems.append(f"手机号格式异常：{phone}")
            if not suffix:
                problems.append("缺少虚拟号后缀")
            elif not re.fullmatch(r"\d{2,6}", suffix):
                problems.append(f"虚拟号后缀格式异常：{suffix}")
            elif len(suffix) != 4:
                warnings.append(f"订单 {order_no} 的虚拟号后缀为 {len(suffix)} 位，请确认")

        record_assistant = str(record.get("assistant") or "").strip()
        if record_assistant and args.respect_record_assistant:
            if record_assistant in resolve_errors:
                problems.append(
                    f"逐条指定的助教「{record_assistant}」不可用："
                    f"{resolve_errors[record_assistant]}"
                )
            chosen = record_assistant
            if batch_assistant and record_assistant != batch_assistant:
                warnings.append(
                    f"订单 {order_no} 按逐条指定使用「{record_assistant}」，"
                    f"与本次批次默认「{batch_assistant}」不同"
                )
        else:
            chosen = batch_assistant
            if record_assistant and not args.respect_record_assistant:
                warnings.append(
                    f"订单 {order_no} 自带的 assistant 字段已忽略，"
                    f"统一使用本次指定「{batch_assistant}」"
                )

        if order_no and order_no not in seen:
            seen[order_no] = index

        item = {
            "index": index + 1,
            "orderNo": order_no,
            "orderTime": order_time,
            "phoneWithSuffix": f"{phone} [{suffix}]" if phone and suffix else None,
            "recipientName": record.get("recipientName"),
            "assistant": chosen,
            "problems": problems,
        }
        items.append(item)
        if problems:
            blocked.append({
                "index": index + 1,
                "orderNo": order_no or None,
                "problems": problems,
            })

    # 服务端去重：已存在的记录一律跳过，绝不覆盖
    existing: set[str] = set()
    if args.check_table:
        candidates = [i["orderNo"] for i in items if not i["problems"] and i["orderNo"]]
        if candidates:
            existing, dedup_failures = find_existing(
                base_id, table_id, fields_cfg["orderNo"]["fieldId"], candidates
            )
            for no, reason in dedup_failures:
                fatal.append(f"订单 {no} 去重查询失败：{reason}")
                for item in items:
                    if item["orderNo"] == no:
                        item["problems"].append("去重查询失败")
        if fatal:
            # 去重不可靠时不得写入，否则可能重复
            existing = set()

    if blocked and args.require_all:
        fatal.extend(
            f"第 {b['index']} 条（{b['orderNo'] or '无编号'}）：{'；'.join(b['problems'])}"
            for b in blocked
        )

    writeable = []
    for item in items:
        if item["problems"] or item["orderNo"] in existing:
            continue
        identity = identities.get(item["assistant"]) if item["assistant"] else None
        if identity is None:
            item["problems"].append(f"助教「{item['assistant']}」未解析")
            blocked.append({
                "index": item["index"],
                "orderNo": item["orderNo"],
                "problems": [f"助教「{item['assistant']}」未解析"],
            })
            continue
        writeable.append({
            "orderNo": item["orderNo"],
            "orderTime": item["orderTime"],
            "phoneWithSuffix": item["phoneWithSuffix"],
            "recipientName": item["recipientName"],
            "assistant": item["assistant"],
            "cells": {
                fields_cfg["orderNo"]["fieldId"]: item["orderNo"],
                fields_cfg["orderTime"]["fieldId"]: item["orderTime"],
                fields_cfg["phone"]["fieldId"]: item["phoneWithSuffix"],
                fields_cfg["assistant"]["fieldId"]: [
                    {"userId": identity["userId"], "corpId": identity["corpId"]}
                ],
            },
        })

    if not writeable and not fatal:
        fatal.append("没有可写入的记录：可能全部已存在或被阻断")

    # 生成写入文件；超过单次上限自动分片
    write_plan: list[dict] = []
    if args.out and writeable and not fatal:
        out_path = Path(args.out)
        limit = max(1, args.max_per_file)
        chunks = [
            writeable[i:i + limit] for i in range(0, len(writeable), limit)
        ]
        for idx, chunk in enumerate(chunks, start=1):
            if len(chunks) == 1:
                path = out_path
            else:
                path = out_path.with_name(
                    f"{out_path.stem}-part{idx}{out_path.suffix or '.json'}"
                )
            path.write_text(
                json.dumps([{"cells": w["cells"]} for w in chunk],
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_plan.append({"file": str(path), "count": len(chunk)})

    result = {
        "ok": not fatal,
        "next_action": "ask_user_for_assistant" if ask_user else (
            "review_blocked_records" if blocked else "proceed"
        ),
        "summary": {
            "total": len(items),
            "writeable": len(writeable),
            "skipped_existing": len(existing),
            "blocked": len(blocked),
        },
        "assistant": {
            "requested": batch_assistant,
            "source": assistant_source,
            "identity": identities.get(batch_assistant),
        },
        "assistants": list(identities.values()),
        "fatal": fatal,
        "blocked_records": blocked,
        "warnings": warnings,
        "skipped": sorted(existing),
        "write_plan": write_plan,
        "records": writeable[:20],
        "records_truncated": len(writeable) > 20,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
