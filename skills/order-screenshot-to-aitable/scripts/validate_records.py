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
  "remark": "客户要求改地址"                  // 可选，订单备注
}

写入范围：订单编号、下单时间、虚拟手机号，以及可选的订单备注。
承接助教不在本 skill 的处理范围内，既不读取也不写入。

订单备注是可选的，两种提供方式：
1. 在输入 JSON 的某条记录里给 "remark" 字段，表示这条订单的备注。
2. 用 --remark "<订单编号>=<备注内容>"，可重复多次。

备注必须能明确对到某条订单。对应不上时不会猜，而是停下来要求确认。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from _common import DEFAULT_CONFIG, load_config, run_dws

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

# 写入时实际使用的字段键；配置里缺少任何一个都会在加载后报错
WRITE_FIELD_KEYS = ("orderNo", "orderTime", "phone")

# 可选字段：配置里没有就静默跳过，不影响主流程
OPTIONAL_FIELD_KEYS = ("remark",)

# 订单备注长度上限；超出时阻断，避免误贴大段无关文本
MAX_REMARK_LENGTH = 500


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


def parse_remark_args(values: list[str] | None):
    """解析 --remark 参数。

    支持两种写法：
      "<订单编号>=<备注内容>"  明确指定是哪条订单
      "<备注内容>"             不指定订单，仅当本次只有一条订单时可用

    返回 (mapped, unmapped, errors)：
      mapped   {订单编号: 备注内容}
      unmapped 未指定订单的备注列表
      errors   格式问题
    """
    mapped: dict[str, str] = {}
    unmapped: list[str] = []
    errors: list[str] = []

    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        if "=" in text:
            order_no, content = text.split("=", 1)
            order_no = order_no.strip()
            content = content.strip()
            if not order_no:
                errors.append(f"备注缺少订单编号：{text}")
                continue
            if not content:
                errors.append(f"备注内容为空：{text}")
                continue
            if order_no in mapped:
                errors.append(f"订单 {order_no} 提供了多条备注，无法确定用哪条")
                continue
            mapped[order_no] = content
        else:
            unmapped.append(text)

    return mapped, unmapped, errors


