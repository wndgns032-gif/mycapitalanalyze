# MyCapital Analyze — 운영 매뉴얼

로컬 저장소: `D:\workbuddy\blog\mycapitalanalyze.com`
GitHub: https://github.com/wndgns032-gif/mycapitalanalyze

## 1. 파이프라인 구조

```
config_sources.json (RSS 소스)
        │
        ▼
scripts/crawler.py   → content/raw/{slug}.json      원문 수집 (제목/본문/발행일/출처 URL)
        ▼
scripts/rewrite.py   → content/posts/{slug}.md      영어 원고 재창작 + SEO 슬러그 + FAQ
        ▼
scripts/translate.py → content/translations/{lang}/{slug}.json  12개 언어 번역
        ▼
scripts/fix_length.py                               글자수 보정
        ▼
scripts/build.py     → index.html / post/*.html / {lang}/** / sitemap.xml / feed.xml
```

한 번에 실행: `python scripts/publish.py` (크롤→재가공→번역→보정→빌드→커밋)

## 2. 모델 제공자

`config.json`의 `provider` 값으로切换.

| provider | 모델 | 비고 |
|---|---|---|
| `glm` (현재) | `glm-4-flash` | https://open.bigmodel.cn — 무료/저가. 번역 품질은 준수 |
| `deepseek` | `deepseek-v4-flash` | 품질 더 좋음, 유료 |

## 3. 발행량 정책 (중요 — 구글 스케일드 콘텐츠 어뷰즈 방지)

- `config.json`의 `max_new_posts_per_run` = **3**. 하루 최대 3편.
- GitHub Actions cron은 **하루 1회** (`17 6 * * *` = 한국 15:17).
- 30분 주기로 돌리지 말 것. 신규 도메인이 하루 수십~수백 URL을 쏟아내면
  사이트 전체 품질 신호가 떨어지고, 개별 글을 고쳐도 복구되지 않는다.

## 4. GitHub Actions 설정 (Roy가 직접 해야 하는 부분)

저장소 → Settings → Secrets and variables → Actions → New repository secret

| Secret 이름 | 값 |
|---|---|
| `GLM_API_KEY` | `***REMOVED***` |
| `GH_PAT` | repo 권한이 있는 Personal Access Token (Actions가 커밋을 푸시해야 함) |

`DEEPSEEK_API_KEY`는 선택(provider를 deepseek으로 바꿀 때만).

## 5. SEO 관련 적용된 사항 (build.py)

- 페이지별 **hreflang 상호 링크** + `x-default` (존재하는 언어만)
- **NewsArticle** JSON-LD (출처 `isBasedOn` 포함 → 재가공 콘텐츠의 저작권/신뢰성 방어)
- **BreadcrumbList**, 본문에 `## FAQ`가 있으면 **FAQPage** 스키마 자동 생성
- 본문 상단에 원문 출처 링크 표기
- 관련 글 3개 내부 링크
- sitemap.xml: 실제 존재하는 (언어 × 글) 조합만 수록 → 404 유도 URL 제거, `lastmod` 포함
- 홈 페이지 언어별 고유 title/description/H1

## 6. 알려진 미해결 사항

1. **라이브 도메인 불일치** — `https://mycapitalanalyze.com` 은 현재
   `/{lang}/post/news-YYYYMMDD-hash` 구조의 **별도 Next.js 앱**(8개 언어)이 서비스 중이다.
   이 저장소의 정적 사이트와는 다른 애플리케이션이다.
   도메인을 이 정적 사이트로 붙이려면 Vercel 프로젝트 연결이 필요하고,
   기존 Next.js 앱을 유지하려면 그쪽 소스 저장소/토큰이 필요하다.
2. **Vercel 토큰** — 로컬에 없음. 배포 자동화하려면 필요.
3. **GitHub 커밋 푸시 권한** — 로컬 git에 자격 증명이 없으면 `GH_PAT` 필요.

## 7. 콘텐츠 소스 (config_sources.json)

FRED Blog(세인트루이스 연은), 연준 보도자료, BLS, IMF, World Bank 등
공공·연구기관 RSS. 저작권 안전(요약+독립 분석)하고 에버그린 성격이 강한 소스 위주.
