import yaml, glob, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = os.path.join(BASE, '.github', 'workflows')
for f in sorted(glob.glob(os.path.join(W, '*.yml'))):
    try:
        d = yaml.safe_load(open(f, encoding='utf-8'))
        jobs = list((d.get('jobs') or {}).keys())
        print('OK   %-24s jobs=%s' % (os.path.basename(f), jobs))
    except Exception as e:
        print('FAIL', os.path.basename(f), e)
