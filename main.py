import feedparser, requests, json, os, sys, subprocess, ssl, certifi
import trafilatura
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from supabase import create_client

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

_BROWSER_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
_GOOGLEBOT_UA = 'Googlebot/2.1 (+http://www.google.com/bot.html)'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID')
GITHUB_PAGES_URL   = os.getenv('GITHUB_PAGES_URL', '')
STREAMLIT_APP_URL  = os.getenv('STREAMLIT_APP_URL', '')
SUPABASE_URL       = os.getenv('SUPABASE_URL', '')
SUPABASE_KEY       = os.getenv('SUPABASE_KEY', '')

RSS_FEEDS = [
    {"name": "Hugging Face Papers", "url": "https://huggingface.co/papers/rss.xml",    "cat": "논문"},
    {"name": "Papers with Code",    "url": "https://paperswithcode.com/rss.xml",        "cat": "논문"},
    {"name": "arXiv AI",            "url": "https://arxiv.org/rss/cs.AI",               "cat": "논문"},
    {"name": "arXiv HCI",           "url": "https://arxiv.org/rss/cs.HC",               "cat": "논문"},
    {"name": "arXiv ML",            "url": "https://arxiv.org/rss/cs.LG",               "cat": "논문"},
    {"name": "MIT Tech Review",     "url": "https://www.technologyreview.com/feed/",    "cat": "뉴스"},
    {"name": "TechCrunch AI",       "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "cat": "뉴스"},
    {"name": "Import AI",           "url": "https://importai.substack.com/feed",        "cat": "뉴스레터"},
    {"name": "Latent Space",        "url": "https://www.latent.space/feed",             "cat": "뉴스레터"},
    {"name": "TLDR AI",             "url": "https://tldr.tech/ai/rss",                  "cat": "뉴스레터"},
    {"name": "r/MachineLearning",   "url": "https://www.reddit.com/r/MachineLearning/.rss", "cat": "커뮤니티"},
    {"name": "r/LocalLLaMA",        "url": "https://www.reddit.com/r/LocalLLaMA/.rss",     "cat": "커뮤니티"},
    {"name": "ACM TOCHI",           "url": "https://dl.acm.org/action/showFeed?type=etoc&feed=rss&jc=tochi", "cat": "논문"},
]

# ── Content fetching ──────────────────────────────────────────────────────────

def _extract(html):
    return trafilatura.extract(
        html, include_comments=False, include_tables=False,
        favor_recall=True, no_fallback=False,
    )

def fetch_reddit_content(url):
    """Reddit JSON API로 게시글 + 상위 댓글 수집"""
    try:
        clean = url.split('?')[0].rstrip('/')
        resp = requests.get(
            clean + '.json',
            headers={'User-Agent': 'Mozilla/5.0 AI-NewsReader/1.0', 'Accept': 'application/json'},
            timeout=15,
        )
        if not resp.ok:
            return None
        data = resp.json()
        if len(data) < 2:
            return None

        post = data[0]['data']['children'][0]['data']
        lines = [f"제목: {post.get('title', '')}"]

        selftext = (post.get('selftext') or '').strip()
        if selftext and selftext != '[removed]':
            lines.append(f"본문: {selftext[:500]}")

        # 점수 상위 댓글
        top = sorted(
            [c['data'] for c in data[1]['data']['children'] if c.get('kind') == 't1'],
            key=lambda c: c.get('score', 0), reverse=True
        )
        good = [(c['score'], c['body'][:300]) for c in top[:8]
                if c.get('body') and c['body'] != '[removed]' and c.get('score', 0) > 1]
        if good:
            lines.append("주요 댓글:")
            for score, body in good[:5]:
                lines.append(f"  [{score}점] {body}")

        return '\n'.join(lines)
    except Exception:
        return None

def fetch_content(url):
    if 'reddit.com' in url:
        return fetch_reddit_content(url)

    # Soft paywall 우회: Google 리퍼러 → Googlebot → trafilatura 기본
    for ua, ref in [(_BROWSER_UA, 'https://www.google.com/'), (_GOOGLEBOT_UA, '')]:
        try:
            hdrs = {'User-Agent': ua, 'Accept-Language': 'en-US,en;q=0.9'}
            if ref:
                hdrs['Referer'] = ref
            resp = requests.get(url, headers=hdrs, timeout=15, allow_redirects=True)
            if resp.ok and len(resp.text) > 500:
                text = _extract(resp.text)
                if text and len(text) > 200:
                    return text[:5000]
        except Exception:
            pass

    try:
        html = trafilatura.fetch_url(url)
        if html:
            text = _extract(html)
            if text:
                return text[:5000]
    except Exception:
        pass

    return None

# ── Korean summary generation ─────────────────────────────────────────────────

def ask_claude(prompt, timeout=180):
    result = subprocess.run(['claude', '-p', prompt], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"claude 오류: {result.stderr}")
    return result.stdout.strip()

def _parse_json(raw):
    raw = raw.strip()
    if raw.startswith('```'):
        parts = raw.split('```')
        raw = parts[1]
        if raw.startswith('json'):
            raw = raw[4:]
    return json.loads(raw.strip())

def generate_ko_summaries(articles):
    """Claude로 전체 아티클 한국어 요약 (배치 15개씩)"""
    BATCH = 15
    summaries = {}

    type_hint = {
        '논문':    '논문 — 기존 연구와의 핵심 차별점과 기여를 이해하기 쉽게',
        '뉴스':    '뉴스 — 무슨 일이 있었는지와 왜 지금 중요한지',
        '뉴스레터': '뉴스레터 — 핵심 인사이트',
        '커뮤니티': '커뮤니티 — 어떤 논의가 달아올랐고 커뮤니티 반응은',
    }

    for start in range(0, len(articles), BATCH):
        batch = list(enumerate(articles))[start:start + BATCH]
        lines = []
        for i, a in batch:
            hint = type_hint.get(a.get('cat', ''), '핵심 내용 요약')
            # 본문 → RSS 요약 순으로 사용
            text = (a.get('content') or a.get('summary') or '')[:800]
            lines.append(f"[{i}] {hint}\n제목: {a['title']}\n내용: {text}")

        prompt = (
            "아래 AI 관련 아티클들을 각각 한국어로 2~3문장 요약해줘. JSON만 반환해.\n\n"
            "타입별 가이드:\n"
            "- 논문: '기존엔 X였는데, 이 연구는 Y를 해서 Z를 가능하게 했어' 형태로. 전문 용어는 쉽게 풀어줘.\n"
            "- 뉴스: 무슨 일 + 왜 중요한지\n"
            "- 커뮤니티: 어떤 주제로 토론이 뜨거웠는지 + 주요 의견\n\n"
            + '\n\n---\n\n'.join(lines)
            + f'\n\n형식: {{"{start}": "요약...", "{start+1}": "요약...", ...}}\n'
            '숫자는 위 [숫자] 그대로. JSON만.'
        )

        try:
            summaries.update(_parse_json(ask_claude(prompt)))
            print(f"   요약 완료 {min(start + BATCH, len(articles))}/{len(articles)}")
        except Exception as e:
            print(f"  [요약 배치 {start} 오류] {e}")

    return summaries

# ── Story ─────────────────────────────────────────────────────────────────────

def get_story(articles, digest):
    clusters = digest.get('clusters', [])

    cluster_sections = []
    for c in clusters:
        arts = [f"  - [{articles[i]['source']}] {articles[i]['title']}"
                for i in c['indices'] if i < len(articles)]
        cluster_sections.append(
            f"묶음: {c['label']} (큰 테마: {c['theme']})\n" + '\n'.join(arts[:6])
        )

    all_news = '\n'.join(
        f"- [{a['cat']}] {a['source']}: {a['title']}. {a.get('summary', '')[:80]}"
        for a in articles[:30]
    )

    prompt = f"""오늘 AI 세계에서 일어난 일들을 아래 뉴스 전부를 엮어서 친구에게 이야기하듯 한국어로 써줘.

규칙:
1. 전체 뉴스를 하나의 흐름으로 엮기 — 빠뜨리지 말고 다 언급
2. 각 주제 묶음 이야기가 끝날 때마다, 다음으로 넘어가기 전에 반드시:
   "결국 이 얘기들을 큰 그림으로 보면 [전체 스코프 한 문장]이야. 그리고 이제..."
   같은 형태로 전체 맥락을 짚어준 뒤 다음 주제로 자연스럽게 연결
3. 딱딱한 보고서 X — 지식 많은 친구가 신나서 설명하는 느낌
4. 이모지 자연스럽게 (남발 금지)
5. 길이 제한 없음 — 풍성하게
6. 마지막: 💡 오늘 전체를 관통하는 핵심 한 문장
7. 스토리 텍스트만 반환

오늘의 뉴스 묶음:
{chr(10).join(cluster_sections)}

전체 뉴스 목록:
{all_news}"""

    return ask_claude(prompt, timeout=240)

# ── Feeds & articles ──────────────────────────────────────────────────────────

def load_feeds_from_db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        res = sb.table('feeds').select('id,url,name,tag').eq('active', True).execute()
        return [{'id': f['id'], 'url': f['url'], 'name': f['name'], 'cat': f['tag']}
                for f in (res.data or [])]
    except Exception:
        return None

def fetch_articles(max_per_feed=5):
    feeds = load_feeds_from_db() or [
        {'id': None, 'url': f['url'], 'name': f['name'], 'cat': f['cat']}
        for f in RSS_FEEDS
    ]
    articles = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed['url'])
            for entry in parsed.entries[:max_per_feed]:
                summary = (entry.get('summary') or entry.get('description') or '')[:600]
                pub = ''
                if entry.get('published_parsed'):
                    import time as _t
                    pub = datetime.fromtimestamp(_t.mktime(entry.published_parsed)).isoformat()
                articles.append({
                    'feed_id': feed.get('id'), 'source': feed['name'], 'cat': feed['cat'],
                    'title': entry.get('title', '')[:120], 'summary': summary,
                    'url': entry.get('link', ''), 'date': entry.get('published', '')[:16],
                    'published_at': pub,
                })
        except Exception as e:
            print(f"  [skip] {feed['name']}: {e}")
    return articles

