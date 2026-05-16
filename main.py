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

def _extract(html):
    return trafilatura.extract(
        html, include_comments=False, include_tables=False,
        favor_recall=True, no_fallback=False,
    )

def fetch_content(url):
    # 전략 1: Google 리퍼러 + 브라우저 UA (soft paywall 대부분 통과)
    for ua, ref in [
        (_BROWSER_UA,   'https://www.google.com/'),
        (_GOOGLEBOT_UA, ''),
    ]:
        try:
            headers = {'User-Agent': ua, 'Accept-Language': 'en-US,en;q=0.9'}
            if ref:
                headers['Referer'] = ref
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if resp.ok and len(resp.text) > 500:
                text = _extract(resp.text)
                if text and len(text) > 200:
                    return text[:5000]
        except Exception:
            pass

    # 전략 2: trafilatura 기본 fetcher
    try:
        html = trafilatura.fetch_url(url)
        if html:
            text = _extract(html)
            if text:
                return text[:5000]
    except Exception:
        pass

    return None

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

def load_feeds_from_db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        res = sb.table('feeds').select('id,url,name,tag').eq('active', True).execute()
        return [{'id': f['id'], 'url': f['url'], 'name': f['name'], 'cat': f['tag']} for f in (res.data or [])]
    except Exception:
        return None

def fetch_articles(max_per_feed=5):
    feeds = load_feeds_from_db() or [{'id': None, **{k: f[k] for k in ('url','name','cat')}} for f in RSS_FEEDS]
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
                articles.append({'feed_id': feed.get('id'), 'source': feed['name'], 'cat': feed['cat'],
                    'title': entry.get('title','')[:120], 'summary': summary,
                    'url': entry.get('link',''), 'date': entry.get('published','')[:16],
                    'published_at': pub})
        except Exception as e:
            print(f"  [skip] {feed['name']}: {e}")
    return articles

def save_articles_to_db(articles, today):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 본문 병렬 수집 (최대 10개 동시)
    valid = [a for a in articles if a.get('url')]
    print(f"   본문 수집 중 ({len(valid)}개)…")
    contents = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_content, a['url']): a['url'] for a in valid}
        done = 0
        for fut in as_completed(futs):
            contents[futs[fut]] = fut.result()
            done += 1
            if done % 10 == 0:
                print(f"   {done}/{len(valid)}")

    rows = [{'feed_id': a.get('feed_id'), 'title': a['title'], 'url': a['url'],
             'summary': a.get('summary', ''), 'content': contents.get(a['url']),
             'published_at': a.get('published_at') or None, 'date': today}
            for a in valid]

    for i in range(0, len(rows), 50):
        try:
            sb.table('articles').upsert(rows[i:i+50], on_conflict='url').execute()
        except Exception as e:
            print(f"  [articles upsert warn] {e}")

def ask_claude(prompt):
    result = subprocess.run(['claude', '-p', prompt], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"claude 오류: {result.stderr}")
    return result.stdout.strip()

def get_digest(articles):
    listing = '\n'.join(f"[{i}] ({a['cat']}) {a['source']} | {a['title']} | {a['summary'][:120]}"
                        for i, a in enumerate(articles[:45]))
    prompt = f"""다음 AI 뉴스 목록을 분석해서 JSON만 반환해줘 (코드블록, 설명 없이).

{listing}

형식:
{{"trends":["트렌드1","트렌드2","트렌드3"],"clusters":[{{"label":"이름","desc":"한줄설명","theme":"큰개념","indices":[0,1,2],"color":"#185FA5","bg":"#E6F1FB","textColor":"#0C447C","themeColor":"#0F6E56","themeBg":"#E1F5EE"}}],"must_reads":[{{"index":0,"why":"이유","hook":"연구 연관성"}}],"explanations":{{"0":"3문장 설명"}}}}

clusters 3개, must_reads 2개, 색상 파랑/보라/초록 계열로 다르게. JSON만."""
    raw = ask_claude(prompt).lstrip('```json').lstrip('```').rstrip('```').strip()
    return json.loads(raw)

def build_html(articles, digest, generated_at):
    data_js = json.dumps({'articles':articles,'digest':digest,'generated_at':generated_at,
        'total':len(articles),'counts':{'논문':sum(1 for a in articles if a['cat']=='논문'),
        '뉴스':sum(1 for a in articles if a['cat']=='뉴스'),
        '뉴스레터':sum(1 for a in articles if a['cat']=='뉴스레터'),
        '커뮤니티':sum(1 for a in articles if a['cat']=='커뮤니티')}}, ensure_ascii=False)
    return open('template.html').read().replace('__DATA__', data_js).replace('__GENTIME__', generated_at)

def save_to_supabase(articles, digest, today, generated_at):
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("   [skip] SUPABASE_URL/KEY 없음")
        return
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    counts = {cat: sum(1 for a in articles if a['cat'] == cat)
              for cat in ['논문', '뉴스', '뉴스레터', '커뮤니티']}
    sb.table('reports').upsert({
        'date': today,
        'articles': articles,
        'digest': digest,
        'total': len(articles),
        'counts': counts,
        'generated_at': generated_at,
    }, on_conflict='date').execute()

def send_telegram(reader_url, dashboard_url, total):
    today = datetime.now().strftime('%m/%d')
    text = (f"📰 오늘의 AI 뉴스 ({today}) — {total}개 수집\n\n"
            f"👆 리더 (Unread 스타일)\n{reader_url}\n\n"
            f"📊 대시보드 (클러스터 분석)\n{dashboard_url}")
    requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': text}, timeout=10)

def main():
    print("1. RSS 수집 중...")
    articles = fetch_articles()
    print(f"   {len(articles)}개 수집")
    if not articles: sys.exit(1)

    print("2. Claude 분석 중...")
    digest = get_digest(articles)
    print(f"   클러스터 {len(digest['clusters'])}개")

    today = datetime.now().strftime('%Y-%m-%d')
    generated_at = datetime.now().strftime('%Y년 %m월 %d일 %H:%M 생성')

    print("3. Supabase 저장...")
    save_to_supabase(articles, digest, today, generated_at)
    save_articles_to_db(articles, today)

    print("4. HTML 생성 (GitHub Pages 백업)...")
    html = build_html(articles, digest, generated_at)
    Path('index.html').write_text(html, encoding='utf-8')
    print("   완료")

    reader_url    = (GITHUB_PAGES_URL.rstrip('/') + '/reader.html') if GITHUB_PAGES_URL else ''
    dashboard_url = STREAMLIT_APP_URL or GITHUB_PAGES_URL
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and reader_url:
        print("5. 텔레그램 전송...")
        send_telegram(reader_url, dashboard_url, len(articles))

    print("=== 완료 ===")

if __name__ == '__main__':
    main()
