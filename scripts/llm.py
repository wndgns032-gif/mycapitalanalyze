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
    # 게시글 작성·번역·길이보정 — 무료 제공자 우선, 유료 DeepSeek 는 폴백
    'write': [p.strip().lower() for p in (CONFIG.get('write_provider_order')
                                          or DEFAULT_WRITE_ORDER)],
    # 사이트 점검·보고
    'check': [p.strip().lower() for p in (CONFIG.get('check_provider_order')
                                          or DEFAULT_CHECK_ORDER)],
}
PURPOSE_MODEL = {
    'write': (CONFIG.get('write_model') or '').strip(),
    'check': (CONFIG.get('check_model') or '').strip(),
}

# 제공자별 flash 모델 기본값 (config에 flash 모델이 없을 때 사용)
# ⚠️ 로이 지시(2026-09-30): DeepSeek 은 반드시 flash 로만 호출한다.
#    실측(api.deepseek.com/models, 2026-09-30) 결과 DeepSeek 가 제공하는 모델은
#    'deepseek-flash'(DeepSeek-V4.1-Flash) 와 'deepseek-v4-pro' 두 개뿐이다.
#    'deepseek-v4-flash' 는 존재하지 않는 이름이라 404 가 난다. 절대 쓰지 말 것.
DEFAULT_FLASH = {
    'deepseek': 'deepseek-flash',
    'glm': 'glm-5.3-flash',
    # 무료 제공자 기본 모델 (2026-10-02 로이 지시: 무료 API 우선 → 실패 시 DeepSeek)
    'gemini': 'gemini-flash-latest',        # Google AI Studio 무료 티어 (별칭이라 항상 최신 flash)
    'groq': 'openai/gpt-oss-20b',           # Groq 무료 개발자 티어 (30 RPM / 1,000 RPD)
    'openrouter': 'openrouter/free',        # OpenRouter 무료 모델 라우터 (하루 50회)
    'nvidia': 'nvidia/deepseek-v4-flash',   # NVIDIA NIM 무료 티어 (일일 토큰 상한 없음)
}

# 무료 제공자 — 비용이 0원이므로 flash 전용 정책(비용 통제 목적)을 적용하지 않는다.
# config 의 섹션에 "free": true 로 표시하거나 이름이 이 목록에 있으면 예외 처리한다.
FREE_PROVIDERS = {'gemini', 'groq', 'openrouter', 'nvidia', 'cloudflare', 'mistral', 'cohere'}

# config 에 순서가 없을 때의 기본 사슬 — 무료 → GLM → DeepSeek(유료 폴백)
DEFAULT_WRITE_ORDER = ['gemini', 'groq', 'openrouter', 'nvidia', 'glm', 'deepseek']
DEFAULT_CHECK_ORDER = ['groq', 'gemini', 'openrouter', 'deepseek']


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
    """사용 가능한 (name, base_url, api_key, model) 목록을 용도별 우선순위대로.

    무료 제공자(비용 0원)는 flash 전용 정책의 예외다 — 정책 목적이 비용 통제이기 때문.
    """
    order = PURPOSE_ORDER.get(purpose) or PURPOSE_ORDER['write']
    override = PURPOSE_MODEL.get(purpose, '')
    # ⚠️ 2026-10-05 실측 버그: override 를 order[0] 에 적용했는데, 무료 제공자
    #   (gemini/groq) 가 1순위가 되자 **Gemini 에 'deepseek-flash' 모델명이 붙어**
    #   404 가 났다("models/deepseek-flash is not found ... for generateContent").
    #   write_model/check_model 은 원래 유료 폴백(DeepSeek)용 값이다
    #   → override 는 체인의 **마지막(폴백) 제공자**에만 적용한다.
    fallback = order[-1] if order else ''
    out = []
    for name in order:
        sec = CONFIG.get(name) or {}
        if not (sec.get('api_key') or '').strip():
            continue
        model = _flash_model(name, sec, override if name == fallback else '')
        if not model:
            continue
        is_free = bool(sec.get('free')) or name in FREE_PROVIDERS
        if FLASH_ONLY and not is_free and not _is_flash(model):
            continue
        out.append((name, sec['base_url'].rstrip('/'), sec['api_key'].strip(), model, is_free))
    return out


# 하드 가드: 어떤 경로로도 flash 가 아닌 유료 모델이 호출되면 즉시 중단.
# (config 오타로 pro/추론 모델이 나가 비용이 폭주하는 것을 원천 차단)
for _p in PURPOSE_ORDER:
    for _row in chain(_p):
        if not _row[4] and not _is_flash(_row[3]):
            raise SystemExit('[llm] flash 전용 정책 위반: purpose=%s provider=%s model=%s'
                             % (_p, _row[0], _row[3]))


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
    for name, base_url, api_key, model, is_free in providers:
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
                    # 추론 모델에서 빈 응답이 오는 케이스 → 재시도.
                    # 2026-10-05 실측: Gemini 는 "Say only: PONG" 처럼 지시가 짧으면
                    # 빈 문자열을 그대로 반환한다. 같은 요청을 반복해도 또 비니
                    # "최소 몇 단어로 답하라" 는 지시를 덧붙여 다시 물어본다.
                    last_err = 'empty content'
                    if not any('Answer with at least' in (m.get('content') or '')
                               for m in messages):
                        messages = list(messages) + [
                            {'role': 'user',
                             'content': 'Answer with at least five words of plain text.'}]
                    time.sleep(1)
                    continue
                return content, name
            except urllib.error.HTTPError as e:
                detail = e.read().decode('utf-8', 'ignore')[:200]
                last_err = 'HTTP %s %s' % (e.code, detail)
                log('  [llm] %s 실패: %s' % (name, last_err))
                # 429(요청 한도)·503(일시적 고부하) 는 **기다리면 다시 된다**.
                #   2026-10-05 실측: Gemini 무료 티어가 503 → 429 로 막히는데
                #   예전 코드는 이 둘을 즉시 폴백시켰다 → 무료 API 를 거의 못 쓰고
                #   유료 DeepSeek 이 대부분을 처리했다(로이: "무료 API도 이용해줘").
                #   → 무료 제공자는 짧은 백오프 후 같은 제공자를 한 번 더 시도한다.
                if e.code in (429, 503) and is_free:
                    wait = 5 if e.code == 429 else 3
                    log('  [llm] %s %s → %d초 대기 후 재시도(무료 티어)' % (name, e.code, wait))
                    time.sleep(wait)
                    if attempt <= retries:
                        continue
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
