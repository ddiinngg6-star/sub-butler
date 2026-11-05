#!/usr/bin/env bash
# 一键安装到 VPS：创建虚拟环境、安装、写入每日定时任务。
# 用法：在项目目录里执行  bash deploy/install.sh  [每天运行的时间，默认 09:00]
set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
RUN_AT="${1:-09:00}"
HOUR="${RUN_AT%%:*}"
MINUTE="${RUN_AT##*:}"

command -v python3 >/dev/null || { echo "需要 python3（3.10+）"; exit 1; }

echo "→ 创建虚拟环境并安装"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q .

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "→ 已生成 config.yaml，请先编辑它"
fi
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "→ 已生成 .env，请填入密钥"
fi

CRON_LINE="$MINUTE $HOUR * * * cd $APP_DIR && .venv/bin/sub-butler run >> data/cron.log 2>&1"
mkdir -p data
( crontab -l 2>/dev/null | grep -v "sub-butler run" ; echo "$CRON_LINE" ) | crontab -
echo "→ 已添加定时任务：每天 $RUN_AT 运行（服务器时区）"
echo
echo "下一步："
echo "  1. 编辑 config.yaml 和 .env"
echo "  2. .venv/bin/sub-butler check            # 检查 Notion / 邮箱 / 大模型是否连得上"
echo "  3. .venv/bin/sub-butler sync --dry-run   # 先试运行看看效果"
