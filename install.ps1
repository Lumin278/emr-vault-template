# ─────────────────────────────────────────────────────────────
# 电子病历库 · 一键安装（Windows PowerShell）
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   ... -WithVault        顺便把空库骨架复制到 ~\电子病历库
#   ... -Claude           顺便装给 Claude Code（~\.claude\skills）
#   ... -Force            目标已存在时覆盖
# ─────────────────────────────────────────────────────────────
param(
  [switch]$WithVault,
  [switch]$Claude,
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$HermesSkills = if ($env:HERMES_HOME) { Join-Path $env:HERMES_HOME "skills" } else { Join-Path $HOME ".hermes\skills" }
$ClaudeSkills = Join-Path $HOME ".claude\skills"
$VaultDest    = Join-Path $HOME "电子病历库"

function Install-Skill($src, $parent) {
  $name = Split-Path -Leaf $src
  $dest = Join-Path $parent $name
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  if ((Test-Path $dest) -and (-not $Force)) {
    Write-Host "  SKIP  $name 已存在（要覆盖加 -Force）"
    return
  }
  if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
  Copy-Item -Recurse -Force $src $dest
  Write-Host "  OK    $name -> $dest"
}

Write-Host "==> 安装 Hermes 技能到 $HermesSkills"
Install-Skill (Join-Path $Here "skills\health-vault") $HermesSkills
Install-Skill (Join-Path $Here "skills\drug-lookup")  $HermesSkills

if ($Claude) {
  Write-Host "==> 安装 Claude Code 技能到 $ClaudeSkills"
  Install-Skill (Join-Path $Here "skills\health-vault") $ClaudeSkills
  Install-Skill (Join-Path $Here "skills\drug-lookup")  $ClaudeSkills
}

if ($WithVault) {
  if (Test-Path $VaultDest) {
    Write-Host "==> 库目录已存在，跳过：$VaultDest"
  } else {
    Copy-Item -Recurse (Join-Path $Here "vault-template") $VaultDest
    Write-Host "==> 空库已建立：$VaultDest"
  }
}

Write-Host @"

────────────────────────────────────────────────────────────
装好了。接下来：

1) 建库（还没建的话）
     Copy-Item -Recurse vault-template ~\电子病历库     # 或用 install.ps1 -WithVault
   用 Obsidian 打开这个文件夹，把 "张三" 改成你的名字，
   并改一下库根 AGENTS.md 里的「库根路径」和「当前成员」。

2) 让 Agent 干活
   · Hermes：直接说「按 AGENTS.md 归档这份体检报告」，把单据照片发进去
   · 其他 Agent：把库目录打开，说「按 AGENTS.md 干活」（入口文件已备好）

3) 药品数据（可选）：见仓库 README 的「关于药品数据」一节。

⚠️ 不诊断、不给用药建议、不判断紧急度。所有输出请与医生确认。
────────────────────────────────────────────────────────────
"@
