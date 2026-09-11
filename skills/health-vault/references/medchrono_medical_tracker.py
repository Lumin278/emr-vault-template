#!/usr/bin/env python3
"""
病史随访追踪系统 — 核心引擎
================================
将零散的微信截图、化验单照片、文字描述，一键转换为：
  1. Obsidian 结构化笔记（主档案 / 时间轴 / 用药 / 化验 / 问题清单）
  2. 合并完整病历 MD + 附件图片
  3. 可分享的 HTML（左侧目录导航 + 图片内嵌）
  4. 浏览器一键导出 PDF

支持增量更新：已有记录自动识别，只需放入新图片/描述，运行即追加。

用法:
  python medical_tracker.py process   --input ./病例文件夹 --output ./Obsidian输出目录
  python medical_tracker.py update    --input ./新增图片  # 增量追加
  python medical_tracker.py export    --output ./输出目录  # 仅导出HTML
"""

import os
import re
import sys
import json
import base64
import hashlib
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ============================================================
# 配置常量
# ============================================================
CACHE_FILE = ".medical_tracker_cache.json"

# Obsidian 笔记模板
NOTE_TEMPLATES = {
    "主档案": """---
name: {name}
gender: {gender}
birth_year: {birth_year}
tags:
  - 朋友看病
  - 病史随访
created: {date}
updated: {date}
diagnosis: {diagnosis}
---

# {name} — 就医档案

> [!abstract] 基本信息
> - **姓名**: {name} | **性别**: {gender} | **出生**: {birth_year}年
> - **诊断**: {diagnosis}
> {extra_info}

---

## 病史摘要

{history_summary}

---

## 关键异常指标追踪

{key_indicators}

---

## 当前用药

{current_meds}

---

## 关联笔记

- [[{name}_时间轴]] — 详细就医时间轴
- [[{name}_用药记录]] — 用药变更追踪
- [[{name}_化验指标追踪]] — 全部化验数据明细
- [[{name}_咨询问题]] — 待咨询医生的问题清单

---

> [!quote] 最后更新
> `{date}` — 由 Medical History Tracker 自动生成
""",

    "时间轴": """---
name: {name}
tags:
  - 时间轴
  - 就医记录
created: {date}
updated: {date}
---

# {name} — 就医时间轴

> 本笔记兼容 [1st-Timeline](https://github.com/lnabc03/1st-Timeline) 插件
> 每次新增就诊记录时，在对应日期下添加条目即可。

{timeline_content}

---

```mermaid
timeline
    title {name} 就医时间线
{timeline_mermaid}
```

> [!quote] 维护说明
> 新增就诊记录时，在对应月份下添加 `### YYYY年MM月DD日 — 医院科室 · 医生名` 三级标题块即可。
""",

    "用药记录": """---
name: {name}
tags:
  - 用药记录
  - 随访
created: {date}
updated: {date}
---

# {name} — 用药记录

## 用药变更时间轴

{med_timeline}

## 当前用药详情

| 药品 | 分类 | 用法用量 | 开始日期 | 状态 |
|------|------|----------|----------|:--:|
{current_meds_table}

## 用药注意事项

{med_notes}
""",

    "化验指标追踪": """---
name: {name}
tags:
  - 化验指标
  - 追踪
created: {date}
updated: {date}
---

# {name} — 化验指标追踪

> 以下按检测类别分组，每次新增报告直接追加到对应表格。

{lab_tables}

> [!tip] 使用说明
> 收到新报告后，按检测日期添加新行。
""",

    "咨询问题": """---
name: {name}
tags:
  - 咨询问题
created: {date}
updated: {date}
---

# {name} — 待咨询医生问题

> 按优先级排列，每次就诊前勾选要问的问题。

## 紧急（本次就诊必问）

{urgent_questions}

## 近期（本次或下次就诊）

{recent_questions}

## 长期管理

{longterm_questions}

## 就诊记录模板

```markdown
### YYYY年MM月DD日 — XX医院 XX科室 · XX医生
**主诉**:
**医生建议**:
**检查开具**:
**用药调整**:
**下次复诊**: YYYY-MM-DD
**备注**:
```
"""
}


