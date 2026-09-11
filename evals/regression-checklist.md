# 发版前检查清单

同步发布副本或改动脚本后，按顺序过一遍。

## 仓库卫生

- [ ] `skills/order-screenshot-to-aitable/references/target-table.json` 不存在于仓库中
- [ ] 全仓库搜索无真实 baseId、tableId、fieldId、同事姓名
- [ ] 无 `__pycache__`、无 `.DS_Store`
- [ ] `target-table.example.json` 是有效 JSON 且只含占位符
- [ ] `LICENSE` 与 `README.md` 的项目名一致

## 技能结构

- [ ] `SKILL.md` frontmatter 只有 `name` 和 `description`
- [ ] `description` 包含触发场景，且说明缺助教时先追问
- [ ] 结构校验通过

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/order-screenshot-to-aitable
```

## 脚本

- [ ] 四个脚本语法正确
- [ ] 缺少配置时给出可操作指引，不抛堆栈
- [ ] 缺助教时预检与校验都返回 `next_action: ask_user_for_assistant`
- [ ] 缺助教时不生成写入文件
- [ ] 姓名不精确时被拒绝，不采用相似结果

## 行为回归

按 `evals/test-prompts.md` 跑一遍，重点是：

- [ ] 正常流程能写入并回读正确
- [ ] 缺助教时先追问、不写入
- [ ] 已存在的订单被跳过，不覆盖
- [ ] 写入前后指纹比对 `unchanged: true`
- [ ] 模糊姓名被拒绝

## 发布前最后一次

- [ ] 在真实表上以「只读 + 预演」跑完整流程，确认没有意外写入
- [ ] 确认表格记录数在测试前后一致
