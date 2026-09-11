# 实体抽取结构（归档入库用）

抽取原则：**只抽单据/文字里写着的**。缺失写 `null`，不要推断；数值拿不准标 `[待核对]`。

## 顶层结构

```json
{
  "document": {
    "type": "体检报告 | 化验单 | 处方 | 门诊病历 | 出院小结 | 影像报告 | 其他",
    "date": "YYYY-MM-DD",
    "institution": "机构名",
    "department": "科室",
    "patient": "患者名",
    "raw_image": ["attachments/2026-09-11_血常规_原图.png"]
  },
  "symptoms":       [ { "name": "腹痛", "severity": "轻|中|重", "duration": "3天", "onset": "起病时间/诱因", "progression": "好转|稳定|加重" } ],
  "vitals":         [ { "type": "bp|hr|temp|rr|spo2|weight|height|bmi", "value": "145/92", "unit": "mmHg", "timestamp": "本次测量" } ],
  "lab_values":     [ { "name": "甲状腺球蛋白抗体", "value": 123.4, "unit": "IU/mL", "ref_range": "<115", "flag": "normal|high|low|borderline|unknown", "category": "血常规|生化|免疫|凝血|尿常规|其他" } ],
  "medications":    [ { "name": "优甲乐", "specification": "50μg×100片", "dose": "50μg", "frequency": "qd", "route": "po", "duration": "长期", "context": "new|existing|stopped", "note": "医嘱原文" } ],
  "diagnoses":      [ { "name": "甲状腺功能减退症", "status": "confirmed|suspected", "source": "医生原文", "quote": "「考虑甲减」" } ],
  "procedures":     [ { "name": "甲状腺超声", "site": "颈部", "purpose": "结节随访", "result": "3mm 结节，定级 3 类" } ],
  "action_items":   [ { "type": "复查|取药|随访|待问医生", "detail": "3 个月后复查甲功", "due": "2026-12-11", "urgency": "routine|soon" } ],
  "questions":      [ "这个药需要空腹吃吗" ],
  "uncertain":      [ "参考区间未印全，第 4–6 项按常规区间标注 [待核对]" ]
}
```

## 字段纪律

- `diagnoses.status`：**只有医生明确写出「确诊/诊断」才用 `confirmed`**；原话是「考虑/疑似/待排」一律 `suspected`，并在 `quote` 里保留原文。
- `lab_values.flag`：只在单据给出了参考区间或有明确箭头（↑/↓/H/L）时才判定；否则 `unknown`。
- `medications.frequency` 保留原文（`bid` 与「每日两次」都照抄），不要改写成别的表达。
- `action_items.due` 只取单据里写明的日期/时间窗；没写就留 `null`。
- 单位一律照抄（`IU/mL` 不要换算成 `mIU/L`）。
- 患者不明确（没写名字、可能是家人）时，先问再归档，不要默认。
