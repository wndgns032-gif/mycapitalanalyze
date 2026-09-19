# 일일 발행 + 상태 점검 자동화 실행 기록

## 2026-09-19 (첫 실행)
- 결과: 전 단계 성공. 신규 1편 발행 (bank-of-england-taskforce-begins-work-to-harmonise, en + ko/zh/ja/es 4개 번역)
- 커밋: e53030f (발행) + 77e0472 (indexnow 상태) → 원격 06c321f
- Vercel 배포: dpl_2hw9cvfVmab28dcDRPeBXjZ4Jyy7
- sitemap 142 URL, IndexNow 신규 5개 제출(200), 라이브 점검 전항목 통과(aff_id 있음, adsbygoogle 0, GA4 있음, 키파일 200)

## 알아둘 것 (재발 패턴)
1. gitpush_api.py 가 원격과 분기(merge-base 없음)일 때: git fetch → git merge <remote-sha> -X ours --allow-unrelated-histories 후 재실행하면 됨. 이번엔 이 절차로 해결.
2. gitpush_api.py 는 푸시 성공 후 마지막 로컬 정렬 단계(update-ref)에서 항상 실패함 — 원격에서 새로 만들어진 커밋 SHA가 로컬에 없기 때문. "DONE remote main -> <sha>" 로그가 보이면 푸시는 성공한 것. 이후 `git fetch origin main && git reset --keep <sha>` 로 수동 정렬 필요. (exit code 1이어도 푸시 자체는 성공)
3. 푸시 중 blob 업로드 HTTP 400/422 간헐 발생 — 재실행하면 통과됨(멱등).
4. Git Bash 에서 관리형 python 은 반드시 "C:/Users/..." 슬래시 경로로 호출 (백슬래시 bare 경로는 command not found).
5. curl -o 는 Git Bash 에서 /dev/null 대신 nul 사용, 여러 -w 출력을 && 로 이으면 exit 23 발생 → for 루프로 1건씩.
6. translate.py 는 약 6~7분 소요, 초반에 빈 응답 재시도(JSON 파싱 실패)가 몇 번 나와도 최종 성공하면 정상.
