#!/usr/bin/env python3
"""药品库查询（本机个人使用）。输出刻意保持精简，避免把大段说明书塞进上下文。

用法：
  python3 q.py 布洛芬                 # 查药：命中概况（剂型清单 + 关键字段摘要）
  python3 q.py --full 布洛芬          # 同上，但不截断（按需才用）
  python3 q.py --field 禁忌 布洛芬     # 只看某个字段：适应症|禁忌|不良反应|相互作用|成分|用法
  python3 q.py -i 阿司匹林 布洛芬      # 查两药相互作用
  python3 q.py -s 头痛                # 全文搜索（名称/成分/适应症/不良反应），只回药名
  python3 q.py -n 布洛芬              # 只要规范名 + 成分 + 剂型清单
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB = Path("〈药品库目录〉/drugs.sqlite")
FORMS = (
    "注射液", "注射用", "葡萄糖注射液", "氯化钠注射液", "片", "胶囊", "缓释片", "缓释胶囊",
    "分散片", "肠溶片", "肠溶胶囊", "颗粒", "口服液", "口服溶液", "溶液", "混悬液", "滴剂",
    "乳膏", "软膏", "凝胶", "栓", "贴", "喷雾剂", "气雾剂", "滴眼液", "滴鼻液", "滴耳液",
    "糖浆", "散", "丸", "合剂", "酊", "膏", "洗剂", "搽剂", "吸入剂", "粉雾剂",
)
FIELD_MAP = {
    "适应症": "indication", "适应": "indication", "用于": "indication",
    "禁忌": "contraindications", "不良反应": "adverse_reactions", "副作用": "adverse_reactions",
    "相互作用": "drug_interactions", "成分": "composition", "用法": "dosage", "剂量": "dosage",
    "警告": "warnings", "储存": "storage",
}


def connect() -> sqlite3.Connection:
    if not DB.exists():
        sys.exit(f"药品库不存在：{DB}（先跑 build.py）")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def generic(name: str) -> str:
    """去掉剂型后缀，得到通用名近似（用于把 布洛芬缓释胶囊 → 布洛芬）。"""
    g = name
    for f in sorted(FORMS, key=len, reverse=True):
        if g.endswith(f) and len(g) > len(f):
            g = g[: -len(f)]
            break
    return g.strip("（）() ")


def order_sql() -> str:
    return " ORDER BY LENGTH(name) ASC"


def find(con, kw: str, limit: int = 10):
    """精确 → 前缀 → 成分包含 → 名称包含，逐级放宽。"""
    rows = con.execute(f"SELECT * FROM drugs WHERE name = ?{order_sql()}", (kw,)).fetchall()
    if not rows:
        rows = con.execute(f"SELECT * FROM drugs WHERE name LIKE ? || '%'{order_sql()} LIMIT ?", (kw, limit)).fetchall()
    if not rows:
        rows = con.execute(f"SELECT * FROM drugs WHERE composition LIKE '%' || ? || '%'{order_sql()} LIMIT ?", (kw, limit)).fetchall()
    if not rows:
        rows = con.execute(f"SELECT * FROM drugs WHERE name LIKE '%' || ? || '%'{order_sql()} LIMIT ?", (kw, limit)).fetchall()
    return rows


def trunc(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + f" …（共 {len(s)} 字，用 --full 看全文）"


def cmd_lookup(con, args) -> int:
    rows = find(con, args.kw, args.limit)
    if not rows:
        print(f"库里没有匹配「{args.kw}」的条目。按规矩：请向用户确认药名（药盒上的字最准），或换关键词。")
        return 0
    head = rows[0]
    print(f"## 命中 {len(rows)} 条（通用名近似：{generic(head['name'])}）")
    print("剂型/条目：" + "、".join(r["name"] for r in rows[:12]))
    if args.name_only:
        return 0
    fields = [("成分", "composition"), ("适应症", "indication"), ("禁忌", "contraindications"),
              ("不良反应", "adverse_reactions"), ("相互作用", "drug_interactions")]
    if args.field:
        key = FIELD_MAP.get(args.field.strip(), args.field.strip())
        fields = [(args.field, key)] if key in {f[1] for f in [("", k) for k in FIELD_MAP.values()]} else fields
    print(f"\n（以最短条目「{head['name']}」为准）")
    width = 100000 if args.full else 420
    for label, col in fields:
        val = head[col]
        if val:
            print(f"\n【{label}】{trunc(val, width)}")
    return 0


def cmd_interaction(con, args) -> int:
    a, b = args.a, args.b
    rows = con.execute(
        "SELECT * FROM interactions WHERE (a LIKE '%'||?||'%' AND b LIKE '%'||?||'%') "
        "OR (b LIKE '%'||?||'%' AND a LIKE '%'||?||'%') LIMIT ?",
        (a, b, a, b, args.limit),
    ).fetchall()
    if rows:
        print(f"## 「{a}」×「{b}」共 {len(rows)} 条")
        for r in rows:
            risk = f"[{r['risk']}] " if r["risk"] else ""
            print(f"- {risk}{r['a']} + {r['b']}：{trunc(r['detail'], 300)}")
        return 0

    print(f"## 「{a}」×「{b}」没有直接配对记录。下面分别列出两药各自的相互作用提示（供参考，非两药直配）：")
    for kw in (a, b):
        sub = con.execute(
            "SELECT * FROM interactions WHERE a LIKE '%'||?||'%' OR b LIKE '%'||?||'%' LIMIT ?",
            (kw, args.limit),
        ).fetchall()
        print(f"\n【{kw}】相关 {len(sub)} 条：")
        if not sub:
            print("- （库中无记录）")
        for r in sub:
            risk = f"[{r['risk']}] " if r["risk"] else ""
            print(f"- {risk}{r['a']} + {r['b']}：{trunc(r['detail'], 220)}")
    print("\n> 提醒：以上只是说明书原文摘录，**不做任何判断**；用药问题请咨询医生或药师。")
    return 0


def cmd_search(con, args) -> int:
    rows = con.execute(
        "SELECT d.name AS name, d.composition AS composition FROM drugs_fts f JOIN drugs d ON d.rowid = f.rowid "
        "WHERE drugs_fts MATCH ? ORDER BY rank LIMIT ?",
        (args.kw, args.limit),
    ).fetchall()
    if not rows:
        print(f"全文搜索无结果：{args.kw}")
        return 0
    print(f"## 全文搜索「{args.kw}」→ {len(rows)} 条（只回名称，需要细节再查具体药）")
    for r in rows:
        print(f"- {r['name']}" + (f"（成分：{r['composition']}）" if r["composition"] else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="本地药品库查询")
    ap.add_argument("kw", nargs="?", help="药名（任何写法）")
    ap.add_argument("--full", action="store_true", help="不截断字段")
    ap.add_argument("--field", help="只看某字段：适应症|禁忌|不良反应|相互作用|成分|用法")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("-i", "--interaction", nargs=2, metavar=("药A", "药B"))
    ap.add_argument("-s", "--search", metavar="关键词")
    ap.add_argument("-n", "--name-only", action="store_true")
    args = ap.parse_args()

    con = connect()
    if args.interaction:
        args.a, args.b = args.interaction
        return cmd_interaction(con, args)
    if args.search:
        args.kw = args.search
        return cmd_search(con, args)
    if not args.kw:
        ap.print_help()
        return 1
    return cmd_lookup(con, args)


if __name__ == "__main__":
    sys.exit(main())