def save_articles_to_db(articles, today):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    valid = [a for a in articles if a.get('url')]

    # 1. 본문 병렬 수집
    print(f"   본문 수집 중 ({len(valid)}개)…")
    contents = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_content, a['url']): a['url'] for a in valid}
        done = 0
        for fut in as_completed(futs):
            contents[futs[fut]] = fut.result()
            done += 1
            if done % 10 == 0:
                print(f"   본문 {done}/{len(valid)}")

    # content를 article에 붙여서 요약 생성에 활용
    for a in valid:
        a['content'] = contents.get(a['url'])

    # 2. 한국어 요약 생성
    print(f"   한국어 요약 생성 중…")
    ko = generate_ko_summaries(valid)

    # 3. Supabase upsert
    rows = [{
        'feed_id':    a.get('feed_id'),
        'title':      a['title'],
        'url':        a['url'],
        'summary':    a.get('summary', ''),
        'summary_ko': ko.get(str(i)),
        'content':    a.get('content'),
        'published_at': a.get('published_at') or None,
        'date':       today,
    } for i, a in enumerate(valid)]

    for i in range(0, len(rows), 50):
        try:
            sb.table('articles').upsert(rows[i:i+50], on_conflict='url').execute()
        except Exception as e:
            print(f"  [articles upsert warn] {e}")

