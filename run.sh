#!/bin/bash
# AI 뉴스 다이제스트 자동 실행 스크립트
# crontab에 추가: 0 9 * * * /bin/bash /Users/hana/ai-news-dashboard/run.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 환경 변수 로드
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "[$(date '+%Y-%m-%d %H:%M')] 시작"

# Python 실행
python3 main.py

# Git push (GitHub Pages 배포)
git add index.html
git commit -m "daily digest $(date '+%Y-%m-%d')"
git push origin main

echo "[$(date '+%Y-%m-%d %H:%M')] 완료"
