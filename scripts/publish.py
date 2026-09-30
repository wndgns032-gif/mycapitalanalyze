#!/usr/bin/env python3
"""
자동 게시 오케스트레이터 — 크롤링 → 재가공 → 번역 → 글자수 보정 → 빌드 → 커밋까지 한 번에.
(push는 GitHub Actions에서 PAT 자격 증명으로 수행)

사용법: python scripts/publish.py
"""
import subprocess, sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run(script, args=None, soft=False):
    """soft=True 면 실패해도 파이프라인을 중단하지 않는다 (SEO 제출 등 부가 단계)."""
    print(f'\n===== {script} =====')
    cmd = [sys.executable, os.path.join(BASE, 'scripts', script)]
    if args:
        cmd += args
    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        msg = f'!! {script} 실패 (exit {r.returncode})'
        if soft:
            print(msg + ' → 계속 진행')
            return
        print(msg)
        sys.exit(1)


def main():
    # 1. 크롤링
    run('crawler.py')
    # 2. 재가공 (원문 -> 영어 글)
    run('rewrite.py')
    # 3. 13개 언어 번역
    run('translate.py')
    # 4. 글자수 보정
    run('fix_length.py')
    # 5. 앱·게임 레이더 — 모든 언어에 하루 1건씩 (언어별 네이티브 수집, 번역 안 함)
    run('app_radar.py')
    # 6. HTML 빌드 (레이더 글까지 포함해야 하므로 레이더 다음에 둔다)
    run('build.py')
    # 7. 새 URL 색인 요청 (실패해도 배포에는 지장 없음)
    run('submit_index.py', soft=True)

    # 8. 커밋 (변경사항 있을 때만)
    subprocess.run(['git', 'add', '-A'], cwd=BASE)
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=BASE)
    if diff.returncode != 0:
        msg = subprocess.run(
            ['git', 'log', '-1', '--format=%cd', '--date=format:%Y%m%d-%H%M'],
            capture_output=True, text=True, cwd=BASE).stdout.strip()
        subprocess.run(['git', 'commit', '-m', 'auto: content update'], cwd=BASE)
        print('커밋 완료')
    else:
        print('변경사항 없음 (커밋 스킵)')

    print('\n전체 파이프라인 완료')

if __name__ == '__main__':
    main()
