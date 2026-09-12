# 教别人使用订单截图录单

这份文档写给第一次接触的人：需要装什么、需要准备什么、按什么顺序做。

## 一、先讲清楚分工

整套东西由四层组成，缺任何一层都跑不起来：

| 层 | 是什么 | 谁提供 |
|---|---|---|
| 执行工具 | `dws` 命令行，负责真正连接钉钉读写数据 | 钉钉官方开源 |
| 钉钉技能包 | 让 Codex 知道怎么调用 `dws` 的说明文件 | 随 `dws` 一起安装 |
| 录单技能 | 本仓库的 `order-screenshot-to-aitable`，定义录单流程和安全规则 | 本仓库 |
| 数据表 | 目标多维表，以及访问它的权限 | 使用者的钉钉组织 |

一句话：`dws` 是手，钉钉技能包是说明书，录单技能是流程，多维表是目的地。

## 二、三个前置条件

缺任何一条都无法开始，先确认再往下走。

**条件一：电脑上有 Codex。** 需要能读图，因为订单信息来源是截图。

**条件二：`dws` 已安装，且钉钉登录成功，且组织已开通 CLI 权限。**

第三点最容易被忽略。钉钉企业默认可能没有开放 CLI 访问，需要管理员在开发者后台开启。没开通时，登录会提示申请，管理员同意后再登录一次即可。

**条件三：对方账号对目标多维表有「可编辑」权限，且承接助教与写入账号在同一个组织内。**

只有查看权限时能读字段，但写不进去。助教字段是钉钉人员字段，跨组织写入一定失败。

## 三、安装步骤

### 第 1 步：安装 dws

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.sh | sh
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.ps1 | iex
```

装完验证：

```bash
dws --version
```

如果提示找不到命令，重开一个终端窗口，让 PATH 生效。

### 第 2 步：钉钉登录

```bash
dws auth login
```

浏览器会自动打开，选择组织并授权。远程或没有图形界面的环境用：

```bash
dws auth login --device
```

登录后确认状态：

```bash
dws auth status --format json
```

看到 `authenticated: true` 和正确的组织名就算成功。

### 第 3 步：安装钉钉技能包

`dws` 安装时默认已经带了多技能模式。确认一下：

```bash
ls ~/.codex/skills | grep dingtalk
```

应该能看到 `dingtalk-aitable`、`dingtalk-shared`、`dingtalk-aisearch`、`dingtalk-contact` 等。如果缺失，补装：

```bash
dws skill setup --mode multi --target codex
```

其中和录单直接相关的是这四个：

| 技能 | 作用 |
|---|---|
| `dingtalk-aitable` | 读写多维表的命令说明 |
| `dingtalk-shared` | 全局执行契约、错误处理与安全底线 |
| `dingtalk-aisearch` | 按姓名把承接助教解析成钉钉账号 |
| `dingtalk-contact` | 校验助教所属组织 |

### 第 4 步：安装录单技能

在 Codex 里直接粘贴：

```text
请使用 $skill-installer 安装：
https://github.com/fanjingtaoboy-dotcom/order-screenshot-to-aitable/tree/main/skills/order-screenshot-to-aitable
```

装完重启或刷新 Codex，然后用 `$order-screenshot-to-aitable` 调用。

### 第 5 步：配置自己的表格

仓库里只放示例配置，真实配置在本地生成：

```bash
cd ~/.codex/skills/order-screenshot-to-aitable
cp references/target-table.example.json references/target-table.json
```

需要读取四个值填进去。**不要凭记忆填字段 ID**，必须从接口实时读取。

先解析表格链接，拿到 baseId、tableId、viewId：

```bash
dws aitable +url-resolve --url "<你的多维表链接>" --verify --format json
```

再读真实字段：

```bash
dws aitable table get --base-id <baseId> --table-ids <tableId> --format json
```

从返回中找到订单编号、下单时间、收货号码、承接助教四个字段的 `fieldId`，填进 `target-table.json`。

### 第 6 步：预检

```bash
python3 scripts/preflight_check.py --assistant "<助教姓名>"
```

看到 `ok: true` 才算配置完成。这一步会检查登录态、字段类型、助教身份、目标表是否存在。

## 四、需要准备的材料

配置阶段需要：

| 材料 | 说明 |
|---|---|
| 多维表链接 | 浏览器打开目标表格后复制地址栏 |
| 字段 ID | 用上面的 `dws aitable table get` 读取 |
| 承接助教姓名 | 必须在写入账号所属组织内真实存在 |

日常使用只需要两样：**订单截图** 和 **这批订单给谁**。

## 五、日常使用流程

在 Codex 里发截图，并说明助教：

```text
把这几张截图里的订单录入多维表，这批给张老师。
```

然后它会按这个顺序执行，不需要使用者干预：

1. 预检表格与助教身份
2. 读图提取订单编号、下单时间、虚拟号
3. 校验位数、交叉核对、按订单编号去重
4. 写入前记录全表指纹
5. 追加新增记录
6. 写入后比对指纹并回读字段

最后会给出报告：新增几条、跳过几条、阻断几条及原因、原有记录是否未被改动。

### 忘记了写助教姓名

它会先追问，不会先录入：

```text
收到截图了。请告诉我这批订单分配给哪位助教来承接，我再录入。
```

补上姓名后它会从头执行。这是刻意设计：订单归错人比多问一句麻烦得多。

## 六、四种会停下来追问的情况

遇到下面这些，它会停下来等人，不会自行猜测：

| 情况 | 表现 |
|---|---|
| 没给助教姓名 | 追问助教是谁 |
| 姓名查不到 | 请确认姓名是否正确 |
| 姓名只是相似（如简称） | 把检索到的候选念出来确认 |
| 同名候选多个 | 列出候选让人选 |

## 七、必须提前知道的三条规则

**只增不改。** 唯一允许的写操作是新增记录。修改、删除、改字段、改表格结构全部禁止。写入前后会比对全表指纹，原有记录被改动会报出具体是哪几条。

**订单编号字段必须是文本类型。** 如果是数字类型，19 位订单号会被四舍五入，且不可逆。预检会拦住这种情况。

**已存在的订单会跳过。** 按订单编号去重，重复提交同一张截图不会产生重复记录，也不会覆盖原记录。

## 八、怎么确认装对了

按顺序检查，任何一步不过就别往下走。

```bash
# 1. 工具在位
dws --version

