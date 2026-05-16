#!/usr/bin/env python3
"""OPML 파일을 파싱해서 Supabase feeds 테이블에 저장합니다.

사용법:
  python3 opml.py your-feeds.opml

OPML 폴더 구조가 태그로 저장됩니다.
  <outline text="논문">
    <outline xmlUrl="https://..." text="arXiv AI"/>
  </outline>
  → feeds 테이블에 tag='논문' 으로 저장
"""
import sys, os
from xml.etree import ElementTree as ET
from supabase import create_client

def parse_opml(path):
    import re
    content = open(path, encoding='utf-8').read()
    # & in URLs가 &amp;로 이스케이프 안 된 경우 수정
    content = re.sub(r'&(?!(amp|lt|gt|quot|apos);)', '&amp;', content)
    root = ET.fromstring(content)
    body = root.find('body')
    feeds = []

    def walk(node, parent_tag=None):
        url  = node.get('xmlUrl')
        name = node.get('title') or node.get('text') or url or '(이름없음)'
        tag  = parent_tag or node.get('category') or '기타'

        if url:
            feeds.append({'url': url.strip(), 'name': name.strip(), 'tag': tag.strip()})
        else:
            # 이 노드는 폴더 — 자식에 현재 text를 태그로 전달
            folder_tag = (node.get('title') or node.get('text') or parent_tag or '기타').strip()
            for child in node:
                walk(child, folder_tag)
            return

        for child in node:
            walk(child, tag)

    for outline in body:
        walk(outline)

    return feeds

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    feeds = parse_opml(sys.argv[1])
    print(f"파싱된 피드: {len(feeds)}개\n")

    url = os.getenv('SUPABASE_URL') or ''
    key = os.getenv('SUPABASE_KEY') or ''
    if not url or not key:
        # .env 직접 읽기
        if os.path.exists('.env'):
            for line in open('.env'):
                line = line.strip()
                if line.startswith('SUPABASE_URL='):
                    url = line.split('=', 1)[1]
                elif line.startswith('SUPABASE_KEY='):
                    key = line.split('=', 1)[1]

    if not url or not key:
        print("오류: SUPABASE_URL / SUPABASE_KEY 환경 변수가 없어요.")
        sys.exit(1)

    sb = create_client(url, key)

    ok = 0
    for f in feeds:
        if not f['url']:
            continue
        try:
            sb.table('feeds').upsert(
                {'url': f['url'], 'name': f['name'], 'tag': f['tag'], 'active': True},
                on_conflict='url'
            ).execute()
            print(f"  ✓ [{f['tag']}] {f['name']}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {f['name']}: {e}")

    print(f"\n완료: {ok}/{len(feeds)}개 저장")

if __name__ == '__main__':
    main()
