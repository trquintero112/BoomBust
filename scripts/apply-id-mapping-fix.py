from pathlib import Path
import re

refresh = Path('scripts/refresh-projections.py')
main = Path('src/main.jsx')

if not refresh.exists():
    raise SystemExit('Missing scripts/refresh-projections.py')
if not main.exists():
    raise SystemExit('Missing src/main.jsx')

py = refresh.read_text(encoding='utf-8')
jsx = main.read_text(encoding='utf-8')

# 1. Add canonical team aliases once.
if 'TEAM_ALIASES = {' not in py:
    marker = 'POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}'
    aliases = '''POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}

TEAM_ALIASES = {
    "LA": "LAR", "LAR": "LAR", "STL": "LAR",
    "JAC": "JAX", "JAX": "JAX",
    "WSH": "WAS", "WAS": "WAS",
    "OAK": "LV", "LV": "LV",
    "SD": "LAC", "LAC": "LAC",
}


def normalize_team(value):
    team = str(value or "").upper().strip()
    return TEAM_ALIASES.get(team, team)
'''
    if marker not in py:
        raise SystemExit('Could not find POSITIONS marker in refresh-projections.py')
    py = py.replace(marker, aliases, 1)

# 2. Normalize DST history/model keys.
py = py.replace('models[str(team)] = model', 'models[normalize_team(team)] = model')
py = py.replace('models[str(team)] = model\n', 'models[normalize_team(team)] = model\n')

# 3. Normalize team at final player construction.
py = re.sub(
    r'team\s*=\s*player\.get\("team"\)\s*or\s*"FA"',
    'team = normalize_team(player.get("team") or "FA")',
    py,
)

# 4. Replace K/DST lookup block with stable-ID-first matching.
lookup_patterns = [
    r'''if position == "K":\s*model\s*=\s*kicker_models\.get\(key\);\s*position_residuals\s*=\s*residuals\["K"\]\s*elif position == "DST":\s*model\s*=\s*dst_models\.get\(team\);\s*position_residuals\s*=\s*residuals\["DST"\]''',
    r'''kicker_key\s*=\s*clean_name\(player\.get\("gsis_id"\)\s*or\s*key\)\s*if position == "K":\s*model\s*=\s*kicker_models\.get\(kicker_key\)\s*or\s*kicker_models\.get\(key\);\s*position_residuals\s*=\s*residuals\["K"\]\s*elif position == "DST":\s*model\s*=\s*dst_models\.get\(team\);\s*position_residuals\s*=\s*residuals\["DST"\]'''
]
replacement = '''kicker_keys = [
            clean_name(identifier)
            for identifier in (
                player.get("gsis_id"),
                player.get("nfl_id"),
                player.get("espn_id"),
                player.get("sportradar_id"),
                name,
            )
            if identifier
        ]
        if position == "K":
            model = next(
                (kicker_models.get(kicker_key) for kicker_key in kicker_keys if kicker_models.get(kicker_key)),
                None,
            )
            position_residuals = residuals["K"]
        elif position == "DST":
            model = dst_models.get(normalize_team(team))
            position_residuals = residuals["DST"]'''

changed_lookup = False
for pattern in lookup_patterns:
    new_py, count = re.subn(pattern, replacement, py, count=1, flags=re.S)
    if count:
        py = new_py
        changed_lookup = True
        break

if not changed_lookup and 'kicker_keys = [' not in py:
    raise SystemExit('Could not locate K/DST lookup block. No source files were committed.')

# 5. Ensure PBP kicker history prefers stable ID.
py = py.replace(
    'kicker = play.get("kicker_player_name") or play.get("kicker_player_id")',
    'kicker = play.get("kicker_player_id") or play.get("kicker_player_name")',
)

# 6. Add diagnostics once.
if 'K matched=' not in py:
    py = py.replace(
        'print(f"Week {target_week}: K models={len(kicker_models)}, DST models={len(dst_models)}")',
        '''matched_kickers = sum(
            1 for sleeper_player in sleeper_players.values()
            if str(sleeper_player.get("position") or "").upper() == "K"
            and any(
                clean_name(identifier) in kicker_models
                for identifier in (
                    sleeper_player.get("gsis_id"),
                    sleeper_player.get("nfl_id"),
                    sleeper_player.get("espn_id"),
                    sleeper_player.get("sportradar_id"),
                    sleeper_player.get("full_name"),
                )
                if identifier
            )
        )
        print(f"Week {target_week}: K models={len(kicker_models)}, K matched={matched_kickers}, DST models={len(dst_models)}")''',
    )

# 7. Stop React from converting null to the real number zero.
old_min = "const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};"
new_min = "const num=v=>{if(v===null||v===undefined||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null};"
if old_min in jsx:
    jsx = jsx.replace(old_min, new_min, 1)
else:
    jsx = re.sub(
        r'const num\s*=\s*v\s*=>\s*\{\s*const n\s*=\s*Number\(v\);\s*return Number\.isFinite\(n\)\?n:null;\s*\};',
        new_min,
        jsx,
        count=1,
    )

refresh.write_text(py, encoding='utf-8')
main.write_text(jsx, encoding='utf-8')
print('Patched scripts/refresh-projections.py')
print('Patched src/main.jsx')
