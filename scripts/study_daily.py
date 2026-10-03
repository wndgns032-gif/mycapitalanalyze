#!/usr/bin/env python3
"""KO 전용 학습 세트 생성기 (7급 공무원 워밍업).

하루 30분 구성 (로이 확정 2026-10-03):
    복습 5분  = 7일 전 세트 다시 보기
    복습 5분  = 어제 세트 다시 보기
    새 내용 20분 = 요일별 1과목 집중 (월·화 한국사 / 수·목 TOEIC / 금·토 PSAT / 일 휴식)

새 내용은 매일 3과목을 몰아넣지 않는다 — 20분에 3과목은 아무것도 남지 않는다.
요일당 1과목을 집중해 주 2회씩 만나고, 7일/1일 복습이 그 사이를 이어준다.

PSAT 은 "반복 출제 유형"을 유형 카드로 만들어 같은 유형을 돌려가며 외우게 한다.
계산 문항은 파이썬이 calc_expr 을 다시 계산해 calc_value 와 대조한다(오답 차단).

출력: content/study/ko/{YYYY-MM-DD}.json
사용법:
    python scripts/study_daily.py             # 오늘 세트 생성
    python scripts/study_daily.py --force     # 이미 있으면 덮어쓰기
    python scripts/study_daily.py --dry-run   # 저장 없이 미리보기
    python scripts/study_daily.py --date 2026-10-05
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import llm  # noqa: E402

STUDY_DIR = os.path.join(BASE, 'content', 'study', 'ko')
LANG = 'ko'

# 요일 → 그날의 새 내용 트랙 (0=월 ... 6=일)
WEEKDAY_TRACK = {0: 'history', 1: 'history', 2: 'toeic', 3: 'toeic',
                 4: 'psat', 5: 'psat', 6: 'rest'}

# ---------- 로테이션 소재 (한능검 심화 범위 / PSAT 자료해석 반복 유형) ----------
HISTORY_TOPICS = [
    '선사 시대와 고조선', '여러 나라의 성장', '삼국의 성립과 항쟁', '신라의 삼국 통일과 발해',
    '통일신라와 발해의 사회·경제', '고려의 건국과 정치 변동', '고려의 대외 관계(거란·여진·몽골)',
    '고려의 경제와 사회', '고려의 문화와 불교', '조선의 건국과 통치 체제 정비',
    '조선 전기의 정치 운영', '조선 전기의 경제·사회', '조선 전기의 문화와 과학',
    '조선 후기 정치 운영의 변화', '조선 후기 경제 변동(대동법·상평통보)',
    '조선 후기 사회 변동(신분제 동요)', '조선 후기 실학과 서민 문화',
    '개항 이전 서양과의 접촉', '흥선대원군의 개혁 정치', '개항과 불평등 조약',
    '임오군란과 갑신정변', '동학 농민 운동', '갑오개혁과 을미개혁', '독립협회와 대한제국',
    '일제의 국권 침탈 과정', '항일 의병과 애국계몽 운동', '1910년대 무단 통치와 저항',
    '3·1 운동과 대한민국 임시정부', '1920년대 항일 운동(의열단·봉오동·청산리)',
    '1930년대 이후 항일 운동', '일제 강점기 경제 수탈과 사회', '일제 강점기 문화와 교육',
    '8·15 광복과 분단', '미군정과 대한민국 정부 수립', '6·25 전쟁과 전후 복구',
    '이승만 정부와 4·19 혁명', '장면 정부와 5·16 군사 정변', '박정희 정부와 경제 개발',
    '유신 체제와 민주화 운동', '신군부와 5·18 민주화 운동', '6월 민주 항쟁',
    '문민정부 이후의 정치', '남북 관계의 변화', '근현대 경제 발전과 위기', '근현대 사회·문화 변화',
]

PSAT_TYPES = [
    '증가율 비교', '감소율 비교', '비중 변화 추적', 'A/B 배수 비교', '평균과 중앙값 함정',
    '가중평균 계산', '교차표 조건 추론', '증감 추이 판독', '최대·최소 항목 찾기',
    '구간별 합계 비교', '전체 대비 부분 비율', '연평균 증가율', '지수 변환 비교',
    '2단 비율 계산', '표와 그래프 종합', '증가량과 증가율 구분', '순위 변동 추적',
    '조건부 비율 계산', '여러 해 비교 최적 선택', '단위 환산 함정',
]

TOEIC_PART5_POINTS = [
    '주어-동사 수일치', '시제 일치', '가정법', '관계대명사', '분사구문', '전치사',
    '접속사와 전치사 구분', '부사의 위치', '비교급·최상급', '수동태', 'to부정사와 동명사', '대명사 일치',
]

TOEIC_PART7_TYPES = ['이메일', '사내 공지', '광고·전단', '기사문', '이중 지문(이메일+일정표)']

# ---------- 매일 최하단 TOEIC 단어 (정적 데이터 · LLM 호출 없음) ----------
# 로이 요청(2026-10-03): 트랙과 무관하게 '매일' 최하단에 단어를 붙인다.
# LLM에 맡기면 실패·비용이 생기므로 날짜 기반 결정적 로테이션으로 매일 확실히 나오게 한다.
TOEIC_VOCAB = [
    {"w": "accommodate", "pos": "동", "mean": "수용하다 · 숙박을 제공하다", "ex": "The hotel can accommodate 500 guests."},
    {"w": "adjacent", "pos": "형", "mean": "인접한", "ex": "The office is adjacent to the station."},
    {"w": "anticipate", "pos": "동", "mean": "예상하다", "ex": "We anticipate a rise in demand."},
    {"w": "authorize", "pos": "동", "mean": "승인하다 · 권한을 주다", "ex": "Only managers can authorize the payment."},
    {"w": "comply with", "pos": "동", "mean": "~을 준수하다", "ex": "All staff must comply with the policy."},
    {"w": "consolidate", "pos": "동", "mean": "통합하다", "ex": "We consolidated the two departments."},
    {"w": "courteous", "pos": "형", "mean": "정중한", "ex": "The staff were courteous and helpful."},
    {"w": "deadline", "pos": "명", "mean": "마감일", "ex": "The deadline is next Friday."},
    {"w": "defective", "pos": "형", "mean": "불량한", "ex": "Return any defective items within 30 days."},
    {"w": "dispatch", "pos": "동", "mean": "발송하다", "ex": "The order was dispatched yesterday."},
    {"w": "eligible", "pos": "형", "mean": "자격이 있는", "ex": "Only full-time staff are eligible."},
    {"w": "expedite", "pos": "동", "mean": "신속히 처리하다", "ex": "We expedited the shipment."},
    {"w": "fluctuate", "pos": "동", "mean": "변동하다", "ex": "Prices fluctuate seasonally."},
    {"w": "incentive", "pos": "명", "mean": "인센티브 · 유인", "ex": "Sales incentives boosted performance."},
    {"w": "inventory", "pos": "명", "mean": "재고", "ex": "The inventory is checked monthly."},
    {"w": "itinerary", "pos": "명", "mean": "여행 일정", "ex": "Please review your itinerary."},
    {"w": "liability", "pos": "명", "mean": "책임 · 부채", "ex": "The company accepted no liability."},
    {"w": "mandatory", "pos": "형", "mean": "의무적인", "ex": "Attendance is mandatory."},
    {"w": "notify", "pos": "동", "mean": "통지하다", "ex": "Please notify us of any changes."},
    {"w": "obsolete", "pos": "형", "mean": "구식의", "ex": "The software is now obsolete."},
    {"w": "outstanding", "pos": "형", "mean": "미결제의 · 뛰어난", "ex": "Two invoices are still outstanding."},
    {"w": "oversee", "pos": "동", "mean": "감독하다", "ex": "She oversees the project."},
    {"w": "prospective", "pos": "형", "mean": "잠재적인 · 예상되는", "ex": "We met a prospective client."},
    {"w": "reimburse", "pos": "동", "mean": "상환하다", "ex": "Travel costs will be reimbursed."},
    {"w": "remittance", "pos": "명", "mean": "송금", "ex": "Remittance must arrive by the 15th."},
    {"w": "renovation", "pos": "명", "mean": "개보수", "ex": "The renovation takes six weeks."},
    {"w": "rigorous", "pos": "형", "mean": "엄격한", "ex": "Testing is rigorous."},
    {"w": "shipment", "pos": "명", "mean": "선적 · 출하", "ex": "The shipment arrived late."},
    {"w": "subsidiary", "pos": "명", "mean": "자회사", "ex": "It is a subsidiary of the group."},
    {"w": "terminate", "pos": "동", "mean": "종료하다", "ex": "The contract was terminated."},
    {"w": "tentative", "pos": "형", "mean": "잠정적인 · 임시의", "ex": "We set a tentative date."},
    {"w": "vacancy", "pos": "명", "mean": "공석 · 빈자리", "ex": "There is one vacancy left."},
    {"w": "validate", "pos": "동", "mean": "확인하다 · 유효하게 하다", "ex": "Validate your ticket online."},
    {"w": "viable", "pos": "형", "mean": "실행 가능한", "ex": "A viable alternative."},
    {"w": "waive", "pos": "동", "mean": "면제하다 · 포기하다", "ex": "The fee was waived."},
    {"w": "warranty", "pos": "명", "mean": "보증", "ex": "The warranty lasts two years."},
    {"w": "appraise", "pos": "동", "mean": "평가하다 · 감정하다", "ex": "The property was appraised."},
    {"w": "benchmark", "pos": "명", "mean": "기준점", "ex": "Set a benchmark for quality."},
    {"w": "turnover", "pos": "명", "mean": "매출액 · 이직률", "ex": "Annual turnover rose 12 percent."},
    {"w": "concise", "pos": "형", "mean": "간결한", "ex": "Keep the report concise."},
]

# 자주 쓰는 표현 중 헷갈리는 쌍 (비슷한 표현 vs 차이)
TOEIC_CONFUSABLES = [
    {"a": "in time", "b": "on time", "diff": "in time = 제때(시간 내에) / on time = 정각에(약속 시각에 딱 맞춰)"},
    {"a": "assure", "b": "ensure", "diff": "assure = 사람에게 확신시키다(assure A that) / ensure = 일이 되도록 보장하다(ensure that)"},
    {"a": "rise", "b": "raise", "diff": "rise = 자동사, 스스로 오르다(가격이 오른다) / raise = 타동사, 올리다(가격을 올리다)"},
    {"a": "affect", "b": "effect", "diff": "affect = 동사 '영향을 미치다' / effect = 명사 '영향'"},
    {"a": "principal", "b": "principle", "diff": "principal = 형용사 '주요한'·명사 '교장' / principle = 명사 '원칙'"},
    {"a": "farther", "b": "further", "diff": "farther = 물리적 거리(더 멀리) / further = 추가의·더 나아가(추상)"},
    {"a": "lend", "b": "borrow", "diff": "lend = 빌려주다(lend A to B) / borrow = 빌리다(borrow A from B)"},
    {"a": "beside", "b": "besides", "diff": "beside = ~옆에(위치) / besides = ~게다가(추가)"},
    {"a": "economic", "b": "economical", "diff": "economic = 경제의(economy) / economical = 경제적인·절약하는"},
    {"a": "stationary", "b": "stationery", "diff": "stationary = 정지한(움직이지 않는) / stationery = 문구류"},
    {"a": "adapt", "b": "adopt", "diff": "adapt = 적응하다·개조하다 / adopt = 채택하다·입양하다"},
    {"a": "imminent", "b": "eminent", "diff": "imminent = 임박한(곧 일어날) / eminent = 저명한(유명한)"},
    {"a": "loose", "b": "lose", "diff": "loose = 헐렁한(형용사) / lose = 잃다(동사)"},
    {"a": "complement", "b": "compliment", "diff": "complement = 보완하다 / compliment = 칭찬하다"},
    {"a": "advice", "b": "advise", "diff": "advice = 명사 '조언' / advise = 동사 '조언하다'"},
    {"a": "personal", "b": "personnel", "diff": "personal = 개인의 / personnel = 직원(인사)"},
]

# 빈출문제 쉽게 외우는 방법 (1일 1줄)
TOEIC_TIPS = [
    "단어는 문장째로 외우기 — 뒤 명사까지 묶어 외우면 Part 7에서 그대로 보입니다.",
    "Part 5는 품사 문제가 절반 — 빈칸 앞뒤 품사만 확인하면 3초컷입니다.",
    "접속사 vs 전치사: 빈칸 뒤에 '주어+동사'가 오면 접속사(Since), 명사만 오면 전치사(Because of)입니다.",
    "수일치: 주어와 동사 사이 전치사구는 무시하고 핵심 주어만 찾으세요.",
    "Part 7은 문서 종류(이메일·공지·광고)를 먼저 파악하면 '목적' 문제가 바로 풀립니다.",
    "정답은 동의어 치환 — 지문 단어와 같은 뜻의 다른 표현이 정답입니다.",
    "부정어(not·never·without)에 밑줄 — Part 7에서 부정 표현이 정답의 힌트입니다.",
    "분사구문은 주절 주어를 확인 — 행위자면 -ing, 대상이면 -ed입니다.",
    "시제는 시간 부사어를 먼저 — yesterday·since·by the time이 답을 정합니다.",
    "관계대명사: 빈칸 뒤가 불완전하면 which·that, 완전하면 where·when·why입니다.",
    "오답은 '지문에 없는 정보' — 그럴듯해도 지문 근거가 없으면 오답입니다.",
    "하루 3개씩만 — 한 달이면 90개. 양보다 매일이 훨씬 중요합니다.",
]

TRACK_TITLE = {'history': '한국사(심화)', 'toeic': 'TOEIC', 'psat': 'PSAT 자료해석', 'rest': '주간 복습'}


# ---------- 공통 유틸 ----------
def load_all():
    """저장된 학습 세트 전체를 날짜순으로 반환."""
    out = []
    if not os.path.isdir(STUDY_DIR):
        return out
    for fn in sorted(os.listdir(STUDY_DIR)):
        if not fn.endswith('.json'):
            continue
        try:
            out.append(json.load(open(os.path.join(STUDY_DIR, fn), encoding='utf-8')))
        except Exception:
            print(f'  ! 손상된 학습 JSON 건너뜀: {fn}')
    out.sort(key=lambda x: x.get('date', ''))
    return out


def glob_files():
    if not os.path.isdir(STUDY_DIR):
        return []
    return [os.path.join(STUDY_DIR, fn) for fn in sorted(os.listdir(STUDY_DIR))
            if fn.endswith('.json')]


def json_from(text):
    """LLM 응답에서 첫 번째 JSON 객체를 꺼낸다 (코드펜스 허용)."""
    t = text.strip()
    t = re.sub(r'^```(?:json)?', '', t).strip()
    t = re.sub(r'```$', '', t).strip()
    i, j = t.find('{'), t.rfind('}')
    if i < 0 or j <= i:
        raise ValueError('JSON 없음')
    return json.loads(t[i:j + 1])


def ask(system, user, purpose='write'):
    content, provider = llm.chat(
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        max_tokens=6000, temperature=0.7, purpose=purpose)
    print(f'    · {provider} 응답 {len(content)}자')
    return json_from(content)


SAFE_RE = re.compile(r'^[0-9+\-*/().\s]+$')
# 선택지 앞머리의 표기(①, A), 1.) 를 벗겨낸다 → 렌더러가 ①②③④⑤ 를 직접 붙여
# "정답 ②" 와 목록 표기가 항상 일치하게 만든다.
MARK_RE = re.compile(r'^\s*(?:[①②③④⑤⑥⑦⑧⑨⑩]|[A-Ea-e][).、.,]|[0-9][).])\s*')


def clean_choices(q):
    ch = [MARK_RE.sub('', str(c)).strip() for c in (q.get('choices') or [])]
    if ch:
        q['choices'] = ch
    return q


def balance_answers(qs, seed):
    """정답 위치가 한쪽에 몰리면 선택지 순서만 결정적으로 섞는다(내용 불변)."""
    if len(qs) < 4:
        return qs
    cnt = Counter(q.get('answer') for q in qs if isinstance(q.get('answer'), int))
    if not cnt or cnt.most_common(1)[0][1] < 3:
        return qs
    rnd = random.Random(seed)
    for q in qs:
        ch = q.get('choices') or []
        a = q.get('answer')
        if not isinstance(a, int) or not (0 <= a < len(ch)):
            continue
        idx = list(range(len(ch)))
        rnd.shuffle(idx)
        q['choices'] = [ch[i] for i in idx]
        q['answer'] = idx.index(a)
    print('    · 정답 편중 → 선택지 순서 재배치')
    return qs


def calc_ok(expr, claimed):
    """calc_expr 을 파이썬으로 다시 계산해 calc_value 와 대조."""
    if not expr or not claimed:
        return False, 'calc 없음'
    if not SAFE_RE.match(str(expr)):
        return False, '허용되지 않은 수식'
    try:
        val = eval(str(expr), {'__builtins__': {}}, {})  # 숫자·사칙연산만 통과
    except Exception:
        return False, '계산 실패'
    try:
        clm = float(str(claimed).replace(',', '').replace('%', '').strip())
    except Exception:
        return False, '값 파싱 실패'
    ok = abs(val - clm) <= max(0.02, abs(clm) * 0.02)
    return ok, f'{expr} = {val:.4g} (주장 {clm})'


def _choice_num(s):
    """선택지 텍스트에서 첫 숫자를 꺼낸다('25%' → 25.0, '약 30명' → 30.0)."""
    m = re.findall(r'-?\d+(?:\.\d+)?', str(s).replace(',', ''))
    return float(m[0]) if m else None


def fix_psat_answer(p):
    """calc_value 와 일치하는 선택지를 정답으로 맞춘다(모델이 정답 위치를 잘못 찍은 경우 보정).
    일치하는 선택지가 아예 없으면 False(폐기)."""
    cv = _choice_num(p.get('calc_value'))
    if cv is None:
        return False
    ch = p.get('choices') or []
    a = p.get('answer')
    # calc_value 가 비율(0.xx)인데 선택지가 퍼센트(xx)인 경우를 커버: cv·cv*100·cv/100 모두 대조
    cands = [cv]
    if cv != 0:
        cands.append(cv * 100)
        cands.append(cv / 100)
    tol = max(0.02, abs(cv) * 0.02)
    if isinstance(a, int) and 0 <= a < len(ch):
        n = _choice_num(ch[a])
        for cc in cands:
            if n is not None and abs(n - cc) <= max(0.02, abs(cc) * 0.02):
                return True
    # 정답이 calc 와 안 맞으면 → calc 에 맞는 선택지를 찾아 보정
    for i, c in enumerate(ch):
        n = _choice_num(c)
        for cc in cands:
            if n is not None and abs(n - cc) <= max(0.02, abs(cc) * 0.02):
                print(f'    · PSAT 정답 보정: {a} → {i} (calc={cv}, 선택지={c})')
                p['answer'] = i
                return True
    return False


def gen_vocab(d):
    """매일 최하단 TOEIC 단어 — 날짜 기반 결정적 로테이션(LLM 호출 없음).

    트랙(한국사·TOEIC·PSAT·휴식)과 무관하게 매일 붙는다.
    salt*3 간격으로 3개를 고르므로 하루가 지나면 겹치지 않고 다음 3개로 넘어간다.
    """
    salt = d.toordinal()
    nv = len(TOEIC_VOCAB)
    words = [TOEIC_VOCAB[(salt * 3 + i) % nv] for i in range(3)]
    return {
        'words': words,
        'confusable': TOEIC_CONFUSABLES[salt % len(TOEIC_CONFUSABLES)],
        'tip': TOEIC_TIPS[salt % len(TOEIC_TIPS)],
    }


# ---------- 트랙별 생성 + 검증 ----------
def gen_history(topic):
    system = ('당신은 한국사능력검정시험 심화(1~3급) 대비 학습 콘텐츠를 만드는 출제자입니다. '
              '실제 기출문제를 그대로 옮기지 말고 같은 주제·같은 유형·같은 난이도로 새 문항을 만드세요. '
              '반드시 JSON 하나만 출력합니다.')
    user = f'''주제: {topic}
한국사능력검정시험 **심화** 기준으로 아래 JSON을 만드세요.
{{
 "track": "history",
 "topic": "{topic}",
 "concept": {{"title": "개념 제목", "points": ["핵심 포인트 5~6개, 각 40자 이내"], "mnemonic": "한 줄 암기 문장"}},
 "questions": [{{"q": "발문", "choices": ["①...", "②...", "③...", "④...", "⑤..."],
                "answer": 0, "explain": "해설 3~4문장", "trap": "헷갈리는 포인트 한 줄"}}]
}}
규칙:
- questions 는 정확히 5문항, 5지선다, answer 는 0~4 정수.
- 연도·인물·제도는 사실과 일치해야 합니다. 확실하지 않으면 다른 소재로 바꾸세요.
- 난이도는 심화 70점(2급) 목표 수준. 단순 연도 암기보다 사료·비교·인과형을 섞으세요.
'''
    data = ask(system, user)
    qs = data.get('questions') or []
    good = []
    for q in qs:
        ch = q.get('choices') or []
        a = q.get('answer')
        if len(ch) < 4 or not isinstance(a, int) or not (0 <= a < len(ch)):
            print(f'    ✗ 문항 구조 불량 → 제외: {str(q.get("q"))[:40]}')
            continue
        if len(str(q.get('explain', ''))) < 20:
            print('    ✗ 해설 부족 → 제외')
            continue
        good.append(q)
    data['questions'] = good[:5]
    if len(good) < 3:
        raise ValueError(f'한국사 문항 부족({len(good)}개)')
    return data


def gen_toeic(p5_point, p7_type):
    system = ('You are an experienced TOEIC question writer. Write original questions in the exact '
              'TOEIC format (Part 5 and Part 7). Output a single JSON object only.')
    user = f'''Focus point for Part 5: {p5_point}
Passage type for Part 7: {p7_type}

{{
 "track": "toeic",
 "part5": [{{"q": "sentence with a blank ____", "choices": ["A) ...", "B) ...", "C) ...", "D) ..."],
            "answer": 0, "explain": "한국어 해설 2~3문장", "point": "{p5_point}"}}],
 "part7": {{"passage": {{"title": "제목", "body": "지문 120~180 words, 실제 TOEIC 스타일"}},
            "questions": [{{"q": "질문", "choices": ["A) ...", "B) ...", "C) ...", "D) ..."],
                           "answer": 0, "explain": "한국어 해설 2~3문장",
                           "evidence": "지문에 실제로 있는 문장을 그대로 복사"}}]}}
}}
Rules:
- part5: exactly 5 questions, 4 choices each.
- part7: exactly 1 passage + exactly 4 questions.
- `evidence` MUST be copied verbatim from the passage (it is string-matched for validation).
- Explanations in Korean, questions/passage in English.
'''
    data = ask(system, user)
    p5 = []
    for q in (data.get('part5') or []):
        ch, a = q.get('choices') or [], q.get('answer')
        if len(ch) == 4 and isinstance(a, int) and 0 <= a < 4:
            p5.append(q)
    data['part5'] = p5[:5]
    p7 = data.get('part7') or {}
    passage = str((p7.get('passage') or {}).get('body', ''))
    kept = []
    for q in (p7.get('questions') or []):
        ch, a = q.get('choices') or [], q.get('answer')
        ev = str(q.get('evidence', '')).strip()
        if len(ch) != 4 or not isinstance(a, int) or not (0 <= a < 4):
            print('    ✗ Part7 문항 구조 불량 → 제외')
            continue
        if not ev or ev not in passage:
            print(f'    ✗ 근거 문장이 지문에 없음 → 제외: {ev[:40]}')
            continue
        kept.append(q)
    if kept:
        p7['questions'] = kept
        data['part7'] = p7
    else:
        data.pop('part7', None)
    if len(p5) < 3:
        raise ValueError(f'Part5 문항 부족({len(p5)}개)')
    return data


def gen_psat(ptype):
    system = ('당신은 PSAT(공직적격성평가) 자료해석 영역 출제자입니다. '
              '표의 숫자로 계산이 정확히 맞아떨어지는 문항만 만듭니다. JSON 하나만 출력하세요.')
    user = f'''유형: {ptype}
아래 JSON을 만드세요.
{{
 "track": "psat",
 "type_card": {{"name": "{ptype}", "steps": ["풀이 1단계", "2단계", "3단계"],
                "pitfall": "함정 한 줄", "shortcut": "암기용 요약 공식"}},
 "problems": [{{"title": "표 제목",
   "table": {{"headers": ["구분", "2024", "2025"], "rows": [["수출액(억 달러)", "120", "150"], ["수입액(억 달러)", "80", "90"]]}},
   "q": "발문", "choices": ["20%", "25%", "30%", "35%", "40%"], "answer": 1,
   "solution": "풀이 3~4문장",
   "calc_expr": "150/120-1", "calc_value": "0.25"}}]
}}
규칙:
- problems 는 정확히 2문항, 5지선다, answer 는 0~4 정수.
- calc_expr 은 **표에 있는 숫자만** 쓴 사칙연산 식(문자·%·단위 금지). calc_value 는 그 계산 결과를 소수로.
  파이썬이 다시 계산해 대조하므로 반드시 일치해야 합니다.
- 표 숫자는 계산이 깔끔하게 떨어지도록 만드세요(예: 120→150).
- 정답이 "계산 결과"인 문항으로 만드세요(주관식 서술 금지).
- 정답 선택지에 표시된 숫자가 calc_value 와 같아야 합니다(예: calc_value=0.25 → "25%" 또는 "0.25"인 선택지가 정답).
- table.rows 는 2~3개 행으로 만드세요(1행만 있으면 계산이 단조로워집니다).
- 두 문항은 서로 다른 소재와 숫자를 사용하세요(중복 금지).
- choices 의 각 항목 앞에 ①②③④⑤ 기호를 붙이지 마세요(렌더러가 부착합니다).
'''
    data = ask(system, user)
    kept = []
    for p in (data.get('problems') or []):
        ch, a = p.get('choices') or [], p.get('answer')
        if len(ch) < 4 or not isinstance(a, int) or not (0 <= a < len(ch)):
            print('    ✗ 자료해석 문항 구조 불량 → 제외')
            continue
        ok, why = calc_ok(p.get('calc_expr'), p.get('calc_value'))
        if not ok:
            print(f'    ✗ 계산 검증 실패 → 제외 ({why})')
            continue
        if not fix_psat_answer(p):
            print('    ✗ 정답-계산 불일치(보정 불가) → 제외')
            continue
        print(f'    ✓ 계산·정답 검증 통과: {why}')
        kept.append(p)
    data['problems'] = kept[:2]
    if not data.get('type_card') and not kept:
        raise ValueError('자료해석 결과 없음')
    return data


# ---------- 메인 ----------
def pick(seq, used, salt):
    """아직 안 쓴 소재를 순서대로 고르되, 다 쓰면 처음부터 순환."""
    for i in range(len(seq)):
        cand = seq[(salt + i) % len(seq)]
        if cand not in used:
            return cand
    return seq[salt % len(seq)]


def normalize_file(path):
    """이미 생성된 세트에 표기 정리 + 정답 편중 완화 + TOEIC 단어 백필(LLM 재호출 없음)."""
    it = json.load(open(path, encoding='utf-8'))
    d = datetime.date.fromisoformat(it['date'])
    salt = d.toordinal()
    if not it.get('vocab'):
        it['vocab'] = gen_vocab(d)
    all_q = ((it.get('questions') or []) + (it.get('part5') or [])
             + ((it.get('part7') or {}).get('questions') or []) + (it.get('problems') or []))
    for q in all_q:
        clean_choices(q)
    balance_answers(it.get('questions') or [], salt)
    balance_answers(it.get('part5') or [], salt + 1)
    balance_answers((it.get('part7') or {}).get('questions') or [], salt + 2)
    balance_answers(it.get('problems') or [], salt + 3)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(it, f, ensure_ascii=False, indent=2)
    return it


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.date.today().isoformat())
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--normalize', action='store_true',
                    help='기존 세트 전체에 표기 정리/정답 편중 완화만 적용')
    args = ap.parse_args()

    if args.normalize:
        n = 0
        for path in sorted(glob_files()):
            it = normalize_file(path)
            print(f'  ✓ 정리: {it["date"]} Day {it["day"]}')
            n += 1
        print(f'[study] {n}개 세트 정리 완료')
        return 0

    d = datetime.date.fromisoformat(args.date)
    path = os.path.join(STUDY_DIR, f'{d.isoformat()}.json')
    if os.path.exists(path) and not args.force:
        print(f'이미 생성됨: {path} (덮어쓰려면 --force)')
        return 0

    existing = load_all()
    day_no = 1 + sum(1 for x in existing if x.get('date', '') < d.isoformat())
    track = WEEKDAY_TRACK[d.weekday()]
    used_topics = {x.get('topic') for x in existing if x.get('topic')}
    used_psat = {x.get('psat_type') for x in existing if x.get('psat_type')}
    salt = d.toordinal()

    print(f'[study] {d.isoformat()} · Day {day_no} · 트랙={track} ({TRACK_TITLE[track]})')

    item = {'day': day_no, 'date': d.isoformat(), 'track': track,
            'track_title': TRACK_TITLE[track]}

    if track == 'rest':
        item['rest_note'] = '새 내용 없이 이번 주 세트만 다시 봅니다. 5문항 이상 틀렸다면 그 과목을 한 번 더 복습하세요.'
    elif track == 'history':
        topic = pick(HISTORY_TOPICS, used_topics, salt)
        print(f'  · 한국사 주제: {topic}')
        for attempt in (1, 2):
            try:
                item.update(gen_history(topic))
                break
            except Exception as e:
                print(f'  ! 한국사 시도 {attempt} 실패: {e}')
                if attempt == 2:
                    raise
        item['topic'] = topic
    elif track == 'toeic':
        p5 = pick(TOEIC_PART5_POINTS, set(), salt)
        p7 = pick(TOEIC_PART7_TYPES, set(), salt)
        print(f'  · TOEIC: Part5={p5} / Part7={p7}')
        for attempt in (1, 2):
            try:
                item.update(gen_toeic(p5, p7))
                break
            except Exception as e:
                print(f'  ! TOEIC 시도 {attempt} 실패: {e}')
                if attempt == 2:
                    raise
        item['part5_point'] = p5
        item['part7_type'] = p7
    else:  # psat
        ptype = pick(PSAT_TYPES, used_psat, salt)
        print(f'  · PSAT 유형: {ptype}')
        for attempt in (1, 2):
            try:
                item.update(gen_psat(ptype))
                break
            except Exception as e:
                print(f'  ! PSAT 시도 {attempt} 실패: {e}')
                if attempt == 2:
                    raise
        item['psat_type'] = ptype

    # 매일 최하단 TOEIC 단어 (트랙 무관 · LLM 호출 없음)
    item['vocab'] = gen_vocab(d)

    if args.dry_run:
        print(json.dumps(item, ensure_ascii=False, indent=2)[:2000])
        return 0

    # 선택지 표기 정리 + 정답 위치 편중 완화 (모든 트랙 공통)
    all_q = ((item.get('questions') or []) + (item.get('part5') or [])
             + ((item.get('part7') or {}).get('questions') or []) + (item.get('problems') or []))
    for q in all_q:
        clean_choices(q)
    balance_answers(item.get('questions') or [], salt)
    balance_answers(item.get('part5') or [], salt + 1)
    balance_answers((item.get('part7') or {}).get('questions') or [], salt + 2)
    balance_answers(item.get('problems') or [], salt + 3)

    os.makedirs(STUDY_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(item, f, ensure_ascii=False, indent=2)
    print(f'  → 저장: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
