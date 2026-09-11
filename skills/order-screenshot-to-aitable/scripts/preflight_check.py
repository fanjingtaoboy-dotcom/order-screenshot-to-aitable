#!/usr/bin/env python3
"""只读预检：确认目标多维表满足「只增不改」写入的前置条件。

本脚本只调用只读接口，绝不写入、修改或删除任何数据。
退出码：0 = 全部通过；2 = 存在阻断项。
"""

from __future__ import annotations

import argparse
import json
import sys

from _common import (
    DEFAULT_CONFIG,
    NON_WRITABLE_TYPES,
    fetch_auth,
    load_config,
    resolve_assistant,
    run_dws,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="订单入表只读预检")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="目标表配置 JSON")
    parser.add_argument("--assistant", help="本次承接助教姓名；省略则校验配置默认值")
    args = parser.parse_args()

    blockers: list[str] = []
    warnings: list[str] = []
    context: dict = {}

    config = load_config(args.config)
    target = config["target"]
    fields_cfg = config["fields"]
    defaults = config.get("defaults", {})
    base_id = target["baseId"]
    table_id = target["tableId"]

    # 1. 登录态与组织
    auth, err = fetch_auth()
    if err:
        blockers.append(f"钉钉登录态不可用：{err}")
    else:
        context["corpId"] = auth.get("corp_id")
        context["corpName"] = auth.get("corp_name")
        context["operator"] = auth.get("user_name")

    # 2. 字段存在性与类型（订单编号必须是文本）
    fields_payload, err = run_dws(
        ["aitable", "field", "get", "--base-id", base_id, "--table-id", table_id,
         "--field-ids", ",".join(c["fieldId"] for c in fields_cfg.values())]
    )
    if err or not fields_payload:
        blockers.append(f"读取字段定义失败：{err}")
    else:
        live_fields = {
            item["fieldId"]: item
            for item in fields_payload.get("data", {}).get("fields", [])
        }
        for key, cfg in fields_cfg.items():
            live = live_fields.get(cfg["fieldId"])
            if live is None:
                blockers.append(
                    f"字段「{cfg['name']}」（{cfg['fieldId']}）不存在，配置可能已过期"
                )
                continue
            if live.get("type") != cfg["requiredType"]:
                blockers.append(
                    f"字段「{cfg['name']}」当前类型为 {live.get('type')}，"
                    f"要求 {cfg['requiredType']}"
                )
            if live.get("type") in NON_WRITABLE_TYPES:
                blockers.append(f"字段「{cfg['name']}」属于只读类型，不能写入")
            context.setdefault("fields", {})[key] = {
                "fieldId": live["fieldId"],
                "name": live.get("fieldName"),
                "type": live.get("type"),
            }

    # 3. 承接助教必须由本次使用者显式指定，随后解析为组织内唯一人员。
    #    缺失时必须追问，绝不静默退回配置默认值，否则会把订单记到错误的人名下。
    assistant_name = args.assistant
    example = defaults.get("assistantName") or ""
    context["assistantSource"] = "cli" if assistant_name else "missing"
    ask_user = False
    if not assistant_name:
        ask_user = True
        hint = f"；例如 --assistant {example}" if example else ""
        blockers.append(
            f"缺少承接助教姓名。请先向使用者追问本次订单分配给哪位助教，"
            f"拿到姓名后再执行，不要先录入{hint}"
        )
    else:
        identity, rerr = resolve_assistant(
            assistant_name,
            corp_id=context.get("corpId"),
            expect_org=context.get("corpName"),
        )
        if rerr:
            ask_user = True
            blockers.append(f"承接助教「{assistant_name}」不可用：{rerr}")
        else:
            context["assistant"] = identity

    # 4. 目标表身份校验
    table_payload, err = run_dws(
        ["aitable", "table", "get", "--base-id", base_id, "--table-ids", table_id]
    )
    if err or not table_payload:
        blockers.append(f"读取目标数据表失败：{err}")
    else:
        tables = table_payload.get("data", {}).get("tables", [])
        table = next((t for t in tables if t.get("tableId") == table_id), None)
        if table is None:
            blockers.append(f"目标数据表 {table_id} 不存在或当前账号无权访问")
        else:
            context["tableName"] = table.get("tableName")
            if target.get("tableName") and table.get("tableName") != target["tableName"]:
                warnings.append(
                    f"数据表名称已变更为「{table.get('tableName')}」，"
                    f"配置里记录的是「{target['tableName']}」"
                )
            view_ids = {v.get("viewId") for v in table.get("views", [])}
            if target.get("viewId") and target["viewId"] not in view_ids:
                warnings.append(f"目标视图 {target['viewId']} 不在该数据表下")

    print(json.dumps(
        {"ok": not blockers,
         "next_action": (
             "ask_user_for_assistant" if ask_user
             else "fix_blockers" if blockers
             else "proceed"
         ),
         "blockers": blockers, "warnings": warnings, "context": context},
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not blockers else 2


if __name__ == "__main__":
    sys.exit(main())
