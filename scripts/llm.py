"""공용 LLM 호출 모듈.

정책 (로이 지시 2026-09-19, 최우선 목표 = 비용 최소화):
  * 모든 호출은 **Flash 모델만** 사용한다. 추론형·대형 모델은 금지.
  * 용도별로 제공자를 고정한다:
      - `purpose='write'` (게시글 작성/번역/길이보정) → **GLM**
      - `purpose='check'` (사이트 점검·보고 요약)  → **DeepSeek (Flash)**
  * 1순위가 실패하면(401/402/403/429/5xx/네트워크) 보조 제공자로 **자동 폴백**.
    → 키가 만료돼도 작업이 죽지 않는다. 폴백은 로그에 남긴다.

반환값: (content, provider_name)
실패 시: RuntimeError
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))

# flash_only=true 면 모델명에 'flash' 가 없는 제공자는 건너뛴다.
FLASH_ONLY = bool(CONFIG.get('flash_only', True))

# 용도별 제공자 우선순위 (로이 정책: 글쓰기=GLM / 점검=DeepSeek)
PURPOSE_ORDER = {
    # 게시글 작성·번역·길이보정
    'write': [p.strip().lower() for p in (CONFIG.get('write_provider_order')
                                          or [(CONFIG.get('provider') or 'glm'), 'deepseek'])],
    # 사이트 점검·보고
    'check': [p.strip().lower() for p in (CONFIG.get('check_provider_order')
                                          or [(CONFIG.get('check_provider') or 'deepseek'), 'glm'])],
}
PURPOSE_MODEL = {
    'write': (CONFIG.get('write_model') or '').strip(),
    'check': (CONFIG.get('check_model') or '').strip(),
}

# 제공자별 flash 모델 기본값 (config에 flash 모델이 없을 때 사용)
DEFAULT_FLASH = {
    'deepseek': 'deepseek-v4-flash',
    'glm': 'glm-5.3-flash',
}


def _is_flash(model):
    return 'flash' in (model or '').lower()


def _flash_model(name, sec, override=''):
    """flash_only 정책을 만족하는 모델명을 고른다."""
    model = (override or sec.get('model') or '').strip()
    if not FLASH_ONLY:
        return model or DEFAULT_FLASH.get(name, '')
    if _is_flash(model):
        return model
    # config에 명시된 flash 대체 모델이 있으면 사용
    alt = (sec.get('flash_model') or '').strip()
    if _is_flash(alt):
        return alt
    return DEFAULT_FLASH.get(name, model)


def chain(purpose='write'):
    """사용 가능한 (name, base_url, api_key, model) 목록을 용도별 우선순위대로."""
    order = PURPOSE_ORDER.get(purpose) or PURPOSE_ORDER['write']
    override = PURPOSE_MODEL.get(purpose, '')
    out = []
    for name in order:
        sec = CONFIG.get(name) or {}
        if not (sec.get('api_key') or '').strip():
            continue
        model = _flash_model(name, sec, override if name == order[0] else '')
        if not model:
            continue
        if FLASH_ONLY and not _is_flash(model):
            continue
        out.append((name, sec['base_url'].rstrip('/'), sec['api_key'].strip(), model))
    return out


def chat(messages, max_tokens=16384, temperature=0.6, response_format=None,
         timeout=180, retries=2, log=print, purpose='write'):
    """Flash 모델로 채팅 완료를 호출한다. 실패 시 다음 제공자로 폴백.

    purpose: 'write' (GLM) | 'check' (DeepSeek Flash)
    반환: (content, provider_name)
    """
    providers = chain(purpose)
    if not providers:
        raise RuntimeError('사용 가능한 LLM 제공자가 없다 (purpose=%s, flash_only=%s)'
                           % (purpose, FLASH_ONLY))

    last_err = None
    for name, base_url, api_key, model in providers:
        body = {
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
        }
        if response_format:
            body['response_format'] = response_format
        if name == 'deepseek':
            body['thinking'] = {'type': 'disabled'}

        data = json.dumps(body).encode('utf-8')
        for attempt in range(1, retries + 2):
            req = urllib.request.Request(
                base_url + '/chat/completions', data=data,
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + api_key},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    d = json.loads(resp.read().decode('utf-8', 'ignore'))
                content = ((d.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
                if not content.strip():
                    # 추론 모델에서 빈 응답이 오는 케이스 → 재시도
                    last_err = 'empty content'
                    time.sleep(2)
                    continue
                return content, name
            except urllib.error.HTTPError as e:
                detail = e.read().decode('utf-8', 'ignore')[:200]
                last_err = 'HTTP %s %s' % (e.code, detail)
                log('  [llm] %s 실패: %s' % (name, last_err))
                if e.code in (401, 402, 403, 404, 429):
                    break  # 다른 제공자로 폴백
                if attempt <= retries:
                    time.sleep(3)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = 'network: %s' % str(e)[:120]
                log('  [llm] %s 네트워크 오류(%d/%d)' % (name, attempt, retries + 1))
                time.sleep(3)
        log('  [llm] %s 포기 → 다음 제공자로 폴백' % name)

    raise RuntimeError('모든 LLM 제공자 실패: %s' % last_err)


def selftest(purpose='write'):
    """Flash 모델이 실제로 응답하는지 점검. (text, provider) 반환."""
    return chat([{'role': 'user', 'content': 'Reply with only the word: PONG'}],
                max_tokens=64, temperature=0.1, timeout=60, purpose=purpose)


if __name__ == '__main__':
    print('flash_only:', FLASH_ONLY)
    for purpose in ('write', 'check'):
        print('--- purpose=%s  order=%s' % (purpose, PURPOSE_ORDER[purpose]))
        for row in chain(purpose):
            print('    available:', row[0], '/', row[3])
        try:
            text, prov = selftest(purpose)
            print('    selftest OK -> provider=%s, reply=%r' % (prov, text.strip()[:60]))
        except Exception as e:
            print('    selftest FAIL:', str(e)[:200])
