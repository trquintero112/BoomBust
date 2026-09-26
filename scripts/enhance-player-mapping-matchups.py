import json
import math
import re
from pathlib import Path

import nflreadpy as nfl
import numpy as np
import pandas as pd

DATA = Path("public/data/latest.json")
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
TEAM_ALIASES = {
    "LA": "LAR", "LAR": "LAR", "STL": "LAR",
    "JAC": "JAX", "JAX": "JAX", "WSH": "WAS", "WAS": "WAS",
    "OAK": "LV", "LV": "LV", "SD": "LAC", "LAC": "LAC",
}


def clean_name(value):
    value = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    tokens = [token for token in value.split() if token not in SUFFIXES]
    return " ".join(tokens)


def normalize_team(value):
    team = str(value or "").upper().strip()
    return TEAM_ALIASES.get(team, team)


def finite(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def load_frame(loader, label):
    try:
        frame = loader().to_pandas()
        frame.columns = [str(column).lower() for column in frame.columns]
        print(f"{label}: {len(frame)} rows")
        return frame
    except Exception as error:
        print(f"{label} unavailable: {error}")
        return pd.DataFrame()


def projection_model(values, position):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    recent = values[-8:]
    weights = np.arange(1, len(recent) + 1, dtype=float)
    weighted = float(np.average(recent, weights=weights))
    recent_four = float(np.mean(recent[-4:]))
    trend = float(np.polyfit(np.arange(len(recent)), recent, 1)[0]) if len(recent) > 1 else 0.0
    regression = {"QB": .12, "RB": .20, "WR": .22, "TE": .24}.get(position, .20)
    projection = (.60 * weighted + .30 * recent_four + .10 * max(0.0, weighted + trend))
    projection *= 1.0 - regression * .10
    standard_deviation = float(np.std(recent, ddof=1)) if len(recent) > 1 else max(2.5, weighted * .25)
    return {
        "projection": round(max(0.0, projection), 2),
        "weightedAverage": weighted,
        "recentAverage": recent_four,
        "trend": trend,
        "standardDeviation": max(2.5, standard_deviation),
        "games": int(len(recent)),
        "method": "id_crosswalk_historical_model",
    }


def id_crosswalks(ids):
    sleeper_to_gsis = {}
    names_to_gsis = {}
    if ids.empty:
        return sleeper_to_gsis, names_to_gsis
    for _, row in ids.iterrows():
        gsis = row.get("gsis_id")
        if gsis is None or str(gsis).lower() in {"nan", "none", ""}:
            continue
        gsis = str(gsis)
        sleeper = row.get("sleeper_id")
        if sleeper is not None and str(sleeper).lower() not in {"nan", "none", ""}:
            sleeper_to_gsis[str(sleeper)] = gsis
        for column in ("name", "merge_name"):
            if column in ids.columns and row.get(column) is not None:
                key = clean_name(row.get(column))
                if key:
                    names_to_gsis[key] = gsis
    return sleeper_to_gsis, names_to_gsis


def stats_columns(stats):
    points = "fantasy_points_ppr" if "fantasy_points_ppr" in stats.columns else "fantasy_points"
    player_id = "player_id" if "player_id" in stats.columns else "gsis_id"
    player_name = "player_display_name" if "player_display_name" in stats.columns else "player_name"
    opponent = next((column for column in ("opponent_team", "opponent", "opp") if column in stats.columns), None)
    return points, player_id, player_name, opponent


def prior_stats(stats, season, week):
    season_values = pd.to_numeric(stats["season"], errors="coerce") if "season" in stats else pd.Series(season, index=stats.index)
    week_values = pd.to_numeric(stats["week"], errors="coerce") if "week" in stats else pd.Series(0, index=stats.index)
    return stats[(season_values < season) | ((season_values == season) & (week_values < week))].copy()


def build_models(stats, season, week, sleeper_to_gsis, names_to_gsis):
    models = {}
    if stats.empty:
        return models
    points, player_id, player_name, _ = stats_columns(stats)
    if points not in stats or player_id not in stats:
        return models
    data = prior_stats(stats, season, week)
    data[points] = pd.to_numeric(data[points], errors="coerce")
    data = data.dropna(subset=[points])
    sort_columns = [column for column in ("season", "week") if column in data]
    if sort_columns:
        data = data.sort_values(sort_columns)
    for gsis, group in data.groupby(player_id, dropna=False):
        if gsis is None or str(gsis).lower() == "nan":
            continue
        position = str(group.iloc[-1].get("position") or "").upper()
        if position not in SKILL_POSITIONS:
            continue
        model = projection_model(group[points].astype(float).to_numpy(), position)
        if not model:
            continue
        models[str(gsis)] = model
        name = clean_name(group.iloc[-1].get(player_name)) if player_name in group else ""
        if name:
            models[name] = model
    for sleeper_id, gsis in sleeper_to_gsis.items():
        if gsis in models:
            models[sleeper_id] = models[gsis]
    for name, gsis in names_to_gsis.items():
        if gsis in models:
            models[name] = models[gsis]
    return models


def matchup_modifiers(stats, season, week):
    modifiers = {}
    if stats.empty:
        return modifiers
    points, _, _, opponent = stats_columns(stats)
    if points not in stats or opponent is None or "position" not in stats:
        print("Opponent-adjustment columns are unavailable")
        return modifiers
    data = prior_stats(stats, season, week)
    data = data[data["position"].isin(SKILL_POSITIONS)].copy()
    data[points] = pd.to_numeric(data[points], errors="coerce")
    data = data.dropna(subset=[points, opponent, "week"])
    data["opponent_key"] = data[opponent].map(normalize_team)
    game_totals = data.groupby(["season", "week", "opponent_key", "position"], as_index=False)[points].sum()
    if game_totals.empty:
        return modifiers
    # Use up to the eight most recent opponent games and compare with the league position average.
    for position in SKILL_POSITIONS:
        position_data = game_totals[game_totals["position"] == position].sort_values(["season", "week"])
        if position_data.empty:
            continue
        league_average = float(position_data[points].mean())
        if league_average <= 0:
            continue
        for team, group in position_data.groupby("opponent_key"):
            allowed = float(group.tail(8)[points].mean())
            raw = allowed / league_average
            modifiers[(normalize_team(team), position)] = round(max(.85, min(1.15, raw)), 4)
    return modifiers


def recalculate_ranges(player, original_projection, new_projection):
    if original_projection and original_projection > 0:
        ratio = new_projection / original_projection
        for field in ("floor", "ceiling"):
            value = finite(player.get(field))
            if value is not None:
                player[field] = round(max(0.0, value * ratio), 1)
    model = player.get("model") or {}
    sd = finite(model.get("standardDeviation"), max(2.5, new_projection * .30))
    if player.get("floor") is None:
        player["floor"] = round(max(0.0, new_projection - 1.04 * sd), 1)
    if player.get("ceiling") is None:
        player["ceiling"] = round(new_projection + 1.04 * sd, 1)


def enhance_players(players, models, modifiers):
    filled = 0
    adjusted = 0
    for player in players:
        position = str(player.get("pos") or "").upper()
        if position not in SKILL_POSITIONS:
            continue
        sleeper_id = str(player.get("sleeperId") or player.get("id") or "")
        name_key = clean_name(player.get("name"))
        original = finite(player.get("projection"))
        if original is None:
            model = models.get(sleeper_id) or models.get(name_key)
            if model:
                player["model"] = dict(model)
                player["projection"] = model["projection"]
                player["trend"] = round(model["trend"], 2)
                player["confidence"] = min(95, 45 + model["games"] * 5)
                player["probabilityMethod"] = "id_crosswalk_model"
                player.setdefault("sources", {})["customNflverseModel"] = model["projection"]
                original = model["projection"]
                filled += 1
        if original is None or str(player.get("team") or "FA").upper() == "FA":
            continue
        opponent_text = str(player.get("opp") or "")
        opponent = normalize_team(opponent_text.replace("vs", "").replace("@", "").strip())
        modifier = modifiers.get((opponent, position), 1.0)
        new_projection = round(original * modifier, 2)
        player["projection"] = new_projection
        player["matchupModifier"] = modifier
        player["projectionBeforeMatchup"] = original
        player.setdefault("sources", {})["customNflverseModel"] = new_projection
        recalculate_ranges(player, original, new_projection)
        if modifier != 1.0:
            adjusted += 1
    return filled, adjusted


def main():
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    season = int(payload.get("season"))
    stats = load_frame(lambda: nfl.load_player_stats([season - 1, season]), "Player stats")
    ids = load_frame(lambda: nfl.load_ff_playerids(), "Fantasy player ID crosswalk")
    sleeper_to_gsis, names_to_gsis = id_crosswalks(ids)

    total_filled = 0
    total_adjusted = 0
    snapshots = payload.get("weekSnapshots") or {}
    if snapshots:
        for week_key, snapshot in snapshots.items():
            week = int(snapshot.get("week") or week_key)
            models = build_models(stats, season, week, sleeper_to_gsis, names_to_gsis)
            modifiers = matchup_modifiers(stats, season, week)
            filled, adjusted = enhance_players(snapshot.get("players") or [], models, modifiers)
            total_filled += filled
            total_adjusted += adjusted
            print(f"Week {week}: filled={filled}, matchup-adjusted={adjusted}, modifiers={len(modifiers)}")
        current = str(payload.get("defaultWeek") or payload.get("week"))
        if current in snapshots:
            payload["players"] = snapshots[current]["players"]
    else:
        week = int(payload.get("week"))
        models = build_models(stats, season, week, sleeper_to_gsis, names_to_gsis)
        modifiers = matchup_modifiers(stats, season, week)
        total_filled, total_adjusted = enhance_players(payload.get("players") or [], models, modifiers)

    payload.setdefault("methodology", {})["idMapping"] = "Sleeper ID to GSIS ID through nflverse ff-player crosswalk, with suffix-insensitive name fallback"
    payload["methodology"]["matchupAdjustment"] = "Opponent fantasy points allowed to each position over up to eight recent games, capped between 0.85 and 1.15"
    DATA.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Completed enhancement: filled={total_filled}, matchup-adjusted={total_adjusted}")


if __name__ == "__main__":
    main()