# 2. 登录有效
dws auth status --format json

# 3. 钉钉技能包在位
ls ~/.codex/skills | grep dingtalk

# 4. 录单技能在位
ls ~/.codex/skills | grep order-screenshot

# 5. 配置与权限都正确
cd ~/.codex/skills/order-screenshot-to-aitable
python3 scripts/preflight_check.py --assistant "<助教姓名>"
```

第 5 步返回 `ok: true` 就说明可以正式使用了。

## 九、常见卡点

| 现象 | 原因与处理 |
|---|---|
| 找不到 `dws` 命令 | 重开终端让 PATH 生效；或安装未成功，重装一次 |
| 登录时提示申请权限 | 组织未开通 CLI 访问，让管理员在开发者后台开启后重新登录 |
| 预检报字段类型错误 | 订单编号不是文本类型，先在表格里改类型或新建文本字段 |
| 预检报助教不可用 | 姓名打错、不在本组织，或同名多人 |
| 预检报字段不存在 | `fieldId` 填错或表格结构改了，重新读一次字段 |
| 写入失败提示无权限 | 当前账号对该表只有查看权限，需要编辑权限 |
| 记录写进去了但视图里看不到 | 该视图带有承接助教筛选条件，换到不带筛选的视图查看 |

## 十、换一个人使用时要注意

每个人的钉钉账号、组织、目标表格可能都不同。换人时这三样都要重新确认：

1. 用**自己的**账号登录 `dws`，不要共用登录态。
2. 重新生成自己的 `target-table.json`，不要照抄别人的字段 ID。
3. 确认自己组织里能查到要填的助教姓名。

如果对方用的是另一张表或另一套组织，配置阶段要走一遍第 5、6 步。