# ============================================================
# OCR 引擎
# ============================================================
class OCREngine:
    """图片文字提取"""

    def __init__(self, languages: List[str] = None):
        self.languages = languages or ['ch_sim', 'en']
        self._reader = None

    @property
    def reader(self):
        if self._reader is None:
            import easyocr
            print("  加载 OCR 模型（首次较慢）...")
            self._reader = easyocr.Reader(self.languages, gpu=False)
        return self._reader

    def extract(self, image_path: Path) -> str:
        """从图片提取文字"""
        import numpy as np
        from PIL import Image

        try:
            pil_img = Image.open(str(image_path)).convert("RGB")
            img_array = np.array(pil_img)
            results = self.reader.readtext(img_array, detail=0)
            return "\n".join(results)
        except Exception as e:
            return f"[OCR 失败: {e}]"

    def batch_extract(self, image_dir: Path) -> Dict[str, str]:
        """批量提取目录下所有图片"""
        results = {}
        img_files = sorted([
            f for f in os.listdir(image_dir)
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp'))
        ])
        for i, fname in enumerate(img_files):
            print(f"  OCR [{i+1}/{len(img_files)}]: {fname}")
            results[fname] = self.extract(image_dir / fname)
        return results


# ============================================================
# 缓存管理（支持增量更新）
# ============================================================
class CacheManager:
    """JSON 缓存：记录已处理的文件和结构化数据"""

    def __init__(self, cache_path: Path):
        self.path = cache_path
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"processed_files": {}, "events": [], "patient": {}}

    def save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_processed(self, filename: str) -> bool:
        return filename in self.data["processed_files"]

    def mark_processed(self, filename: str, metadata: dict = None):
        self.data["processed_files"][filename] = metadata or {"processed_at": datetime.now().isoformat()}

    def add_event(self, event: dict):
        """添加事件（自动去重按日期）"""
        existing_dates = {e.get("date") for e in self.data["events"]}
        if event.get("date") not in existing_dates:
            self.data["events"].append(event)
        else:
            # 更新已有事件
            for i, e in enumerate(self.data["events"]):
                if e.get("date") == event.get("date"):
                    self.data["events"][i] = {**e, **event}
                    break

    def get_new_files(self, directory: Path) -> List[Path]:
        """递归获取未处理的新文件"""
        new = []
        for f in sorted(directory.rglob("*")):
            if f.is_file() and f.name not in self.data["processed_files"]:
                new.append(f)
        return new