# ── Digest & HTML ─────────────────────────────────────────────────────────────

def get_digest(articles):
    listing = '\n'.join(
        f"[{i}] ({a['cat']}) {a['source']} | {a['title']} | {a['summary'][:120]}"
        for i, a in enumerate(articles[:45])
    )
    prompt = (
        "다음 AI 뉴스 목록을 분석해서 JSON만 반환해줘 (코드블록, 설명 없이).\n\n"
        + listing
        + '\n\n형식:\n'
        '{"trends":["트렌드1","트렌드2","트렌드3"],'
        '"clusters":[{"label":"이름","desc":"한줄설명","theme":"큰개념","indices":[0,1,2],'
        '"color":"#185FA5","bg":"#E6F1FB","textColor":"#0C447C","themeColor":"#0F6E56","themeBg":"#E1F5EE"}],'
        '"must_reads":[{"index":0,"why":"이유","hook":"연구 연관성"}],'
        '"explanations":{"0":"3문장 설명"}}\n\n'
        'clusters 3개, must_reads 2개, 색상 파랑/보라/초록 계열로 다르게. JSON만.'
    )
    return _parse_json(ask_claude(prompt))

def build_html(articles, digest, generated_at):
    data_js = json.dumps({
        'articles': articles, 'digest': digest, 'generated_at': generated_at,
        'total': len(articles),
        'counts': {cat: sum(1 for a in articles if a['cat'] == cat)
                   for cat in ['논문', '뉴스', '뉴스레터', '커뮤니티']},
    }, ensure_ascii=False)
    return open('template.html').read().replace('__DATA__', data_js).replace('__GENTIME__', generated_at)

