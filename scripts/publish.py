#!/usr/bin/env python3
"""
자동 게시 오케스트레이터 — 크롤링 → 재가공 → 번역 → 글자수 보정 → 앱레이더 → 빌드 → 색인 → 커밋.

무인 운영(클라우드) 전제: 어느 한 단계가 실패해도
- 성공한 단계의 산출물은 커밋해 다음 실행에서 이어받게 하고,
- 파이프라인 전체를 죽이지 않는다(2026-09-30 어제 실행이 한 단계 실패로 전부 롤백된 사고 대응).
단, build.py 가 실패했을 때 반쯤 쓰인 HTML 이 배포되는 것은 막는다(산출물 되돌리고 md 만 커밋).

사용법: python scripts/publish.py
"""
import subprocess, sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def run(script, args=None, soft=True):
    """단계 실행. 기본 soft=True — 실패를 기록하고 계속 진행한다. returncode 반환."""
    print(f'\n===== {script} =====')
    cmd = [sys.executable, os.path.join(BASE, 'scripts', script)]
    if args:
        cmd += args
    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        FAILED.append(script)
        print(f'!! {script} 실패 (exit {r.returncode}) → 계속 진행')
    return r.returncode


def revert_build_outputs():
    """build 가 도중에 죽었을 때 반쯤 나온 HTML/JSON 을 되돌린다.

    content/ 와 assets/ (원본 소스)는 유지 — 새 md 는 다음 빌드에서 살아난다.
    그 외 커밋 대상 산출물(HTML, sitemap, data/ 등)은 수정분은 되돌리고
    신규 파일은 지운다.
    """
    st = subprocess.run(['git', 'status', '--porcelain'],
                        capture_output=True, text=True, cwd=BASE).stdout
    for line in st.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip('"')
        if path.startswith(('content/', 'assets/')):
            continue
        if code.strip() == '??':
            try:
                os.remove(os.path.join(BASE, path))
            except OSError:
                pass
        else:
            subprocess.run(['git', 'checkout', '--', path], cwd=BASE)
    print('!! build 산출물 되돌림 (반쪽짜리 HTML 배포 방지)')


def main():
    # 1~5. 콘텐츠 수집·생성 단계 — 각각 실패해도 나머지는 산다.
    run('crawler.py')
    run('rewrite.py')
    run('translate.py')
    run('fix_length.py')
    run('app_radar.py')

    # 6. HTML 빌드 (레이더 글까지 포함해야 하므로 레이더 다음에 둔다)
    if run('build.py') != 0:
        revert_build_outputs()

    # 7. 새 URL 색인 요청
    run('submit_index.py')

    # 8. 커밋 (변경사항 있을 때만 — 성공한 단계의 산출물이라도 살린다)
    subprocess.run(['git', 'add', '-A'], cwd=BASE)
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=BASE)
    if diff.returncode != 0:
        note = (' (실패 단계: ' + ', '.join(FAILED) + ')') if FAILED else ''
        subprocess.run(['git', 'commit', '-m', 'auto: content update' + note], cwd=BASE)
        print('커밋 완료' + note)
    else:
        print('변경사항 없음 (커밋 스킵)')

    if FAILED:
        print('\n!! 실패한 단계: ' + ', '.join(FAILED) + ' — 나머지는 정상 처리됨')
    print('\n전체 파이프라인 완료')


if __name__ == '__main__':
    main()
