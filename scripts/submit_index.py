#!/usr/bin/env python3
"""
IndexNow 제출기 — 새로 생기거나 바뀐 URL을 IndexNow 참여 검색엔진에 즉시 알린다.

IndexNow 참여 엔진 (2026 기준): Bing, Naver, Yandex, Seznam, Yep
※ Google은 IndexNow에 참여하지 않는다. Google은 sitemap.xml + Search Console 경로로만 간다.

동작 방식
1. 사이트 루트의 '<key>.txt' 파일(내용 = key)이 도메인 소유권 증명 역할을 한다.
2. sitemap.xml의 URL 목록과 상태 파일(content/indexnow_state.json)을 비교해
   아직 알리지 않은 신규 URL만 POST한다. (중복 핑 방지 — 남발하면 품질 점수가 깎인다)
3. 응답이 성공이면 그 URL을 상태 파일에 기록한다.

사용법
  python scripts/submit_index.py          # 신규 URL만 제출 (일상)
  python scripts/submit_index.py --all    # 전체 URL 재제출 (초기 세팅·복구용)
  python scripts/submit_index.py --dry    # 실제 전송 없이 대상만 확인
"""
import json, os, re, sys, time, uuid, ssl, urllib.request, urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

DOMAIN = 'mycapitalanalyze.com'
BASE_URL = 'https://www.' + DOMAIN
ENDPOINT = 'https://api.indexnow.org/indexnow'
KEY_FILE_RE = re.compile(r'^[A-Za-z0-9-]{8,128}\.txt$')
STATE_PATH = os.path.join(BASE, 'content', 'indexnow_state.json')
MAX_PER_RUN = 300          # 한 번에 너무 많이 쏘지 않는다 (서버 측 품질 점수 보호)
BATCH = 1000               # 요청당 최대


def find_key():
    """루트의 <key>.txt 파일에서 IndexNow 키를 찾는다."""
    for name in sorted(os.listdir(BASE)):
        if KEY_FILE_RE.match(name):
            key = os.path.splitext(name)[0]
            try:
                body = open(os.path.join(BASE, name), encoding='utf-8').read().strip()
            except OSError:
                continue
            if body == key:
                return key
    return None


def ensure_key():
    """키 파일이 없으면 새로 만든다. 반드시 커밋·배포돼야 소유권이 증명된다."""
    key = find_key()
    if key:
        return key
    key = uuid.uuid4().hex[:32]
    with open(os.path.join(BASE, key + '.txt'), 'w', encoding='utf-8') as f:
        f.write(key + '\n')
    print(f'[key] 새 IndexNow 키 생성: {key}.txt (이 파일을 꼭 커밋해야 합니다)')
    return key


def read_sitemap_urls():
    path = os.path.join(BASE, 'sitemap.xml')
    if not os.path.exists(path):
        print('[warn] sitemap.xml 없음 — 먼저 build.py를 실행하세요')
        return []
    xml = open(path, encoding='utf-8').read()
    urls = re.findall(r'<loc>\s*(.*?)\s*</loc>', xml)
    # sitemap에는 www 도메인으로 기록돼 있으므로 그대로 사용
    return [u for u in dict.fromkeys(urls) if u.startswith('http')]


def load_state():
    if not os.path.exists(STATE_PATH):
        return {'submitted': [], 'last_run': None}
    try:
        return json.load(open(STATE_PATH, encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {'submitted': [], 'last_run': None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def post_batch(key, urls, dry=False):
    payload = {
        'host': DOMAIN,
        'key': key,
        'keyLocation': f'https://{DOMAIN}/{key}.txt',
        'urlList': urls,
    }
    if dry:
        print(f'  [dry] {len(urls)}개 URL 전송 생략')
        return True
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(ENDPOINT, data=body, method='POST', headers={
        'Content-Type': 'application/json; charset=utf-8',
        'User-Agent': 'MyCapitalAnalyze-IndexNow/1.0',
    })
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=60, context=CTX)
            code = resp.status
            print(f'  응답 {code} (수용: 피어가 수신함)')
            if code in (200, 202):
                return True
            time.sleep(5)
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:200]
            print(f'  HTTP {e.code}: {detail}')
            if e.code == 429:
                print('  요청 과다 — 이번 실행은 중단한다')
                return False
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            print(f'  네트워크 오류({attempt+1}/3): {e}')
            time.sleep(5 * (attempt + 1))
    return False


def main():
    args = sys.argv[1:]
    force_all = '--all' in args
    dry = '--dry' in args

    key = ensure_key()
    print(f'[key] {key}.txt  (검증 URL: https://{DOMAIN}/{key}.txt)')

    urls = read_sitemap_urls()
    print(f'[sitemap] URL {len(urls)}개')

    state = load_state()
    submitted = set(state.get('submitted', []))
    targets = urls if force_all else [u for u in urls if u not in submitted]

    if not targets:
        print('[done] 새로 제출할 URL이 없다 (이미 모두 알림 완료)')
        state['last_run'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if not dry:
            save_state(state)
        return

    limited = len(targets) > MAX_PER_RUN
    batch = targets[:MAX_PER_RUN]
    print(f'[submit] 대상 {len(batch)}개' + (' (전체 중 일부 — 다음 실행에서 계속)' if limited else ''))

    ok_all = True
    for i in range(0, len(batch), BATCH):
        chunk = batch[i:i + BATCH]
        print(f'  전송 {i+1}~{i+len(chunk)}')
        ok = post_batch(key, chunk, dry=dry)
        ok_all = ok_all and ok
        if not ok:
            break
        time.sleep(2)

    if dry:
        print('[dry] 실제 전송 없이 대상만 확인했다')
    elif ok_all:
        state['submitted'] = sorted(submitted | set(batch))
        state['last_run'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save_state(state)
        print(f'[done] 상태 기록 완료 (누적 제출 {len(state["submitted"])}개)')
    else:
        print('[warn] 일부 실패 — 성공분이 상태에 반영되지 않아 다음 실행에 재시도된다')


if __name__ == '__main__':
    main()
