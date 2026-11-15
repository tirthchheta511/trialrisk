"""Runs once while Render builds the Docker image:
1. puts the big files back together (they were split into parts because GitHub's website takes files up to 25 MB),
2. downloads the AI text model into the image, so the live site never has to wait for it,
3. reviews a saved trial from start to finish, so a broken build stops here instead of on the live site."""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
sha = lambda path: hashlib.sha256(open(path, 'rb').read()).hexdigest()

for target, info in (json.load(open('parts.json')) if os.path.exists('parts.json') else {}).items():
    if os.path.exists(target) and sha(target) == info['sha256']:
        continue
    names = [f'{target}.part{i}' for i in range(1, info['parts'] + 1)]
    missing = [n for n in names if not os.path.exists(n)]
    if missing:
        sys.exit(f'{", ".join(missing)} is missing on GitHub: upload it, then deploy again')
    with open(target, 'wb') as out:
        for n in names:
            with open(n, 'rb') as f:
                out.write(f.read())
    if sha(target) != info['sha256']:
        sys.exit(f'{target} did not come back together correctly: upload its parts to GitHub again')
    for n in names:
        os.remove(n)
    print(f'Rebuilt {target} from {len(names)} parts ({os.path.getsize(target) / 1e6:.1f} MB)')

t0 = time.time()
import main
res = main.engine.score_study(json.load(open('tests/sample_study.json')))
assert 0 <= res['risk'] <= 1 and res.get('similar_checked') == 20, res
print(f"Self-test passed: {res['nct_id']} risk {100 * res['risk']:.0f}% ({res['band']}), "
      f"{res['similar_stopped']} of {res['similar_checked']} similar past trials stopped, {time.time() - t0:.0f} s")
