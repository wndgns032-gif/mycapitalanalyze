# mycapitalanalyze.com 프로젝트 메모리

## 사이트 개요
- 정적 사이트. 소스=`main` 브랜치, 배포=`gh-pages` 브랜치 + Vercel(커스텀 도메인 www.mycapitalanalyze.com)
- 리포: `github.com/wndgns032-gif/mycapitalanalyze` (**public**)
- 13개 언어(영어는 루트 `/`, `/en/` 은 존재하지 않음 — 404가 정상) + `/game/` + `/ko/study/`
- 수익화: AdSense(pub-9243770518153989) + AliExpress 제휴 + GA4(G-1E3087HBL7)

## 파이프라인 (GitHub Actions 5개)
| 워크플로 | cron(UTC) |
|---|---|
| Game Daily | 03:13 |
| Auto Publish (경제) | 06:17 |
| Submit IndexNow | 07:15 |
| Study Daily (KO) | 12:05 |
| Deploy Pages | main push 시 |

- `scripts/publish.py` = crawler → rewrite → translate → 보정 → build → commit
  **soft fail 구조**: 단계가 실패해도 exit 0 → Actions는 success인데 산출물 0편이 될 수 있다.
- 하루 경제 발행 상한 `max_new_posts_per_run=2`, 품질 게이트 본문 1800자 미만 보류.

## 점검 방법론 (중요)
- **Actions 성공 ≠ 콘텐츠 생성.** 일자별 신규 파일 수로 판단:
  `git log --diff-filter=A --name-only --pretty=format:"%ad|" --date=short -- "content/posts/*.md"`
- Actions 실행 이력은 인증 없이 조회 가능하지만 **로그는 admin 권한 필요(403)**.
  → 실패 원인은 로컬 재현(`python scripts/rewrite.py` 계열)으로 좁힌다.
- 내부링크 무결성 전수검사 스크립트 패턴은 `_shared/diag_20261014.md` 점검 시 사용한
  링크체크 코드 참조(966 HTML / 34,171 링크 전수 검사).

## 함정
- 로컬(중국)은 **gemini가 네트워크로 차단**된다. CI(미국) 동작과 다름 — 로컬 결과를 그대로 신뢰 금지.
- `config.json` 은 .gitignore + 미추적. 절대 커밋 금지. 워크플로는 secrets에서 생성한다.
- 중국발 크롤러 소스(gamelook.com.cn 등)는 CI에서 연결 확인 단계가 별도로 있다.
- LLM 무료 제공자(gemini/groq/glm/openrouter/nvidia)는 자주 죽는다.
  폴백 체인 끝에 deepseek(유료)가 있어서 그것만으로 버티는 구조.

## 알려진 상시 리스크 (2026-10-14 기준)
- 무료 제공자 4곳 동시 사망 이력 있음 → 정기적으로 키 상태 점검 필요
  진단: `python -c` 로 각 제공자에 ping 호출(deepseek / glm / groq)
- 방문자 스냅샷(`scripts/visits_snapshot.py`)은 2026-09-30 이후 미갱신
