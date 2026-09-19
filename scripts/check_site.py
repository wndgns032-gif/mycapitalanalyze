"""사이트 주간 점검 (Weekly health check).

- 사실 수집(HTTP 상태·태그 카운트·사이트맵)은 **코드로** 한다. (LLM 비용 0)
- 최종 요약 문장만 **DeepSeek Flash** 로 만든다. (purpose='check')
  → scripts/llm.py 가 deepseek → 실패 시 glm 으로 자동 폴백한다.

사용법: python scripts/check_site.py
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
# Windows 콘솔/PowerShell 리다이렉트에서 한글이 깨지는 것을 방지
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
import llm  # noqa: E402

DOMAIN = 'https://www.mycapitalanalyze.com'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36'

PUBLIC_CFG = json.load(open(os.path.join(BASE, 'config.public.json'), encoding='utf-8'))
ANALYTICS = PUBLIC_CFG.get('analytics') or {}
ADS_ENABLED = bool((PUBLIC_CFG.get('adsense') or {}).get('enabled'))
AFF_URL = ''
for o in ((PUBLIC_CFG.get('affiliates') or {}).get('offers') or []):
    if (o.get('url') or '').strip():
        AFF_URL = o['url'].strip()
        break

# 점검 대상 URL (대표 페이지만 — 전수 조사는 비용만 늘린다)
URLS = [
    ('홈(en)', DOMAIN + '/'),
    ('홈(ko)', DOMAIN + '/ko/'),
    ('포스트(ko)', DOMAIN + '/ko/post/fed-rate-outlook-2026.html'),
    ('고지(en)', DOMAIN + '/disclosure.html'),
    ('about', DOMAIN + '/about.html'),
]
EXTRA = [
    ('사이트맵', DOMAIN + '/sitemap.xml'),
    ('IndexNow키', DOMAIN + '/2a8cd5dd8042441c87d290839af70705.txt'),
    ('robots', DOMAIN + '/robots.txt'),
]

# 반드시 있어야 하는 태그 / 절대 있으면 안 되는 것
REQUIRED = [
    ('google-site-verification', ANALYTICS.get('google_site_verification')),
    ('naver-site-verification', ANALYTICS.get('naver_site_verification')),
    ('yandex-verification', ANALYTICS.get('yandex_verification')),
    ('msvalidate.01', ANALYTICS.get('bing_site_verification')),
]
FORBIDDEN = ['adsbygoogle', 'pagead2.googlesyndication']


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.status, r.read().decode('utf-8', 'ignore')


def main():
    problems = []
    lines = []

    for label, url in URLS:
        try:
            st, body = fetch(url)
        except urllib.error.HTTPError as e:
            problems.append(f'{label} {url} → HTTP {e.code}')
            lines.append(f'  {label:12} HTTP {e.code}  (실패)')
            continue
        except Exception as e:
            problems.append(f'{label} {url} → {type(e).__name__}')
            lines.append(f'  {label:12} 오류 {type(e).__name__}')
            continue

        row = []
        if st != 200:
            problems.append(f'{label} HTTP {st}')
        row.append(f'HTTP {st}')

        for tag, val in REQUIRED:
            if not val:
                row.append(f'{tag}=설정없음')
                continue
            ok = f'content="{val}"' in body
            row.append(f'{tag}={"OK" if ok else "누락"}')
            if not ok:
                problems.append(f'{label} {tag} 누락')
        if ADS_ENABLED:
            row.append(f'광고={"OK" if "adsbygoogle" in body else "누락(활성설정인데 없음)"}')
            if 'adsbygoogle' not in body and '/post/' in url:
                problems.append(f'{label} 애드센스 활성인데 코드 없음')
        else:
            hits = [f for f in FORBIDDEN if f in body]
            row.append(f'광고={"0(정상)" if not hits else "잔존!"}')
            if hits:
                problems.append(f'{label} 애드센스 미승인인데 코드 잔존: {hits}')

        if AFF_URL and '/post/' in url:
            ok = AFF_URL in body
            row.append(f'제휴={"OK" if ok else "누락"}')
            if not ok:
                problems.append(f'{label} 제휴 링크 누락')
        lines.append(f'  {label:12} ' + ' | '.join(row))

    sitemap_urls = 0
    for label, url in EXTRA:
        try:
            st, body = fetch(url)
            if label == '사이트맵':
                sitemap_urls = len(re.findall(r'<loc>', body))
                lines.append(f'  {label:12} HTTP {st} | URL {sitemap_urls}개')
            else:
                lines.append(f'  {label:12} HTTP {st} | {len(body)} bytes')
            if st != 200:
                problems.append(f'{label} HTTP {st}')
        except Exception as e:
            problems.append(f'{label} {type(e).__name__}')
            lines.append(f'  {label:12} 오류 {type(e).__name__}')

    print('=== mycapitalanalyze.com 주간 점검 ===')
    print('\n'.join(lines))
    print()
    print('이상 항목: %d건' % len(problems))
    for p in problems:
        print('  -', p)

    # ---- 요약은 DeepSeek Flash 1회 호출 (비용 최소) ----
    facts = '\n'.join(lines)
    if problems:
        facts += '\n이상 항목:\n' + '\n'.join('- ' + p for p in problems)
    else:
        facts += '\n이상 항목: 없음'

    prompt = (
        '아래는 금융 블로그 mycapitalanalyze.com 주간 자동 점검 결과다. '
        '한국어로 5줄 이내로 요약해라. '
        '정상이면 "정상"이라고 밝히고, 이상이 있으면 무엇을 고쳐야 하는지 구체적으로 적어라. '
        '추측하지 말고 주어진 사실만 써라.\n\n' + facts
    )
    try:
        summary, provider = llm.chat(
            [{'role': 'user', 'content': prompt}],
            max_tokens=500, temperature=0.3, timeout=90, purpose='check',
        )
        print()
        print('=== 요약 (LLM: %s) ===' % provider)
        print(summary.strip())
    except Exception as e:
        print()
        print('=== 요약 생성 실패(점검 자체는 완료): %s ===' % str(e)[:200])

    return len(problems)


if __name__ == '__main__':
    sys.exit(0 if main() == 0 else 1)