def normalize_remark(value) -> str | None:
    """清洗备注文本。空值返回 None，表示这条订单没有备注。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def main() -> int:
    parser = argparse.ArgumentParser(description="校验订单提取结果")
    parser.add_argument("--input", required=True, help="提取结果 JSON 文件")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="目标表配置 JSON")
    parser.add_argument("--check-table", action="store_true",
                        help="调用只读查询接口，检查订单编号是否已存在")
    parser.add_argument("--require-all", action="store_true",
                        help="严格模式：只要有一条被阻断就不允许写入")
    parser.add_argument("--out", help="可写入记录的输出文件")
    parser.add_argument("--max-per-file", type=int, default=MAX_RECORDS_PER_CALL,
                        help=f"每个写入文件最大条数，默认 {MAX_RECORDS_PER_CALL}")
    parser.add_argument("--remark", action="append", default=None,
                        help='订单备注，可重复。写法 "<订单编号>=<备注内容>"；'
                             '本次只有一条订单时可省略编号直接写内容')
    args = parser.parse_args()

    config = load_config(args.config)
    fields_cfg = config["fields"]
    defaults = config.get("defaults", {})
    base_id = config["target"]["baseId"]
    table_id = config["target"]["tableId"]
    expect_len = int(defaults.get("orderNoLength", 19))

    missing_fields = [k for k in WRITE_FIELD_KEYS if k not in fields_cfg]
    if missing_fields:
        print(json.dumps({
            "ok": False,
            "next_action": "fix_blockers",
            "fatal": [f"目标表配置缺少必需字段：{missing_fields}"],
        }, ensure_ascii=False, indent=2))
        return 1

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        print(json.dumps({
            "ok": False,
            "next_action": "fix_blockers",
            "fatal": ["输入 JSON 不是记录数组"],
        }, ensure_ascii=False, indent=2))
        return 1

    fatal: list[str] = []
    warnings: list[str] = []
    items: list[dict] = []
    seen: dict[str, int] = {}
    blocked: list[dict] = []

    # 解析命令行备注。这里只做格式校验，与订单的绑定放在后面统一处理。
    cli_mapped, cli_unmapped, remark_errors = parse_remark_args(args.remark)
    fatal.extend(remark_errors)

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

        if order_no and order_no not in seen:
            seen[order_no] = index

        record_remark = normalize_remark(record.get("remark"))
        if record_remark and order_no in cli_mapped:
            problems.append(
                f"订单 {order_no} 同时提供了记录内备注和命令行备注，无法确定用哪条"
            )
        remark = record_remark or cli_mapped.get(order_no)
        if remark and len(remark) > MAX_REMARK_LENGTH:
            problems.append(
                f"订单备注过长（{len(remark)} 字，上限 {MAX_REMARK_LENGTH} 字），"
                f"疑似误贴内容"
            )

        items.append({
            "index": index + 1,
            "orderNo": order_no,
            "orderTime": order_time,
            "phoneWithSuffix": f"{phone} [{suffix}]" if phone and suffix else None,
            "recipientName": record.get("recipientName"),
            "remark": remark,
            "problems": problems,
        })
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

    # 绑定未指定订单的备注：只有在本次订单唯一时才允许自动对应，
    # 多订单时无法判断属于哪一条，必须停下来问，不能猜。
    if cli_unmapped:
        valid_orders = [
            i["orderNo"] for i in items
            if i["orderNo"] and not i["problems"] and i["orderNo"] not in existing
        ]
        if len(cli_unmapped) > 1:
            fatal.append(
                f"提供了 {len(cli_unmapped)} 条未指定订单的备注，无法确定对应关系。"
                f"请按「订单编号=备注内容」的格式逐条说明。"
            )
        elif len(valid_orders) == 1:
            target_no = valid_orders[0]
            content = cli_unmapped[0]
            if len(content) > MAX_REMARK_LENGTH:
                fatal.append(
                    f"订单备注过长（{len(content)} 字，上限 {MAX_REMARK_LENGTH} 字）"
                )
            else:
                for item in items:
                    if item["orderNo"] == target_no:
                        if item["remark"] and item["remark"] != content:
                            fatal.append(
                                f"订单 {target_no} 已有备注，与本次提供的备注冲突，"
                                f"无法确定用哪条"
                            )
                        else:
                            item["remark"] = content
        else:
            fatal.append(
                f"备注没有指定属于哪条订单，而本次待写入订单有 "
                f"{len(valid_orders)} 条，无法确定对应关系。"
                f"请按「订单编号=备注内容」的格式说明是哪一单。"
            )

    # 命令行里指明了订单、但该订单不在本次批次中，说明编号写错了
    if cli_mapped:
        batch_orders = {i["orderNo"] for i in items if i["orderNo"]}
        for order_no, content in cli_mapped.items():
            if order_no not in batch_orders:
                fatal.append(
                    f"备注指定的订单 {order_no} 不在本次截图提取结果中，"
                    f"请核对订单编号"
                )
            elif order_no in existing:
                warnings.append(
                    f"订单 {order_no} 已存在于表中，随附的备注不会写入（不覆盖既有记录）"
                )

    if blocked and args.require_all:
        fatal.extend(
            f"第 {b['index']} 条（{b['orderNo'] or '无编号'}）：{'；'.join(b['problems'])}"
            for b in blocked
        )

    remark_cfg = fields_cfg.get("remark")
    remark_enabled = bool(remark_cfg and remark_cfg.get("fieldId"))
    if not remark_enabled and any(
        i["remark"] for i in items
    ):
        fatal.append(
            "本次提供了订单备注，但目标表配置里没有 remark 字段。"
            "请在 references/target-table.json 里补充订单备注字段的 fieldId，"
            "或确认这张表是否真的有该列。"
        )

    writeable = []
    for item in items:
        if item["problems"] or item["orderNo"] in existing:
            continue
        cells = {
            fields_cfg["orderNo"]["fieldId"]: item["orderNo"],
            fields_cfg["orderTime"]["fieldId"]: item["orderTime"],
            fields_cfg["phone"]["fieldId"]: item["phoneWithSuffix"],
        }
        # 没有备注就不写这一列，保持与原有行为一致，也不产生空值覆盖风险
        if remark_enabled and item["remark"]:
            cells[remark_cfg["fieldId"]] = item["remark"]
        writeable.append({
            "orderNo": item["orderNo"],
            "orderTime": item["orderTime"],
            "phoneWithSuffix": item["phoneWithSuffix"],
            "recipientName": item["recipientName"],
            "remark": item["remark"],
            "cells": cells,
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
        "next_action": "review_blocked_records" if blocked else "proceed",
        "summary": {
            "total": len(items),
            "writeable": len(writeable),
            "skipped_existing": len(existing),
            "blocked": len(blocked),
            "with_remark": sum(1 for w in writeable if w["remark"]),
        },
        "fatal": fatal,
        "blocked_records": blocked,
        "warnings": warnings,
        "remarks": [
            {"orderNo": w["orderNo"], "remark": w["remark"]}
            for w in writeable if w["remark"]
        ],
        "skipped": sorted(existing),
        "write_plan": write_plan,
        "records": writeable[:20],
        "records_truncated": len(writeable) > 20,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
