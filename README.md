# 订单截图入多维表 Skill

作者：范范之辈

这个 Codex skill 用来把订单后台的截图，安全地录入钉钉 AI 表格（多维表）。

你给它一批订单截图，它负责读取截图里的订单编号、下单时间、收货人虚拟号信息，校验去重之后追加到表格里，并在写入前后验证表格原有内容一条都没被改动。

## 它解决什么问题

手工从订单后台抄录到多维表，慢、容易抄错，而且 19 位订单号很容易被表格或脚本改成近似值。

这个 skill 把整个过程拆成可校验的步骤，并且把「只增不改」做成机制，而不是一句叮嘱：

1. **写入前预检**：核对表格字段类型与可写字段是否有效。
2. **截图提取**：按固定规则读取订单编号、下单时间、虚拟号后缀和完整号码。
3. **校验去重**：位数检查、交叉核对、格式校验，已有的订单自动跳过。
4. **写入前后指纹比对**：比对全表记录指纹，原有记录被删或被改会精确报出是哪几条。
5. **写入后回读**：逐字段核对实际落库的值。

## 三条硬规则

- 唯一允许的写操作是「新增记录」。修改、删除、改字段、改表格结构、改权限全部禁用。
- 订单编号字段必须是文本类型。数字类型会把 19 位订单号四舍五入，且不可逆。
- 已存在的订单一律跳过，不更新、不覆盖、不补全。

承接助教不在本 skill 的范围内：不询问、不解析、不写入。新记录的该列为空，需要指派时在表内手工完成。

## 前置条件

- Codex（具备读图能力）
- DWS CLI，并完成钉钉登录
- 钉钉技能包：`dingtalk-aitable`、`dingtalk-shared`
- 当前账号对目标多维表有**编辑**权限

## 安装

> 第一次使用、或者要教别人使用，先看 [docs/onboarding.md](docs/onboarding.md)：
> 那里按顺序讲清楚了装什么、要哪些链接、怎么验收。

推荐直接在 Codex 里粘贴：

```text
请使用 $skill-installer 安装：
https://github.com/fanjingtaoboy-dotcom/order-screenshot-to-aitable/tree/main/skills/order-screenshot-to-aitable
```

也可以下载本仓库后，把 `skills/order-screenshot-to-aitable` 放到你的 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
cp -R skills/order-screenshot-to-aitable ~/.codex/skills/
```

重启或刷新 Codex 后，使用 `$order-screenshot-to-aitable` 调用。

## 配置你自己的表格

仓库里只提供示例配置，真实配置在本地：

```bash
cd ~/.codex/skills/order-screenshot-to-aitable
cp references/target-table.example.json references/target-table.json
```

然后填写这些值：

| 字段 | 从哪里来 |
|---|---|
| `baseId` / `tableId` | 多维表链接，或 `dws aitable +url-resolve --url "<链接>"` |
| `viewId` | 目标视图，可用 `dws aitable table get` 查看 |
| 各 `fieldId` | 必须来自实时 `dws aitable field get`，不要凭记忆填 |

填完先跑一次预检：

```bash
python3 scripts/preflight_check.py
```

预检通过再正式使用。

## 怎么用

直接发截图：

```text
把这几张截图里的订单录入多维表。
```

不需要提供助教姓名，这个 skill 不处理助教分派。

## 执行流程

```bash
# 第 0 步：只读预检
python3 scripts/preflight_check.py

# 第 2 步：校验 + 去重，生成写入文件
python3 scripts/validate_records.py \
  --input <提取结果>.json \
  --check-table \
  --out /tmp/order-write.json

# 第 3 步：写入前记录全表基线
python3 scripts/append_guard.py capture --out /tmp/order-baseline.json

# 第 4 步：新增记录（唯一允许的写操作）
dws aitable record create --base-id <baseId> --table-id <tableId> \
  --records-file /tmp/order-write.json --format json

# 第 5 步：核对原有记录未被改动
python3 scripts/append_guard.py verify \
  --baseline /tmp/order-baseline.json --expect-added <实际新增条数>
```

## 已知边界

- **承接助教留空**。本 skill 只写订单编号、下单时间、虚拟手机号三列。如果表里有按承接助教筛选的子视图，新记录不会出现在这些视图里，需要先指派助教。
- **去重按订单编号进行**。已存在的记录会被跳过，不会被更新或覆盖。
- **历史数据无法自动修复**。如果订单编号过去存在数字字段里，末位已被四舍五入破坏，改成文本类型也不会还原原始值。
- **虚拟号有效期通常 29 天**，表里存的是虚拟号，不是真实手机号。
- **截图含姓名和手机号**，属于个人信息，请控制表格共享范围。

## 目录结构

```text
.
├── LICENSE
├── README.md
├── docs/
│   ├── onboarding.md              # 教别人使用：装什么、按什么步骤
│   └── iteration-playbook.md      # 如何适配到自己的表格/组织
├── evals/
│   ├── golden-cases.md            # 预期行为
│   ├── regression-checklist.md    # 发版前检查项
│   └── test-prompts.md            # 可复用的测试提示词
└── skills/
    └── order-screenshot-to-aitable/
        ├── SKILL.md
        ├── agents/openai.yaml
        ├── references/
        │   ├── extraction-rules.md
        │   └── target-table.example.json
        └── scripts/
            ├── _common.py
            ├── preflight_check.py
            ├── validate_records.py
            └── append_guard.py
```

## 许可

CC BY 4.0，详见 [LICENSE](LICENSE)。
