#!/usr/bin/env python3
"""
RSS 크롤러 — 매크로/경제 뉴스 피드에서 새 글을 수집해 원문으로 저장.
중복 감지(processed.json)로 이미 처리한 글은 스킵.

사용법: python scripts/crawler.py
출력: content/raw/{slug}.json (원문 메타 + 본문), content/processed.json (처리 이력)
"""
import json, os, re, sys, time, hashlib, datetime, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = json.load(open(os.path.join(BASE, 'config_sources.json'), encoding='utf-8'))
RAW_DIR = os.path.join(BASE, 'content', 'raw')
PROCESSED_PATH = os.path.join(BASE, 'content', 'processed.json')
os.makedirs(RAW_DIR, exist_ok=True)

MAX_NEW = SRC.get('max_new_per_run', 3)
USER_AGENT = 'Mozilla/5.0 (compatible; MyCapitalAnalyze/1.0; +https://mycapitalanalyze.com)'

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.read()
    except urllib.error.HTTPError as e:
        print(f'  HTTP {e.code} for {url}')
        return None
    except urllib.error.URLError as e:
        print(f'  URLError for {url}: {e.reason}')
        return None

def strip_html(s):
    if not s:
        return ''
    s = re.sub(r'<script.*?</script>', ' ', s, flags=re.S)
    s = re.sub(r'<style.*?</style>', ' ', s, flags=re.S)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def parse_feed(xml_bytes):
    """RSS 2.0 또는 Atom에서 항목 목록 추출. 각 항목: {title, link, description, pubDate}"""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items
    # Atom
    if root.tag.endswith('feed'):
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for e in root.findall('a:entry', ns):
            title = e.findtext('a:title', '', ns).strip()
            link = e.findtext('a:link[@rel="alternate"]/@href', '', ns) or e.findtext('a:link/@href', '', ns)
            desc = e.findtext('a:summary', '', ns) or e.findtext('a:content', '', ns)
            date = e.findtext('a:updated', '', ns) or e.findtext('a:published', '', ns)
            if title and link:
                items.append({'title': title, 'link': link, 'description': strip_html(desc), 'pubDate': date})
    # RSS 2.0
    else:
        for it in root.iter('item'):
            title = it.findtext('title', '').strip()
            link = it.findtext('link', '').strip()
            desc = it.findtext('description', '') or it.findtext('content:encoded', '')
            date = it.findtext('pubDate', '')
            if title and link:
                items.append({'title': title, 'link': link, 'description': strip_html(desc), 'pubDate': date})
    return items

def slugify(source_name, link):
    h = hashlib.sha1(link.encode('utf-8')).hexdigest()[:10]
    name = re.sub(r'[^a-z0-9]+', '-', source_name.lower()).strip('-')
    return f'{name}-{h}'

def parse_date(s):
    """RSS pubDate/updated 문자열을 datetime으로. 실패 시 최소값(오래된 것)으로 처리."""
    if not s:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

def main():
    processed = {}
    if os.path.exists(PROCESSED_PATH):
        processed = json.load(open(PROCESSED_PATH, encoding='utf-8'))

    # 1) 모든 피드의 새 항목 수집 (피드 정보 포함)
    candidates = []
    for feed in SRC['feeds']:
        name, url = feed['name'], feed['url']
        print(f'[{name}] {url}')
        xml_bytes = fetch(url)
        if not xml_bytes:
            continue
        items = parse_feed(xml_bytes)
        print(f'  항목 {len(items)}개')
        for it in items:
            if it['link'] in processed:
                continue
            candidates.append((it, feed, name))

    # 2) 발행일 기준 최신순 정렬
    candidates.sort(key=lambda c: parse_date(c[0].get('pubDate')), reverse=True)
    print(f'새 후보 {len(candidates)}개 (최신순 정렬 완료)')

    # 3) 최신 글부터 최대 MAX_NEW개 수집
    collected = 0
    for it, feed, name in candidates:
        if collected >= MAX_NEW:
            break
        link = it['link']
        slug = slugify(name, link)
        raw = {
            'slug': slug,
            'source_name': name,
            'source_url': link,
            'category': feed['category'],
            'title': it['title'],
            'description': it['description'][:500],
            'pubDate': it['pubDate'],
            'fetched_at': datetime.datetime.utcnow().isoformat() + 'Z',
        }
        out_path = os.path.join(RAW_DIR, slug + '.json')
        json.dump(raw, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        processed[link] = {'slug': slug, 'at': raw['fetched_at']}
        print(f'  NEW: {slug} ({it.get("pubDate", "날짜없음")})')
        collected += 1
        time.sleep(0.3)

    json.dump(processed, open(PROCESSED_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'수집 완료: {collected}개 새 글 (최신 우선순위)')

if __name__ == '__main__':
    main()
