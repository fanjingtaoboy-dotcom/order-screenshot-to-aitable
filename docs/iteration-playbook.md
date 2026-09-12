# 适配到你自己的表格

这份文档说明怎么把 skill 从「示例配置」换成「你自己的表格」。

## 你需要准备的

| 项目 | 说明 |
|---|---|
| 多维表链接 | 形如 `https://alidocs.dingtalk.com/i/nodes/<baseId>?sheetId=<tableId>` |
| 目标视图 | 写入后希望在哪张视图看到记录 |
| 字段对应关系 | 订单编号、下单时间、收货号码分别对应哪个字段 |

## 步骤一：解析 baseId 与 tableId

```bash
dws aitable +url-resolve --url "<多维表链接>" --verify --format json
```

从返回里取 `baseId`、`tableId`、`viewId`。`--verify` 会顺带确认目标真实存在。

## 步骤二：读取真实字段 ID

```bash
dws aitable table get --base-id <baseId> --table-ids <tableId> --format json
```

返回里会列出全部字段及其 `fieldId` 和 `type`。再对目标字段做一次精确读取：

```bash
dws aitable field get --base-id <baseId> --table-id <tableId> \
  --field-ids <字段ID1>,<字段ID2> --format json
```

**字段 ID 必须现场读取，不要凭记忆填写。** 表格改过结构后旧 ID 会失效。

## 步骤三：检查两个关键前提

**订单编号字段必须是文本类型。** 如果是 `number`，19 位订单号会被四舍五入成近似值，且不可逆。这种情况需要先建一个文本字段，或在表格里改类型后再录入。

**确认哪些字段是只读的。** `autoNumber`、`filterUp`、`lookup`、`formula` 以及创建人/创建时间这类系统字段都不能写入。预检会替你拦住，但提前知道更省事。

## 步骤四：写入配置

```bash
cd ~/.codex/skills/order-screenshot-to-aitable
cp references/target-table.example.json references/target-table.json
```

按上一步读到的真实值填写 `target-table.json`。这个文件已被 `.gitignore` 排除，不会被提交。

## 步骤五：验证

```bash
python3 scripts/preflight_check.py
```

期望看到 `ok: true`，并且 `context` 里带上你的表名。

常见阻断及含义：

| 阻断信息 | 含义 |
|---|---|
| 字段「订单编号」当前类型为 number，要求 text | 订单编号字段类型不对，先改表格 |
| 字段「XX」不存在，配置可能已过期 | fieldId 填错或表格结构变了，重新读一次 |
| 钉钉登录态不可用 | 需要先 `dws auth login` |

## 换成别人的组织

换组织时要注意两件事：

1. 写入账号和目标表必须在同一个组织下。
2. 换组织后重新跑一遍预检，不要沿用旧配置。

## 改动 skill 本身之后

改完 `~/.codex/skills/order-screenshot-to-aitable` 里的内容后，用仓库自带的同步脚本发布：

```bash
cd <本仓库>
python3 scripts/sync_publish.py          # 复制 + 脱敏 + 全仓库自检
python3 scripts/sync_publish.py --check  # 只自检
```

脚本会在运行时从两个本地私有文件推导脱敏规则：

| 文件 | 提供什么 |
|---|---|
| `~/.codex/skills/order-screenshot-to-aitable/references/target-table.json` | 真实 baseId、tableId、viewId、字段 ID |
| `~/.codex/skills/order-screenshot-to-aitable/references/local-denylist.txt` | 姓名、组织等需要替换的词 |

这两个文件都在排除项里，不会进入仓库。

换了新的表格或新的同事姓名后，记得更新这两个文件，否则新出现的标识不会被替换。

同步后按 `evals/regression-checklist.md` 过一遍。
