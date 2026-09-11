#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# 电子病历库 · 一键安装
#
#   bash install.sh                 只装技能（health-vault + drug-lookup）
#   bash install.sh --with-vault    顺便把空库骨架复制到 ~/电子病历库
#   bash install.sh --claude        顺便装给 Claude Code（~/.claude/skills）
#   bash install.sh --force         目标已存在时覆盖
# ─────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_SKILLS="${HERMES_HOME:-$HOME/.hermes}/skills"
CLAUDE_SKILLS="$HOME/.claude/skills"
VAULT_DEST="${VAULT_DEST:-$HOME/电子病历库}"

WITH_VAULT=0; WITH_CLAUDE=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --with-vault) WITH_VAULT=1 ;;
    --claude)     WITH_CLAUDE=1 ;;
    --force)      FORCE=1 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

install_skill() {  # $1=技能目录  $2=目标父目录
  local name dest
  name="$(basename "$1")"
  dest="$2/$name"
  mkdir -p "$2"
  if [ -e "$dest" ] && [ "$FORCE" -ne 1 ]; then
    echo "  ⏭  $name 已存在，跳过（要覆盖加 --force）"
    return
  fi
  rm -rf "$dest"
  cp -R "$1" "$dest"
  echo "  ✅ $name → $dest"
}

echo "==> 安装 Hermes 技能到 $HERMES_SKILLS"
install_skill "$HERE/skills/health-vault" "$HERMES_SKILLS"
install_skill "$HERE/skills/drug-lookup"  "$HERMES_SKILLS"

if [ "$WITH_CLAUDE" -eq 1 ]; then
  echo "==> 安装 Claude Code 技能到 $CLAUDE_SKILLS"
  install_skill "$HERE/skills/health-vault" "$CLAUDE_SKILLS"
  install_skill "$HERE/skills/drug-lookup"  "$CLAUDE_SKILLS"
fi

if [ "$WITH_VAULT" -eq 1 ]; then
  if [ -e "$VAULT_DEST" ]; then
    echo "==> 库目录已存在，跳过：$VAULT_DEST"
  else
    cp -R "$HERE/vault-template" "$VAULT_DEST"
    echo "==> 空库已建立：$VAULT_DEST"
  fi
fi

cat <<'TIP'

────────────────────────────────────────────────────────────
装好了。接下来：

1) 建库（还没建的话）
     cp -r vault-template ~/电子病历库       # 或用 bash install.sh --with-vault
   用 Obsidian 打开这个文件夹，把 "张三/" 改成你的名字，
   并改一下库根 AGENTS.md 里的「库根路径」和「当前成员」。

2) 让 Agent 干活
   · Hermes：直接说「按 AGENTS.md 归档这份体检报告」，把单据照片发进去
   · 其他 Agent（Codex / Cursor / Claude Code / Copilot…）：
     把库目录打开，说「按 AGENTS.md 干活」即可（入口文件已备好）

3) 药品数据（可选）
   skills/drug-lookup 只是个查询工具，数据要自备 —— 见仓库 README 的
   「关于药品数据」一节。

⚠️ 不诊断、不给用药建议、不判断紧急度。所有输出请与医生确认。
────────────────────────────────────────────────────────────
TIP