# ============================================================
# 笔记生成器
# ============================================================
class NoteGenerator:
    """生成 Obsidian 结构化笔记"""

    def __init__(self, patient_info: dict, output_dir: Path):
        self.patient = patient_info
        self.output = output_dir
        self.output.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now().strftime("%Y-%m-%d")

    def _fill_template(self, template_name: str, **kwargs) -> str:
        template = NOTE_TEMPLATES[template_name]
        defaults = {
            "name": self.patient.get("name", "患者"),
            "gender": self.patient.get("gender", ""),
            "birth_year": self.patient.get("birth_year", ""),
            "date": self.today,
            "diagnosis": self.patient.get("diagnosis", ""),
            "extra_info": self.patient.get("extra_info", ""),
            "history_summary": self._build_history(),
            "key_indicators": self._build_indicators(),
            "current_meds": self._build_meds(),
            "timeline_content": self._build_timeline(),
            "timeline_mermaid": self._build_mermaid(),
            "med_timeline": self._build_med_timeline(),
            "current_meds_table": self._build_meds_table(),
            "med_notes": self._build_med_notes(),
            "lab_tables": self._build_lab_tables(),
            "urgent_questions": self.patient.get("urgent_questions", "- [ ] 待补充"),
            "recent_questions": self.patient.get("recent_questions", "- [ ] 待补充"),
            "longterm_questions": self.patient.get("longterm_questions", "- [ ] 待补充"),
        }
        defaults.update(kwargs)
        return template.format(**defaults)

    def generate_all(self, cache: CacheManager, overwrite: bool = False):
        """生成所有笔记"""
        name = self.patient.get("name", "患者")
        notes = {
            f"{name}_主档案.md": self._fill_template("主档案"),
            f"{name}_时间轴.md": self._fill_template("时间轴"),
            f"{name}_用药记录.md": self._fill_template("用药记录"),
            f"{name}_化验指标追踪.md": self._fill_template("化验指标追踪"),
            f"{name}_咨询问题.md": self._fill_template("咨询问题"),
        }

        for fname, content in notes.items():
            path = self.output / fname
            if path.exists() and not overwrite:
                print(f"  跳过（已存在）: {fname}")
                continue
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  生成: {fname}")

    def _build_history(self) -> str:
        events = self.patient.get("events", [])
        if not events:
            return "（待补充）"
        rows = []
        for e in sorted(events, key=lambda x: x.get("date", "")):
            rows.append(f"| {e.get('date', '')} | {e.get('summary', '')} |")
        return "\n".join(rows) if rows else "（待补充）"

    def _build_indicators(self) -> str:
        return self.patient.get("key_indicators_md", "（待补充）")

    def _build_meds(self) -> str:
        return self.patient.get("current_meds_md", "（待补充）")

    def _build_timeline(self) -> str:
        return self.patient.get("timeline_md", "（待补充）")

    def _build_mermaid(self) -> str:
        return self.patient.get("mermaid_md", "    （待补充）")

    def _build_med_timeline(self) -> str:
        return self.patient.get("med_timeline_md", "（待补充）")

    def _build_meds_table(self) -> str:
        return self.patient.get("meds_table_md", "| — | — | — | — | — |")

    def _build_med_notes(self) -> str:
        return self.patient.get("med_notes_md", "（待补充）")

    def _build_lab_tables(self) -> str:
        return self.patient.get("lab_tables_md", "（待补充）")


