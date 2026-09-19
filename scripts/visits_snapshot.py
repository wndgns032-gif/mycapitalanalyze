#!/usr/bin/env python3
"""방문자 카운터 스냅샷 백업.

집계 서버에서 일별 방문자 수를 읽어 `data/visits.json` 으로 저장한다.
목적: 외부 카운터 서비스가 사라져도 지금까지의 히스토리를 복원할 수 있게 보관.
      stats.html 은 이 파일을 '읽기 실패 시 대체값(fallback)'으로 사용한다.

사용법: python scripts/visits_snapshot.py
"""
import concurrent.futures
import json
import os
import urllib.request
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = 'mca-visit-7f3q'                              # assets/js/main.js 와 동일해야 함
API = 'https://tallywire.cronpulse.workers.dev'
START = date(2026, 9, 19)                          # 집계 시작일 (KST)
OUT = os.path.join(BASE, 'data', 'visits.json')


def get_day(d):
    key = 'v' + d.strftime('%Y%m%d')
    try:
        # Cloudflare가 기본 User-Agent(Python-urllib)를 차단하므로 UA를 넣어야 한다.
        req = urllib.request.Request(API + '/get/%s/%s' % (NS, key),
                                     headers={'User-Agent': 'Mozilla/5.0 (visits-snapshot)',
                                              'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode('utf-8', 'ignore'))
        return d.isoformat(), int(j.get('value') or 0)
    except Exception:
        return d.isoformat(), None


def main():
    today = date.today()
    days, d = [], START
    while d <= today:
        days.append(d)
        d += timedelta(days=1)

    out, ok = {}, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for day, v in ex.map(get_day, days):
            if v is None:
                continue
            ok += 1
            if v or day == today.isoformat():
                out[day] = v

    # 이전 스냅샷과 병합(읽기 실패한 날은 예전 값 유지)
    old = {}
    if os.path.exists(OUT):
        try:
            old = (json.load(open(OUT, encoding='utf-8')) or {}).get('days') or {}
        except Exception:
            old = {}
    merged = dict(old)
    merged.update(out)

    total = sum(merged.values())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({'updated': today.isoformat(), 'start': START.isoformat(),
               'total': total, 'days': dict(sorted(merged.items()))},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('스냅샷 저장: %s (읽기 성공 %d일, 누적 %d명)' % (OUT, ok, total))


if __name__ == '__main__':
    main()
