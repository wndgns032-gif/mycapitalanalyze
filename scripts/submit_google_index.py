#!/usr/bin/env python3
"""
Google Indexing API 제출기 — 새로 생긴 URL을 구글에 즉시 알린다.

왜 필요한가
  IndexNow(submit_index.py)는 Bing/Naver/Yandex/Seznam/Yep만 커버한다.
  Google은 IndexNow에 참여하지 않으므로 별도 통로가 필요하고, 그게 이 스크립트다.

준비물 (최초 1회, 로이가 직접 — GCP 콘솔 + Search Console)
  1. GCP 프로젝트에서 "Indexing API" 사용 설정
  2. 서비스 계정 생성 → JSON 키 발급
  3. Search Console 해당 속성에 서비스 계정 이메일을 '소유자'로 추가  ← 이걸 빼먹으면 403
  4. JSON 키 전체를 GitHub Secret GOOGLE_SA_JSON 에 등록

동작 방식
  1. sitemap.xml에서 (url, lastmod)를 읽는다.
  2. content/google_index_state.json 과 비교해 아직 알리지 않은 URL만 고른다.
  3. lastmod 최신순으로 정렬해 하루 쿼터(기본 200) 안에서만 전송한다.
  4. 성공한 URL만 상태 파일에 기록한다. (실패분은 다음 실행에 재시도)

예외 처리 방침
  키가 없거나 실패해도 **절대 exit code 를 0 이 아닌 값으로 올리지 않는다.**
  이 스크립트는 워크플로의 부가 단계다 — 여기서 죽으면 발행 파이프라인 전체가 실패로 찍힌다.

사용법
  python scripts/submit_google_index.py          # 신규 URL만 제출
  python scripts/submit_google_index.py --all    # 상태 무시하고 전체 재제출 (복구용)
  python scripts/submit_google_index.py --dry    # 전송 없이 대상만 확인
  python scripts/submit_google_index.py --limit 50
"""
import json, os, re, sys, time
import urllib.request, urllib.error, urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOMAIN = 'mycapitalanalyze.com'
SCOPE = 'https://www.googleapis.com/auth/indexing'
PUBLISH_URL = 'https://indexing.googleapis.com/v3/urlNotifications:publish'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
STATE_PATH = os.path.join(BASE, 'content', 'google_index_state.json')

DAILY_QUOTA = 200     # Google Indexing API 기본 일일 할당량
MAX_PER_RUN = 200
UA = 'MyCapitalAnalyze-GoogleIndex/1.0'


def log(msg):
    print(msg, flush=True)


def load_credentials():
    """GitHub Secret(GOOGLE_SA_JSON) 또는 로컬 파일에서 서비스 계정을 읽는다."""
    raw = os.environ.get('GOOGLE_SA_JSON', '').strip()
    if not raw:
        for cand in (os.path.join(BASE, '.secrets', 'google-sa.json'),
                     os.path.join(BASE, 'google-sa.json')):
            if os.path.exists(cand):
                raw = open(cand, encoding='utf-8').read()
                break
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log(f'[err] 서비스 계정 JSON 파싱 실패: {e}')
        return None


def get_token(sa):
    """서비스 계정으로 OAuth2 액세스 토큰을 받는다. google-auth 가 있으면 그것을, 없으면 직접 JWT."""
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as AuthRequest
        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=[SCOPE])
        creds.refresh(AuthRequest())
        return creds.token
    except ImportError:
        pass
    except Exception as e:
        log(f'[warn] google-auth 경로 실패: {e}')

    # 폴백: 순수 stdlib + cryptography 로 JWT 직접 서명
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        log('[err] google-auth 도 cryptography 도 없다. pip install google-auth 필요')
        return None

    import base64, hashlib

    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

    now = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    claim = {
        'iss': sa['client_email'],
        'scope': SCOPE,
        'aud': TOKEN_URL,
        'iat': now,
        'exp': now + 3600,
    }
    signing_input = (b64(json.dumps(header, separators=(',', ':')).encode())
                     + '.' + b64(json.dumps(claim, separators=(',', ':')).encode()))
    key = serialization.load_pem_private_key(sa['private_key'].encode(), password=None)
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    assertion = signing_input + '.' + b64(sig)

    body = urllib.parse.urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': assertion,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())['access_token']