# ── Supabase ──────────────────────────────────────────────────────────────────

def save_to_supabase(articles, digest, story, today, generated_at):
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("   [skip] SUPABASE_URL/KEY 없음")
        return
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    counts = {cat: sum(1 for a in articles if a['cat'] == cat)
              for cat in ['논문', '뉴스', '뉴스레터', '커뮤니티']}
    sb.table('reports').upsert({
        'date': today, 'articles': articles, 'digest': digest,
        'story': story, 'total': len(articles),
        'counts': counts, 'generated_at': generated_at,
    }, on_conflict='date').execute()

# ── Telegram ──────────────────────────────────────────────────────────────────

def _tg(payload):
    requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                  json=payload, timeout=10)

def send_telegram(total, story):
    today = datetime.now().strftime('%m/%d')
    _tg({
        'chat_id': TELEGRAM_CHAT_ID,
        'text': f"<b>📰 오늘의 AI 뉴스 — {today}</b>  |  총 <b>{total}개</b> 아티클",
        'parse_mode': 'HTML',
        'reply_markup': {'inline_keyboard': [[
            {'text': '📱 AI 뉴스 리더 열기',
             'url': 'https://hanaisreal.github.io/ai-news-dashboard/reader.html'}
        ]]},
    })
    if story:
        _tg({'chat_id': TELEGRAM_CHAT_ID, 'text': story[:4000]})

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("1. RSS 수집 중...")
    articles = fetch_articles()
    print(f"   {len(articles)}개 수집")
    if not articles:
        sys.exit(1)

    print("2. Claude 분석 (digest)...")
    digest = get_digest(articles)
    print(f"   클러스터 {len(digest['clusters'])}개")

    today        = datetime.now().strftime('%Y-%m-%d')
    generated_at = datetime.now().strftime('%Y년 %m월 %d일 %H:%M 생성')

    print("3. 스토리 생성 중...")
    story = get_story(articles, digest)

    print("4. Supabase 저장 (reports)...")
    save_to_supabase(articles, digest, story, today, generated_at)

    print("5. 본문 수집 + 한국어 요약 + articles 저장...")
    save_articles_to_db(articles, today)

    print("6. HTML 생성 (GitHub Pages 백업)...")
    html = build_html(articles, digest, generated_at)
    Path('index.html').write_text(html, encoding='utf-8')

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("7. 텔레그램 전송...")
        send_telegram(len(articles), story)

    print("=== 완료 ===")

if __name__ == '__main__':
    main()