# ============================================================
# HTML 导出器
# ============================================================
class HTMLExporter:
    """将合并 MD 导出为带侧边栏目录的独立 HTML"""

    def __init__(self, md_path: Path, attach_dir: Path, output_path: Path):
        self.md_path = md_path
        self.attach_dir = attach_dir
        self.output_path = output_path

    def export(self) -> Path:
        print("  导出 HTML...")
        content = self._read_md()
        content = self._embed_images(content)
        content = self._process_wiki_links(content)
        content = self._process_callouts(content)
        toc_entries, content = self._process_headings(content)
        content = self._process_markdown(content)
        content = self._process_tables(content)
        content = self._wrap_paragraphs(content)
        toc_html = self._build_toc(toc_entries)
        html = self._assemble(content, toc_html, len(toc_entries))
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        size_mb = os.path.getsize(self.output_path) / (1024 * 1024)
        print(f"  HTML 导出完成: {self.output_path} ({size_mb:.1f} MB)")
        return self.output_path

    def _read_md(self) -> str:
        with open(self.md_path, 'r', encoding='utf-8') as f:
            return f.read()

    def _embed_images(self, content: str) -> str:
        def replacer(match):
            fname = match.group(1)
            path = self.attach_dir / fname
            if path.exists():
                ext = path.suffix.lower()
                mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                            '.png': 'image/png', '.gif': 'image/gif',
                            '.webp': 'image/webp', '.bmp': 'image/bmp'}
                mime = mime_map.get(ext, 'image/png')
                with open(path, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                return f'<img src="data:{mime};base64,{b64}" style="max-width:100%;border:1px solid #ddd;border-radius:4px;margin:8px 0;" alt="{fname}" loading="lazy">'
            return f'<p style="color:red;">[图片缺失: {fname}]</p>'
        return re.sub(r'!\[\[附件/([^\]]+)\]\]', replacer, content)

    def _process_wiki_links(self, content: str) -> str:
        return re.sub(r'\[\[([^\]]+)\]\]', r'<span style="color:#1976d2;">\1</span>', content)

    def _process_callouts(self, content: str) -> str:
        colors = {
            'danger': ('#d32f2f', '#ffebee'), 'warning': ('#f57c00', '#fff3e0'),
            'info': ('#1976d2', '#e3f2fd'), 'success': ('#388e3c', '#e8f5e9'),
            'tip': ('#6a1b9a', '#f3e5f5'), 'important': ('#c62828', '#fce4ec'),
            'abstract': ('#4527a0', '#ede7f6'), 'quote': ('#546e7a', '#eceff1'),
        }
        def replacer(match):
            ctype = match.group(1).lower()
            title = match.group(2).strip()
            body = match.group(3).strip()
            color, bg = colors.get(ctype, ('#546e7a', '#f5f5f5'))
            body = body.replace('\n> ', '\n').replace('\n>', '\n')
            body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', body)
            body = re.sub(r'^- ', r'• ', body, flags=re.MULTILINE)
            body = body.replace('\n', '<br>')
            return f'<div style="margin:12px 0;padding:12px 16px;border-left:4px solid {color};background:{bg};border-radius:0 8px 8px 0;"><div style="font-weight:bold;color:{color};margin-bottom:6px;">{title}</div><div style="color:#333;line-height:1.7;">{body}</div></div>'
        return re.sub(r'>\s*\[!(\w+)\]\s*(.+?)\n((?:>\s*.+?\n?)+)', replacer, content, flags=re.MULTILINE)

    def _process_headings(self, content: str) -> Tuple[List[Tuple[int, str, str]], str]:
        entries = []
        def replacer(level, prefix):
            def r(m):
                text = m.group(1).strip()
                anchor = prefix + hashlib.md5(text.encode()).hexdigest()[:8]
                entries.append((level, anchor, text))
                return f'<h{level} id="{anchor}">{text}</h{level}>'
            return r
        content = re.sub(r'^# (.+)$', replacer(1, 'ch-'), content, flags=re.MULTILINE)
        content = re.sub(r'^## (.+)$', replacer(2, 'sec-'), content, flags=re.MULTILINE)
        content = re.sub(r'^### (.+)$', replacer(3, 'sub-'), content, flags=re.MULTILINE)
        content = re.sub(r'^#### (.+)$', replacer(4, 'det-'), content, flags=re.MULTILINE)
        return entries, content

    def _process_markdown(self, content: str) -> str:
        content = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', content)
        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
        content = re.sub(r'\*(.+?)\*', r'<em>\1</em>', content)
        content = re.sub(r'`([^`]+)`', r'<code style="background:#f0f0f0;padding:2px 6px;border-radius:3px;">\1</code>', content)
        content = re.sub(r'^---$', r'<hr>', content, flags=re.MULTILINE)
        return content

    def _process_tables(self, content: str) -> str:
        lines = content.split('\n')
        out, rows = [], []
        in_table = False
        for line in lines:
            if '|' in line and line.strip().startswith('|'):
                cells = [c.strip() for c in line.split('|')[1:-1]]
                rows.append(cells)
                in_table = True
            else:
                if in_table and rows:
                    out.append(self._render_table(rows))
                    rows = []
                    in_table = False
                out.append(line)
        if rows:
            out.append(self._render_table(rows))
        return '\n'.join(out)

    def _render_table(self, rows: List[List[str]]) -> str:
        if len(rows) >= 2 and all(re.match(r'^:?-{3,}:?$', c) for c in rows[1]):
            header, data = rows[0], rows[2:]
        else:
            header, data = None, rows
        html = '<div style="overflow-x:auto;margin:12px 0;"><table style="border-collapse:collapse;min-width:100%;font-size:0.88em;">'
        if header:
            html += '<thead><tr>' + ''.join(f'<th style="border:1px solid #ddd;padding:8px 10px;background:#f5f5f5;text-align:center;white-space:nowrap;">{c}</th>' for c in header) + '</tr></thead>'
        html += '<tbody>'
        for row in data:
            html += '<tr>' + ''.join(f'<td style="border:1px solid #ddd;padding:6px 8px;text-align:center;">{c}</td>' for c in row) + '</tr>'
        html += '</tbody></table></div>'
        return html

    def _wrap_paragraphs(self, content: str) -> str:
        blocks = content.split('\n\n')
        out = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            if re.match(r'^<(\w+)', block):
                out.append(block)
            else:
                if '\n' in block:
                    block = block.replace('\n', '<br>')
                out.append(f'<p style="line-height:1.8;color:#333;">{block}</p>')
        return '\n'.join(out)

    def _build_toc(self, entries: List[Tuple[int, str, str]]) -> str:
        if not entries:
            return '<p style="color:#999;padding:12px;">（无目录）</p>'
        parts = ['<ul class="toc-list">']
        prev_level = entries[0][0]
        for i, (level, anchor, text) in enumerate(entries):
            clean = re.sub(r'<[^>]+>', '', text)
            cls = f'toc-level-{level}'
            if i == 0:
                parts.append(f'<li class="{cls}"><a href="#{anchor}" class="toc-link">{clean}</a>')
            elif level > prev_level:
                for _ in range(level - prev_level):
                    parts.append('<ul class="toc-list">')
                parts.append(f'<li class="{cls}"><a href="#{anchor}" class="toc-link">{clean}</a>')
            elif level == prev_level:
                parts.append('</li>')
                parts.append(f'<li class="{cls}"><a href="#{anchor}" class="toc-link">{clean}</a>')
            else:
                parts.append('</li>')
                for _ in range(prev_level - level):
                    parts.append('</ul></li>')
                parts.append(f'<li class="{cls}"><a href="#{anchor}" class="toc-link">{clean}</a>')
            prev_level = level
        parts.append('</li>')
        for _ in range(prev_level):
            parts.append('</ul>')
        return ''.join(parts)

    def _assemble(self, body: str, toc: str, toc_count: int) -> str:
        css, js = self._get_assets()
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>完整病历</title>
<style>{css}</style>
</head>
<body>
<button class="btn-print" onclick="window.print()" title="导出为PDF">导出PDF</button>
<button class="toc-toggle" aria-label="目录">☰</button>
<div class="sidebar-overlay"></div>
<div class="layout">
<aside class="sidebar"><div class="sidebar-header"><h2>目录</h2><div class="subtitle">共 {toc_count} 个条目</div></div><nav class="sidebar-nav">{toc}</nav></aside>
<main class="main"><div class="content">{body}</div></main>
</div>
<script>{js}</script>
</body>
</html>"""

    def _get_assets(self) -> Tuple[str, str]:
        css = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif;background:#f0f2f5;color:#333;line-height:1.8}
.layout{display:flex;min-height:100vh}
.sidebar{position:fixed;left:0;top:0;bottom:0;width:280px;background:#fff;border-right:1px solid #e0e0e0;overflow-y:auto;z-index:100;box-shadow:2px 0 12px rgba(0,0,0,0.04);transition:transform .3s}
.sidebar-header{position:sticky;top:0;background:#fff;padding:16px 20px;border-bottom:1px solid #eee;z-index:2}
.sidebar-header h2{font-size:1.1em;color:#d32f2f;margin:0;border:none;padding:0}
.sidebar-header .subtitle{font-size:.78em;color:#999;margin-top:2px}
.sidebar-nav{padding:8px 0 40px}
.toc-list{list-style:none;padding:0;margin:0}
.toc-list .toc-list{padding-left:16px}
.toc-list li{margin:0}
.toc-link{display:block;padding:5px 20px;color:#555;text-decoration:none;font-size:.88em;line-height:1.5;border-left:3px solid transparent;transition:all .15s}
.toc-link:hover{background:#f5f5f5;color:#d32f2f;border-left-color:#d32f2f}
.toc-link.active{background:#fce4ec;color:#d32f2f;border-left-color:#d32f2f;font-weight:600}
.toc-level-1>.toc-link{font-size:.95em;font-weight:600;color:#333;padding:8px 20px 8px 16px;margin-top:4px}
.toc-level-2>.toc-link{font-size:.85em}
.toc-level-3>.toc-link{font-size:.82em;color:#777}
.toc-level-4>.toc-link{font-size:.8em;color:#999}
.toc-toggle{display:none;position:fixed;top:12px;left:12px;z-index:200;width:40px;height:40px;border-radius:50%;background:#d32f2f;color:#fff;border:none;font-size:1.4em;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,0.2)}
.main{margin-left:280px;flex:1;max-width:960px;padding:30px 50px 60px}
.content{background:#fff;padding:40px 50px;border-radius:12px;box-shadow:0 2px 20px rgba(0,0,0,0.06)}
h1{font-size:2em;color:#1a1a1a;border-bottom:3px solid #d32f2f;padding-bottom:12px;margin:10px 0 20px;scroll-margin-top:24px}
h2{font-size:1.5em;color:#d32f2f;margin:36px 0 14px;padding-bottom:8px;border-bottom:1px solid #eee;scroll-margin-top:24px}
h3{font-size:1.2em;color:#333;margin:28px 0 10px;scroll-margin-top:24px}
h4{font-size:1.05em;color:#555;margin:20px 0 8px;scroll-margin-top:24px}
hr{border:none;border-top:1px solid #eee;margin:28px 0}
strong{color:#1a1a1a}
em{color:#666}
img{max-width:100%;height:auto;display:block;margin:12px auto}
th{background:#f5f5f5;font-weight:600;text-align:center;padding:10px 12px;border:1px solid #ddd;white-space:nowrap}
td{padding:8px 12px;border:1px solid #ddd;text-align:center}
tr:nth-child(even) td{background:#fafafa}
.sidebar-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:99}
.btn-print{position:fixed;top:16px;right:24px;z-index:50;padding:10px 20px;background:#d32f2f;color:#fff;border:none;border-radius:8px;font-size:.9em;cursor:pointer;box-shadow:0 2px 12px rgba(211,47,47,0.3);transition:all .2s;font-family:inherit}
.btn-print:hover{background:#b71c1c;box-shadow:0 4px 16px rgba(211,47,47,0.4);transform:translateY(-1px)}
@media(max-width:900px){.sidebar{transform:translateX(-100%)}.sidebar.open{transform:translateX(0)}.sidebar-overlay.show{display:block}.toc-toggle{display:flex}.main{margin-left:0;padding:20px 16px 40px}.content{padding:24px 18px;border-radius:8px}h1{font-size:1.5em}h2{font-size:1.2em}table{font-size:.72em}.btn-print{top:12px;right:12px;padding:8px 14px;font-size:.82em}}
@media print{.sidebar,.toc-toggle,.sidebar-overlay,.btn-print{display:none!important}.main{margin-left:0;padding:0}.content{box-shadow:none;border-radius:0;padding:10px;max-width:100%}body{background:#fff}img{max-width:100%;page-break-inside:avoid}}
"""
        js = """
(function(){
var s=document.querySelector('.sidebar'),o=document.querySelector('.sidebar-overlay'),t=document.querySelector('.toc-toggle'),l=document.querySelectorAll('.toc-link');
function open(){s.classList.add('open');o.classList.add('show')}
function close(){s.classList.remove('open');o.classList.remove('show')}
t.addEventListener('click',function(){s.classList.contains('open')?close():open()});
o.addEventListener('click',close);
l.forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();var t=document.getElementById(this.getAttribute('href').substring(1));if(t){t.scrollIntoView({behavior:'smooth',block:'start'});if(window.innerWidth<=900)close()}})});
var h=Array.from(document.querySelectorAll('h1[id],h2[id],h3[id],h4[id]'));
function hs(){var c='',sy=window.scrollY+80;
for(var i=h.length-1;i>=0;i--){if(h[i].offsetTop<=sy){c=h[i].id;break}}
l.forEach(function(a){a.classList.remove('active');if(a.getAttribute('href')==='#'+c){a.classList.add('active');if(window.innerWidth>900)a.scrollIntoView({block:'nearest',behavior:'smooth'})}})}
var st;window.addEventListener('scroll',function(){clearTimeout(st);st=setTimeout(hs,50)});hs();
})();
"""
        return css, js


# ============================================================
# 主工作流
# ============================================================
class MedicalTracker:
    """病史随访追踪系统主控"""

    def __init__(self, input_dir: str, output_dir: str, patient_name: str,
                 patient_info: dict = None):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.patient_name = patient_name
        self.patient_info = patient_info or {"name": patient_name}
        self.attach_dir = self.output_dir / "附件"
        self.cache_path = self.output_dir / CACHE_FILE
        self.cache = CacheManager(self.cache_path)
        self.ocr = OCREngine()

    def process(self, mode: str = "full"):
        """执行完整或增量处理流程"""
        print(f"\n{'='*60}")
        print(f"  病史随访追踪系统")
        print(f"  患者: {self.patient_name}")
        print(f"  输入: {self.input_dir}")
        print(f"  输出: {self.output_dir}")
        print(f"  模式: {mode}")
        print(f"{'='*60}\n")

        # Step 1: 递归扫描输入目录（仅处理原始文件，排除已生成的 md）
        print("[1/5] 扫描输入文件...")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        VALID_EXT = {'.txt', '.png', '.jpg', '.jpeg', '.bmp', '.webp'}
        if mode == "update":
            all_files = [f for f in self.cache.get_new_files(self.input_dir)
                         if f.suffix.lower() in VALID_EXT]
        else:
            all_files = sorted([f for f in self.input_dir.rglob("*")
                               if f.is_file() and f.suffix.lower() in VALID_EXT])

        if not all_files:
            print("  没有文件，无需处理。")
            return
        print(f"  共 {len(all_files)} 个文件")

        # Step 2: OCR 图片 + 读取文本
        print("\n[2/5] OCR 文字提取...")
        txt_files = [f for f in all_files if f.suffix.lower() == '.txt']
        img_files = [f for f in all_files if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.webp')]

        # 按子目录分组处理
        subdirs = {}
        for f in img_files + txt_files:
            # 相对路径的子目录名作为分组键
            rel = f.relative_to(self.input_dir)
            subdir_key = str(rel.parent) if str(rel.parent) != '.' else 'root'
            if subdir_key not in subdirs:
                subdirs[subdir_key] = {'txt': [], 'img': []}
            if f.suffix.lower() == '.txt':
                subdirs[subdir_key]['txt'].append(f)
            else:
                subdirs[subdir_key]['img'].append(f)

        for subdir_key, group in subdirs.items():
            print(f"\n  [{subdir_key}]/")

            # 读取 txt
            for tf in group['txt']:
                if not self.cache.is_processed(tf.name):
                    with open(tf, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                    print(f"    读取: {tf.name} ({len(content)} 字)")
                    # 保存为 MD
                    md_path = self.output_dir / (tf.stem + ".md")
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(f"# {tf.stem}\n\n{content}\n")
                    self.cache.mark_processed(tf.name, {"text": content})

            # OCR 图片
            for img_file in group['img']:
                if not self.cache.is_processed(img_file.name):
                    text = self.ocr.extract(img_file)
                    md_name = img_file.stem + ".md"
                    md_path = self.output_dir / md_name
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(f"# {img_file.name}\n\n{text}\n")
                    self.cache.mark_processed(img_file.name, {"ocr_text": text})
                    print(f"    OCR: {img_file.name} -> {md_name}")

            # 生成该子目录的汇总
            if group['img']:
                summary_path = self.output_dir / f"{subdir_key.replace('/', '_')}_图片汇总.md"
                with open(summary_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {subdir_key} — 图片文字汇总\n\n")
                    for img_file in group['img']:
                        meta = self.cache.data["processed_files"].get(img_file.name, {})
                        text = meta.get("ocr_text", "（未识别）")
                        f.write(f"## {img_file.name}\n\n{text}\n\n---\n\n")
                print(f"    汇总: {summary_path.name}")

        # Step 3: 复制附件
        print("\n[3/5] 复制图片附件...")
        self.attach_dir.mkdir(parents=True, exist_ok=True)
        for img_file in img_files:
            dest = self.attach_dir / img_file.name
            if not dest.exists():
                shutil.copy2(img_file, dest)

        # Step 4: 生成笔记（此处输出提示，实际内容由 Claude 生成）
        print("\n[4/5] 生成 Obsidian 笔记...")
        print("  OCR 数据已准备完成。请使用 Claude Code 根据 OCR 结果填充笔记模板。")
        print(f"  输出目录: {self.output_dir}")
        print(f"  OCR 文件: {self.output_dir}/*.md")
        print(f"  附件目录: {self.attach_dir}")

        # Step 5: 保存缓存
        self.cache.save()
        print(f"\n[5/5] 缓存已保存: {self.cache_path}")
        print(f"\n{'='*60}")
        print(f"  处理完成！")
        print(f"{'='*60}\n")

    def export_html(self, consolidated_md: str, output_html: str = None):
        """导出 HTML"""
        md_path = Path(consolidated_md)
        if not md_path.exists():
            print(f"错误: 找不到 {consolidated_md}")
            return

        if output_html is None:
            output_html = str(md_path.with_suffix('.html'))

        exporter = HTMLExporter(md_path, self.attach_dir, Path(output_html))
        exporter.export()


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="病史随访追踪系统 — 将零散病历图片/文字转换为结构化 Obsidian 笔记和可分享 HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 首次处理
  python medical_tracker.py process -i ./病例图片 -o ./Obsidian输出 -n 张三

  # 增量更新（追加新图片）
  python medical_tracker.py update -i ./新图片 -o ./Obsidian输出 -n 张三

  # 仅导出 HTML
  python medical_tracker.py export --md 完整病历.md -o 完整病历.html

工作流:
  输入文件(.txt/.jpg/.png) → OCR → 结构化笔记 → 合并MD → HTML/PDF
        """
    )

    sub = parser.add_subparsers(dest="command", help="命令")

    # process
    p_process = sub.add_parser("process", help="完整处理：OCR → 笔记 → 合并 → 导出")
    p_process.add_argument("-i", "--input", required=True, help="输入目录（含 txt 和图片）")
    p_process.add_argument("-o", "--output", required=True, help="输出目录（Obsidian vault 路径）")
    p_process.add_argument("-n", "--name", required=True, help="患者姓名")

    # update
    p_update = sub.add_parser("update", help="增量更新：只处理新文件")
    p_update.add_argument("-i", "--input", required=True, help="输入目录")
    p_update.add_argument("-o", "--output", required=True, help="输出目录")
    p_update.add_argument("-n", "--name", required=True, help="患者姓名")

    # export
    p_export = sub.add_parser("export", help="导出 HTML")
    p_export.add_argument("--md", required=True, help="合并的完整病历 MD 路径")
    p_export.add_argument("-o", "--output", help="输出 HTML 路径（默认同目录同名）")
    p_export.add_argument("--attach", help="附件目录（默认 MD 同目录下的 附件/）")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    tracker = MedicalTracker(
        input_dir=args.input,
        output_dir=args.output,
        patient_name=getattr(args, 'name', '患者'),
    )

    if args.command in ("process", "update"):
        tracker.process(mode=args.command)
    elif args.command == "export":
        attach = args.attach or str(Path(args.md).parent / "附件")
        tracker.export_html(args.md, args.output)


if __name__ == "__main__":
    main()