def read_sitemap():
    """[(url, lastmod)] 를 lastmod 내림차순으로 돌려준다."""
    path = os.path.join(BASE, 'sitemap.xml')
    if not os.path.exists(path):
        log('[warn] sitemap.xml 없음 — build.py를 먼저 실행하세요')
        return []
    xml = open(path, encoding='utf-8').read()
    blocks = re.findall(r'<url\b.*?</url>', xml, re.S) or re.findall(r'<sitemap\b.*?</sitemap>', xml, re.S)
    items = []
    for b in blocks:
        m = re.search(r'<loc>\s*(.*?)\s*</loc>', b)
        if not m:
            continue
        lm = re.search(r'<lastmod>\s*(.*?)\s*</lastmod>', b)
        items.append((m.group(1), lm.group(1) if lm else ''))
    if not items:
        items = [(u, '') for u in re.findall(r'<loc>\s*(.*?)\s*</loc>', xml)]
    seen, out = set(), []
    for u, lm in items:
        if u.startswith('http') and u not in seen:
            seen.add(u)
            out.append((u, lm))
    # 실제 글(/post/)을 정적 페이지(about·privacy·disclosure)보다 먼저 알린다.
    # 쿼터가 하루 200개뿐이라 정적 페이지에 먼저 쓰면 정작 새 글이 밀린다.
    def rank(item):
        u, lm = item
        is_post = 1 if '/post/' in u else 0
        return (is_post, lm)
    out.sort(key=rank, reverse=True)
    return out


def load_state():
    if not os.path.exists(STATE_PATH):
        return {'submitted': [], 'last_run': None, 'quota': {'date': None, 'used': 0}}
    try:
        st = json.load(open(STATE_PATH, encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        st = {}
    st.setdefault('submitted', [])
    st.setdefault('last_run', None)
    st.setdefault('quota', {'date': None, 'used': 0})
    return st


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def publish(token, url, retries=2):
    body = json.dumps({'url': url, 'type': 'URL_UPDATED'}).encode('utf-8')
    for attempt in range(retries + 1):
        req = urllib.request.Request(PUBLISH_URL, data=body, method='POST', headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
            'User-Agent': UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status in (200, 201), resp.status, ''
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:200]
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(3 * (attempt + 1))
                continue
            return False, e.code, detail
        except Exception as e:
            time.sleep(3 * (attempt + 1))
    return False, 0, 'timeout'


def main():
    args = sys.argv[1:]
    force_all = '--all' in args
    dry = '--dry' in args
    limit = MAX_PER_RUN
    if '--limit' in args:
        try:
            limit = int(args[args.index('--limit') + 1])
        except (ValueError, IndexError):
            pass

    state = load_state()
    today = time.strftime('%Y-%m-%d', time.gmtime())
    if state['quota'].get('date') != today:
        state['quota'] = {'date': today, 'used': 0}
    remaining = max(0, DAILY_QUOTA - state['quota'].get('used', 0))
    budget = min(limit, remaining)
    log(f'[quota] 오늘 남은 할당 {remaining}개 (사용 {state["quota"].get("used", 0)}/{DAILY_QUOTA})')

    items = read_sitemap()
    submitted = set(state['submitted'])
    targets = [(u, lm) for u, lm in items if force_all or u not in submitted]
    log(f'[sitemap] 전체 {len(items)}개 / 미제출 {len(targets)}개')

    if not targets:
        log('[done] 새로 제출할 URL이 없다')
        state['last_run'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if not dry:
            save_state(state)
        return

    batch = targets[:budget]
    log(f'[submit] 이번 실행 대상 {len(batch)}개'
        + (' (전체 중 일부 — 다음 실행에서 계속)' if len(targets) > len(batch) else ''))

    if dry:
        for u, lm in batch[:10]:
            log(f'  [dry] {lm[:10]}  {u}')
        if len(batch) > 10:
            log(f'  ... 외 {len(batch) - 10}개')
        return

    sa = load_credentials()
    if sa is None:
        log('[skip] GOOGLE_SA_JSON 시크릿이 없다 — Google Indexing 생략 '
            '(IndexNow만으로도 Bing/Naver/Yandex는 커버된다)')
        return
    log(f'[auth] 서비스 계정: {sa.get("client_email", "?")}')

    token = get_token(sa)
    if not token:
        log('[err] 토큰 발급 실패 — 이번 실행은 건너뛴다')
        return

    ok_urls, failed = [], 0
    for i, (u, lm) in enumerate(batch, 1):
        ok, code, detail = publish(token, u)
        if ok:
            ok_urls.append(u)
        else:
            failed += 1
            log(f'  [{i}/{len(batch)}] 실패 {code} {detail[:120]}')
            if code in (401, 403):
                log('  인증/권한 오류 — Search Console에 서비스 계정을 소유자로 추가했는지 확인 필요. 중단.')
                break
        if i % 10 == 0:
            log(f'  ... {i}/{len(batch)} 진행')
        time.sleep(0.35)

    if ok_urls:
        state['submitted'] = sorted(submitted | set(ok_urls))
        state['quota']['used'] = state['quota'].get('used', 0) + len(ok_urls)
        log(f'[done] {len(ok_urls)}개 제출 완료 (누적 {len(state["submitted"])}개)')
    if failed:
        log(f'[warn] {failed}개 실패 — 다음 실행에 재시도된다')
    state['last_run'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    save_state(state)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # 워크플로를 죽이지 않기 위해 모든 예외를 삼킨다
        print(f'[fatal] {type(e).__name__}: {e}', flush=True)
    sys.exit(0)
