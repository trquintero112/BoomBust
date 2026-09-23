import json
import math
import re
from pathlib import Path
from statistics import NormalDist

import nflreadpy as nfl
import numpy as np
import pandas as pd
import requests

OUTPUT = Path("public/data/latest.json")
SCORING = "PPR"
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}
http = requests.Session()
http.headers.update({"User-Agent": "boom-bust-lab/4.1"})


def clean_name(value):
    value = str(value or "").lower().strip()
    value = re.sub(r"[^a-z0-9 ]+", "", value)
    return re.sub(r"\s+", " ", value)


def numeric(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def get_json(url):
    response = http.get(url, timeout=90)
    response.raise_for_status()
    return response.json()


def load_sleeper():
    state = get_json("https://api.sleeper.app/v1/state/nfl")
    players = get_json("https://api.sleeper.app/v1/players/nfl")
    season = int(state.get("season") or nfl.get_current_season())
    week = max(1, min(18, int(state.get("week") or nfl.get_current_week())))
    return season, week, players


def load_player_stats(seasons):
    try:
        frame = nfl.load_player_stats(seasons).to_pandas()
        frame.columns = [str(column).lower() for column in frame.columns]
        return frame
    except Exception as exc:
        print(f"Player stats failed for {seasons}: {exc}")
        return pd.DataFrame()


def load_schedule(season):
    try:
        frame = nfl.load_schedules([season]).to_pandas()
        frame.columns = [str(column).lower() for column in frame.columns]
        return frame
    except Exception as exc:
        print(f"Schedule import failed: {exc}")
        return pd.DataFrame()


def load_injuries(season):
    try:
        frame = nfl.load_injuries([season]).to_pandas()
        frame.columns = [str(column).lower() for column in frame.columns]
        return frame
    except Exception as exc:
        print(f"Injury import failed: {exc}")
        return pd.DataFrame()


def fantasy_column(frame):
    for column in ("fantasy_points_ppr", "fantasy_points"):
        if column in frame.columns:
            return column
    return None


def player_name_column(frame):
    for column in ("player_display_name", "player_name", "name"):
        if column in frame.columns:
            return column
    return None


def player_id_column(frame):
    for column in ("player_id", "gsis_id"):
        if column in frame.columns:
            return column
    return player_name_column(frame)


def regression_projection(values, position):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    recent = values[-8:]
    weights = np.arange(1, len(recent) + 1, dtype=float)
    weighted_mean = float(np.average(recent, weights=weights))
    recent_four = float(np.mean(recent[-4:]))
    trend = float(np.polyfit(np.arange(len(recent)), recent, 1)[0]) if len(recent) > 1 else 0.0
    if not all(np.isfinite([weighted_mean, recent_four, trend])):
        return None
    position_regression = {"QB": .12, "RB": .20, "WR": .22, "TE": .24, "K": .28, "DST": .28}.get(position, .20)
    projection = (.60 * weighted_mean + .30 * recent_four + .10 * max(0.0, weighted_mean + trend))
    projection *= 1.0 - position_regression * .10
    if not np.isfinite(projection):
        return None
    sd = float(np.std(recent, ddof=1)) if len(recent) > 1 else max(2.5, weighted_mean * .25)
    return {
        "projection": max(0.0, projection),
        "weightedAverage": weighted_mean,
        "recentAverage": recent_four,
        "trend": trend,
        "standardDeviation": max(2.5, sd),
        "games": int(len(recent)),
        "method": "historical_player_model",
    }


def stats_before_week(all_stats, season, target_week):
    if all_stats.empty:
        return pd.DataFrame()
    data = all_stats.copy()
    season_values = pd.to_numeric(data.get("season", season), errors="coerce")
    week_values = pd.to_numeric(data.get("week", 0), errors="coerce")
    return data[(season_values < season) | ((season_values == season) & (week_values < target_week))].copy()


def build_player_models(data):
    if data.empty:
        return {}, {position: [] for position in POSITIONS}
    points_col, name_col, id_col = fantasy_column(data), player_name_column(data), player_id_column(data)
    if not points_col or not name_col or not id_col:
        return {}, {position: [] for position in POSITIONS}
    data = data.copy()
    data[points_col] = pd.to_numeric(data[points_col], errors="coerce")
    data = data.dropna(subset=[points_col])
    sort_columns = [column for column in ("season", "week") if column in data.columns]
    if sort_columns:
        data = data.sort_values(sort_columns)
    models, residuals = {}, {position: [] for position in POSITIONS}
    for _, group in data.groupby(id_col, dropna=False):
        latest = group.iloc[-1]
        name = clean_name(latest.get(name_col))
        position = str(latest.get("position") or "").upper()
        if not name or position not in POSITIONS:
            continue
        values = group[points_col].astype(float).to_numpy()
        model = regression_projection(values, position)
        if model:
            models[name] = model
        if len(values) >= 5:
            for index in range(4, len(values)):
                estimate = regression_projection(values[max(0, index - 8):index], position)
                if estimate:
                    residuals[position].append(float(values[index] - estimate["projection"]))
    return models, residuals


def build_matchups(schedule, week):
    if schedule.empty or "week" not in schedule.columns:
        return {}
    games = schedule[pd.to_numeric(schedule["week"], errors="coerce") == week]
    matchups = {}
    for _, game in games.iterrows():
        home, away = str(game.get("home_team") or ""), str(game.get("away_team") or "")
        if home and away:
            matchups[home], matchups[away] = f"vs {away}", f"@ {home}"
    return matchups


def injury_index(frame):
    if frame.empty:
        return {}
    name_col = player_name_column(frame)
    if not name_col:
        return {}
    if "week" in frame.columns:
        frame = frame.sort_values("week")
    return {
        clean_name(row.get(name_col)): str(row.get("report_status") or row.get("practice_status") or row.get("status") or "")
        for _, row in frame.iterrows() if clean_name(row.get(name_col))
    }


def injury_multiplier(status):
    status = str(status or "").lower()
    if any(token in status for token in ("out", "injured reserve", "pup", "suspend")) or status == "ir":
        return 0.0
    if "doubt" in status:
        return .55
    if "question" in status:
        return .85
    return 1.0


def boom_bust(mean, sd, residuals):
    if mean is None:
        return None, None, None, None, "no_projection"
    sd = max(2.5, numeric(sd, mean * .30))
    floor, ceiling = max(0.0, mean - 1.04 * sd), mean + 1.04 * sd
    boom_line, bust_line = mean + max(5.0, mean * .35), max(0.0, mean - max(5.0, mean * .35))
    if len(residuals) >= 20:
        errors = np.asarray(residuals, dtype=float)
        boom, bust = float(np.mean(mean + errors >= boom_line)), float(np.mean(mean + errors <= bust_line))
        method = "empirical_position_residuals"
    else:
        distribution = NormalDist(mean, sd)
        boom, bust = 1.0 - distribution.cdf(boom_line), distribution.cdf(bust_line)
        method = "normal_approximation"
    return round(floor, 1), round(ceiling, 1), round(boom * 100), round(bust * 100), method


def make_players(sleeper_players, models, residuals, matchups, injuries, target_week, current_week):
    results = []
    for sleeper_id, player in sleeper_players.items():
        position = str(player.get("position") or "").upper()
        position = "DST" if position in {"DEF", "D/ST"} else position
        name = player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        if not name or position not in POSITIONS:
            continue
        key = clean_name(name)
        team = player.get("team") or "FA"
        model = models.get(key)
        status = (injuries.get(key) or player.get("injury_status") or "Healthy") if target_week == current_week else "Historical"
        multiplier = injury_multiplier(status) if target_week == current_week else 1.0
        projection = None if not model else round(model["projection"] * multiplier, 2)
        if team == "FA":
            status, projection = "FREE AGENT", 0.0
        floor, ceiling, boom, bust, method = boom_bust(projection, None if not model else model["standardDeviation"], residuals.get(position, []))
        if team == "FA":
            floor, ceiling, boom, bust, method = 0.0, 0.0, 0, 100, "inactive_player"
        confidence = 0 if not model else min(95, 45 + model["games"] * 5 + (10 if len(residuals.get(position, [])) >= 20 else 0))
        if multiplier < 1:
            confidence = max(20, confidence - 15)
        results.append({
            "id": str(sleeper_id), "sleeperId": str(sleeper_id), "name": name,
            "team": team, "pos": position, "opp": matchups.get(team, ""), "injury": status,
            "projection": projection, "floor": floor, "ceiling": ceiling,
            "boom": boom, "bust": bust, "confidence": confidence,
            "trend": None if not model else round(model["trend"], 2),
            "flexEligible": position in {"RB", "WR", "TE"},
            "probabilityMethod": method, "sources": {"customNflverseModel": projection},
            "model": model or {}, "projectionWeek": target_week,
        })
    results.sort(key=lambda item: (item["pos"], -(item["projection"] if item["projection"] is not None else -1), item["name"]))
    return results


def main():
    season, current_week, sleeper_players = load_sleeper()
    all_stats = load_player_stats([season - 1, season])
    schedule, injuries = load_schedule(season), load_injuries(season)
    injuries_by_name = injury_index(injuries)
    week_snapshots = {}
    for target_week in range(1, current_week + 1):
        models, residuals = build_player_models(stats_before_week(all_stats, season, target_week))
        players = make_players(sleeper_players, models, residuals, build_matchups(schedule, target_week), injuries_by_name, target_week, current_week)
        week_snapshots[str(target_week)] = {
            "week": target_week,
            "generatedMode": "historical_backtest" if target_week < current_week else "current",
            "players": players,
        }
        print(f"Week {target_week}: {sum(p['projection'] is not None for p in players)} projections")
    current_players = week_snapshots[str(current_week)]["players"]
    output = {
        "season": season, "week": current_week, "defaultWeek": current_week,
        "availableWeeks": list(range(1, current_week + 1)), "scoring": SCORING,
        "generatedAt": pd.Timestamp.utcnow().isoformat(), "isDemo": False,
        "providers": [
            {"id": "sleeper", "name": "Sleeper", "status": "live"},
            {"id": "nflverse", "name": "nflverse through nflreadpy", "status": "live"},
        ],
        "methodology": {"historicalWeeks": "Each past week is rebuilt using only games before that week."},
        "supportedPositions": ["QB", "RB", "WR", "TE", "K", "FLX", "DST"],
        "players": current_players, "weekSnapshots": week_snapshots,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
