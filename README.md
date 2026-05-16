# AI 뉴스 다이제스트

Mac Mini cron → GitHub Pages 배포 → 텔레그램 알림

## 구조
Mac Mini 9:00 → RSS 수집 + claude CLI 분석 + HTML 생성 → git push → GitHub Pages → 텔레그램 URL 전송

## 설치

### 1. GitHub 레포 생성 (Public)
```bash
git clone https://github.com/USERNAME/ai-news-dashboard.git
cd ai-news-dashboard
git add . && git commit -m "init" && git push origin main
```

### 2. GitHub Pages 활성화
Settings → Pages → Source: main 브랜치 / (root)

### 3. 환경 변수
```bash
cp .env.example .env
# .env 파일 열어서 값 입력
```

### 4. 패키지 설치
```bash
pip3 install -r requirements.txt
```

### 5. 테스트
```bash
bash run.sh
```

### 6. cron 등록
```bash
crontab -e
# 아래 추가 (매일 오전 9시):
0 9 * * * /bin/bash /Users/hana/ai-news-dashboard/run.sh >> /tmp/ai-news.log 2>&1
```

## 비용
- claude CLI: 기존 구독 그대로
- GitHub Pages: 무료
- 텔레그램: 무료
