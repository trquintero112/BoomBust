from pathlib import Path
import re

refresh = Path("scripts/refresh-projections.py")
if not refresh.exists():
    raise SystemExit("Missing scripts/refresh-projections.py")

code = refresh.read_text(encoding="utf-8")

# Add a function that aliases nflverse kicker models to Sleeper and other IDs.
if "def add_kicker_id_aliases(" not in code:
    marker = "\ndef build_matchups("
    function = r'''

def add_kicker_id_aliases(kicker_models, player_ids):
    """Add cross-platform aliases using nflverse/ffverse player ID mappings."""
    if player_ids.empty:
        return kicker_models

    aliases = dict(kicker_models)
    id_columns = [
        "sleeper_id", "gsis_id", "nfl_id", "espn_id",
        "sportradar_id", "fantasypros_id", "name", "merge_name",
    ]

    for _, row in player_ids.iterrows():
        source_model = None
        source_key = None

        # PBP kicker history is normally keyed by GSIS ID. Name keys remain a fallback.
        for column in ("gsis_id", "nfl_id", "name", "merge_name"):
            if column not in player_ids.columns:
                continue
            value = row.get(column)
            if value is None or (hasattr(value, "__class__") and str(value) == "nan"):
                continue
            candidate = clean_name(value)
            if candidate in aliases:
                source_model = aliases[candidate]
                source_key = candidate
                break

        if source_model is None:
            continue

        for column in id_columns:
            if column not in player_ids.columns:
                continue
            value = row.get(column)
            if value is None or str(value).lower() in {"nan", "none", ""}:
                continue
            aliases[clean_name(value)] = source_model

        source_model.setdefault("crosswalkSource", source_key)

    return aliases
'''
    if marker not in code:
        raise SystemExit("Could not locate build_matchups function")
    code = code.replace(marker, function + marker, 1)

# Ensure the final kicker lookup includes Sleeper ID itself.
old_tuple = '''                player.get("gsis_id"),
                player.get("nfl_id"),
                player.get("espn_id"),
                player.get("sportradar_id"),
                name,'''
new_tuple = '''                sleeper_id,
                player.get("player_id"),
                player.get("gsis_id"),
                player.get("nfl_id"),
                player.get("espn_id"),
                player.get("sportradar_id"),
                name,'''
if old_tuple in code:
    code = code.replace(old_tuple, new_tuple, 1)
elif "sleeper_id," not in code:
    raise SystemExit("Could not locate kicker key tuple")

# Load the official ffverse ID crosswalk in main().
load_marker = '''    injuries=load_frame(lambda:nfl.load_injuries([season]),"Injuries")'''
load_replacement = '''    injuries=load_frame(lambda:nfl.load_injuries([season]),"Injuries")
    player_ids=load_frame(lambda:nfl.load_ff_playerids(),"Fantasy player ID crosswalk")'''
if load_marker in code and "Fantasy player ID crosswalk" not in code:
    code = code.replace(load_marker, load_replacement, 1)

# Alias each target week's available kicker models before matching them to Sleeper.
model_marker = '''        k_training=stats_before_week(all_kicker,season,target_week); kicker_models,k_residuals=build_kicker_models(k_training)'''
model_replacement = '''        k_training=stats_before_week(all_kicker,season,target_week); kicker_models,k_residuals=build_kicker_models(k_training)
        kicker_models=add_kicker_id_aliases(kicker_models,player_ids)'''
if model_marker in code and "add_kicker_id_aliases(kicker_models,player_ids)" not in code:
    code = code.replace(model_marker, model_replacement, 1)

# Improve the matched count to include Sleeper ID aliases.
count_old = '''                    sleeper_player.get("gsis_id"),
                    sleeper_player.get("nfl_id"),'''
count_new = '''                    sleeper_id,
                    sleeper_player.get("gsis_id"),
                    sleeper_player.get("nfl_id"),'''
# Only replace if the script has a loop exposing sleeper_id. We otherwise leave diagnostics alone.
if count_old in code and "for sleeper_id, sleeper_player in sleeper_players.items()" in code:
    code = code.replace(count_old, count_new, 1)

refresh.write_text(code, encoding="utf-8")
print("Patched scripts/refresh-projections.py with nflverse ID crosswalk")
