#!/usr/bin/env python3
"""构建本地药品库（SQLite + FTS5），数据来自 MedGraphRAG-Drug-DB（中文说明书，药典网来源）。

用法：python3 build.py            # 从 research/drugdata/drug_db_v2.json 建库
产出：drugs.sqlite（drugs 表 + interactions 表 + drugs_fts 全文索引）
注意：数据许可不明（爬取自药典网），**仅供本机个人查询使用，不得再分发**。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

SRC = Path("〈药品原始数据目录〉/drug_db_v2.json")
OUT = Path("〈药品库目录〉/drugs.sqlite")


def norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "；".join(str(x) for x in v if x)
    return re.sub(r"\s+", " ", str(v)).strip()


INTER_KEYS = (("drug1", "drug2", "interaction"), ("a", "b", "detail"), ("name1", "name2", "description"))


def pick_interaction(it: dict) -> tuple[str, str, str, str]:
    """真实结构：{"pair": [药A, 药B], "risk": "high|none|...", "note": "..."}"""
    pair = it.get("pair")
    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
        return norm(pair[0]), norm(pair[1]), norm(it.get("risk")), norm(it.get("note"))
    for k1, k2, k3 in INTER_KEYS:
        if it.get(k1) and it.get(k2):
            return norm(it.get(k1)), norm(it.get(k2)), "", norm(it.get(k3)) or json.dumps(it, ensure_ascii=False)
    vals = [norm(v) for v in it.values() if isinstance(v, (str, list))]
    while len(vals) < 3:
        vals.append("")
    return vals[0], vals[1], "", vals[2]


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    drugs = data.get("drugs") or {}
    interactions = data.get("interactions") or []

    OUT.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(OUT)
    con.executescript(
        """
        DROP TABLE IF EXISTS drugs;
        DROP TABLE IF EXISTS interactions;
        DROP TABLE IF EXISTS drugs_fts;
        CREATE TABLE drugs(
            name TEXT PRIMARY KEY, composition TEXT, indication TEXT, contraindications TEXT,
            warnings TEXT, dosage TEXT, adverse_reactions TEXT, drug_interactions TEXT,
            storage TEXT, source TEXT);
        CREATE TABLE interactions(a TEXT, b TEXT, risk TEXT, detail TEXT);
        """
    )

    rows = []
    for name, v in drugs.items():
        if not isinstance(v, dict):
            continue
        rows.append((
            norm(name), norm(v.get("composition")), norm(v.get("indication")),
            norm(v.get("contraindications")), norm(v.get("warnings")), norm(v.get("dosage")),
            norm(v.get("adverse_reactions")), norm(v.get("drug_interactions")),
            norm(v.get("storage")), norm(v.get("source")),
        ))
    con.executemany("INSERT OR REPLACE INTO drugs VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    irows = [pick_interaction(it) for it in interactions if isinstance(it, dict)]
    con.executemany("INSERT INTO interactions VALUES (?,?,?,?)", irows)

    con.executescript(
        """
        CREATE VIRTUAL TABLE drugs_fts USING fts5(name, composition, indication, adverse_reactions,
                                                  content='drugs', content_rowid='rowid');
        INSERT INTO drugs_fts(drugs_fts) VALUES('rebuild');
        CREATE INDEX idx_inter_a ON interactions(a);
        CREATE INDEX idx_inter_b ON interactions(b);
        """
    )
    con.commit()

    n_drugs = con.execute("SELECT COUNT(*) FROM drugs").fetchone()[0]
    n_inter = con.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"建成: {OUT}  ({size_mb:.1f} MB)")
    print(f"药品: {n_drugs} 条 | 相互作用: {n_inter} 条")


if __name__ == "__main__":
    main()
