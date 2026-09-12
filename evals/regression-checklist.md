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
- [ ] `description` 包含触发场景，且说明不处理承接助教
- [ ] 结构校验通过

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/order-screenshot-to-aitable
```

## 脚本

- [ ] 四个脚本语法正确
- [ ] 缺少配置时给出可操作指引，不抛堆栈
- [ ] 预检与校验都不接受 `--assistant` 参数（该参数已移除）
- [ ] 写入文件只包含订单编号、下单时间、虚拟手机号三个字段
- [ ] 脚本中不存在人员检索相关调用

## 行为回归

按 `evals/test-prompts.md` 跑一遍，重点是：

- [ ] 正常流程能写入并回读正确
- [ ] 只给截图、不提助教时能正常执行
- [ ] 已存在的订单被跳过，不覆盖
- [ ] 写入前后指纹比对 `unchanged: true`

## 发布前最后一次

- [ ] 在真实表上以「只读 + 预演」跑完整流程，确认没有意外写入
- [ ] 确认表格记录数在测试前后一致
