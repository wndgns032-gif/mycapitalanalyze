#!/usr/bin/env python3
"""
자동 게시 오케스트레이터 — 크롤링 → 재가공 → 번역 → 글자수 보정 → 빌드 → 커밋까지 한 번에.
(push는 GitHub Actions에서 PAT 자격 증명으로 수행)

사용법: python scripts/publish.py
"""
import subprocess, sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run(script):
    print(f'\n===== {script} =====')
    r = subprocess.run([sys.executable, os.path.join(BASE, 'scripts', script)],
                       cwd=BASE)
    if r.returncode != 0:
        print(f'!! {script} 실패 (exit {r.returncode})')
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
    # 5. HTML 빌드
    run('build.py')

    # 6. 커밋 (변경사항 있을 때만)
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
