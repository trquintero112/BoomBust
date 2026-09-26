from pathlib import Path

workflow = Path('.github/workflows/refresh.yml')
if not workflow.exists():
    raise SystemExit('Missing .github/workflows/refresh.yml')
text = workflow.read_text(encoding='utf-8')
if 'Enhance player IDs and defensive matchups' in text:
    print('Workflow already patched')
    raise SystemExit(0)
needle = '      - name: Add both ESPN leagues\n'
step = '''      - name: Enhance player IDs and defensive matchups
        run: python scripts/enhance-player-mapping-matchups.py
'''
if needle not in text:
    raise SystemExit('Could not find Add both ESPN leagues step')
workflow.write_text(text.replace(needle, step + needle, 1), encoding='utf-8')
print('Patched .github/workflows/refresh.yml')
