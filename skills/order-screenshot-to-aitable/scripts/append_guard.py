#!/usr/bin/env python3
"""只增不改守卫：写入前后各取一次全表指纹，用机制证明原有记录未被改动。

用法：
  append_guard.py capture --out /tmp/baseline.json
  ... 执行 record create ...
  append_guard.py verify  --baseline /tmp/baseline.json --expect-added 1

只读：两个子命令都只调用查询接口。
退出码：0 = 校验通过；2 = 原有记录被删除或修改，或新增数量不符。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from _common import DEFAULT_CONFIG, load_config, run_dws


def fetch_all(base_id: str, table_id: str, page_limit: int,
              field_ids: str | None) -> tuple[dict[str, dict] | None, str | None]:
    args = [
        "aitable", "record", "query",
        "--base-id", base_id,
        "--table-id", table_id,
        "--all",
        "--page-limit", str(page_limit),
    ]
    if field_ids:
        args += ["--field-ids", field_ids]
    payload, err = run_dws(args, timeout=600)
    if err or not payload:
        return None, err or "查询返回为空"
    return {
        rec["recordId"]: rec.get("cells", {})
        for rec in payload.get("data", {}).get("records", [])
    }, None


def fingerprint(records: dict[str, dict]) -> str:
    canonical = json.dumps(records, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="多维表只增不改守卫")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="写入前：记录全表基线")
    cap.add_argument("--out", required=True, help="基线文件路径")
    cap.add_argument("--config", default=str(DEFAULT_CONFIG))
    cap.add_argument("--page-limit", type=int, default=0, help="最大翻页数，0 表示不限")
    cap.add_argument("--field-ids", help="只跟踪指定字段，默认全部")

    ver = sub.add_parser("verify", help="写入后：比对基线")
    ver.add_argument("--baseline", required=True, help="基线文件路径")
    ver.add_argument("--config", default=str(DEFAULT_CONFIG))
    ver.add_argument("--page-limit", type=int, default=0)
    ver.add_argument("--field-ids", help="须与 capture 时保持一致")
    ver.add_argument("--expect-added", type=int, default=None,
                     help="预期新增记录数；不传则只校验原有记录未被改动")

    args = parser.parse_args()
    config = load_config(args.config)
    base_id = config["target"]["baseId"]
    table_id = config["target"]["tableId"]

    if args.command == "capture":
        records, err = fetch_all(base_id, table_id, args.page_limit, args.field_ids)
        if err:
            print(json.dumps({"ok": False, "blockers": [f"采集基线失败：{err}"]},
                             ensure_ascii=False, indent=2))
            return 2
        baseline = {
            "baseId": base_id,
            "tableId": table_id,
            "count": len(records),
            "hash": fingerprint(records),
            "records": records,
        }
        Path(args.out).write_text(
            json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(
            {"ok": True, "captured": len(records), "hash": baseline["hash"],
             "baseline": args.out},
            ensure_ascii=False, indent=2))
        return 0

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    before: dict[str, dict] = baseline["records"]
    hash_before = fingerprint(before)
    baseline_tampered = (
        baseline.get("hash") is not None and baseline["hash"] != hash_before
    )
    current, err = fetch_all(base_id, table_id, args.page_limit, args.field_ids)
    if err:
        print(json.dumps({"ok": False, "blockers": [f"读取当前数据失败：{err}"]},
                         ensure_ascii=False, indent=2))
        return 2

    missing = sorted(set(before) - set(current))
    added = sorted(set(current) - set(before))
    changed = sorted(
        rid for rid in set(before) & set(current)
        if before[rid] != current[rid]
    )

    blockers: list[str] = []
    if baseline_tampered:
        blockers.append("基线文件自身已被改动，校验结果不可信，请重新 capture")
    if missing:
        blockers.append(f"原有记录被删除：{missing}")
    if changed:
        blockers.append(f"原有记录被修改：{changed}")
    if args.expect_added is not None and len(added) != args.expect_added:
        blockers.append(
            f"新增数量为 {len(added)}，期望 {args.expect_added}：{added}"
        )

    print(json.dumps(
        {
            "ok": not blockers,
            "blockers": blockers,
            "count_before": len(before),
            "count_after": len(current),
            "added": added,
            "missing": missing,
            "changed": changed,
            "hash_before": hash_before,
            "hash_after": fingerprint(current),
            "unchanged": not missing and not changed,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if not blockers else 2


if __name__ == "__main__":
    sys.exit(main())
