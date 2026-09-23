import json
import math
import os
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
ESPN_LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID", "").strip()
ESPN_S2 = os.getenv("ESPN_S2", "").strip()
ESPN_SWID = os.getenv("ESPN_SWID", "").strip()

http = requests.Session()
http.headers.update({"User-Agent": "boom-bust-lab/3.0"})


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


def get_json(url, **kwargs):
    response = http.get(url, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


def load_sleeper():
    state = get_json("https://api.sleeper.app/v1/state/nfl")
    players = get_json("https://api.sleeper.app/v1/players/nfl")
    season = int(state.get("season") or nfl.get_current_season())
    week = max(1, min(18, int(state.get("week") or nfl.get_current_week())))
    return season, week, players


def load_stats(season, week):
    try:
        frame = nfl.load_player_stats([season]).to_pandas()
    except Exception as exc:
        print(f"Current-season player stats failed: {exc}")
        return pd.DataFrame()

    frame.columns = [str(column).lower() for column in frame.columns]
    if "week" not in frame.columns:
        return pd.DataFrame()

    frame["week"] = pd.to_numeric(frame["week"], errors="coerce")
    return frame[frame["week"] < week].copy()


def load_previous_season_stats(season):
    try:
        frame = nfl.load_player_stats([season - 1]).to_pandas()
        frame.columns = [str(column).lower() for column in frame.columns]
        return frame
    except Exception as exc:
        print(f"Previous-season player stats failed: {exc}")
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

    position_regression = {
        "QB": 0.12,
        "RB": 0.20,
        "WR": 0.22,
        "TE": 0.24,
        "K": 0.28,
        "DST": 0.28,
    }.get(position, 0.20)

    projection = 0.60 * weighted_mean + 0.30 * recent_four + 0.10 * max(0.0, weighted_mean + trend)
    projection *= 1.0 - position_regression * 0.10

    standard_deviation = float(np.std(recent, ddof=1)) if len(recent) > 1 else max(2.5, weighted_mean * 0.25)
    return {
        "projection": max(0.0, projection),
        "weightedAverage": weighted_mean,
        "recentAverage": recent_four,
        "trend": trend,
        "standardDeviation": max(2.5, standard_deviation),
        "games": int(len(recent)),
    }


def build_player_models(current, previous):
    combined = pd.concat([previous, current], ignore_index=True, sort=False)
    if combined.empty:
        return {}, {position: [] for position in POSITIONS}

    points_col = fantasy_column(combined)
    name_col = player_name_column(combined)
    id_col = player_id_column(combined)
    if not points_col or not name_col or not id_col:
        return {}, {position: [] for position in POSITIONS}

    combined[points_col] = pd.to_numeric(combined[points_col], errors="coerce")
    combined = combined.dropna(subset=[points_col])
    if "season" in combined.columns and "week" in combined.columns:
        combined = combined.sort_values(["season", "week"])

    models = {}
    residuals = {position: [] for position in POSITIONS}

    for _, group in combined.groupby(id_col, dropna=False):
        latest = group.iloc[-1]
        name = clean_name(latest.get(name_col))
        position = str(latest.get("position") or "").upper()
        if not name or position not in POSITIONS:
            continue

        values = group[points_col].astype(float).to_numpy()
        model = regression_projection(values, position)
        if model is None:
            continue
        models[name] = model

        if len(values) >= 5:
            for index in range(4, len(values)):
                training = values[max(0, index - 8):index]
                estimate = regression_projection(training, position)
                if estimate:
                    residuals[position].append(float(values[index] - estimate["projection"]))

    return models, residuals


def build_matchups(schedule, week):
    if schedule.empty or "week" not in schedule.columns:
        return {}
    games = schedule[pd.to_numeric(schedule["week"], errors="coerce") == week]
    matchups = {}
    for _, game in games.iterrows():
        home = str(game.get("home_team") or "")
        away = str(game.get("away_team") or "")
        if home and away:
            matchups[home] = f"vs {away}"
            matchups[away] = f"@ {home}"
    return matchups


def injury_index(frame):
    if frame.empty:
        return {}
    name_col = player_name_column(frame)
    if not name_col:
        return {}
    week_col = "week" if "week" in frame.columns else None
    if week_col:
        frame = frame.sort_values(week_col)
    index = {}
    for _, row in frame.iterrows():
        name = clean_name(row.get(name_col))
        if name:
            index[name] = str(row.get("report_status") or row.get("practice_status") or row.get("status") or "")
    return index


def injury_multiplier(status):
    status = str(status or "").lower()
    if "out" in status or "ir" == status:
        return 0.0
    if "doubt" in status:
        return 0.55
    if "question" in status:
        return 0.85
    return 1.0


def boom_bust(mean, standard_deviation, residuals):
    if mean is None:
        return None, None, None, None
    sd = max(2.5, numeric(standard_deviation, mean * 0.30))
    floor = max(0.0, mean - 1.04 * sd)
    ceiling = mean + 1.04 * sd
    boom_line = mean + max(5.0, mean * 0.35)
    bust_line = max(0.0, mean - max(5.0, mean * 0.35))

    if len(residuals) >= 20:
        errors = np.asarray(residuals, dtype=float)
        boom = float(np.mean(mean + errors >= boom_line))
        bust = float(np.mean(mean + errors <= bust_line))
        method = "empirical_position_residuals"
    else:
        distribution = NormalDist(mean, sd)
        boom = 1.0 - distribution.cdf(boom_line)
        bust = distribution.cdf(bust_line)
        method = "normal_approximation"

    return round(floor, 1), round(ceiling, 1), round(boom * 100), round(bust * 100), method


def espn_league(season):
    if not ESPN_LEAGUE_ID:
        return {"connected": False}
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{ESPN_LEAGUE_ID}"
    cookies = {}
    if ESPN_S2 and ESPN_SWID:
        cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID}
    try:
        body = get_json(url, params=[("view", "mSettings"), ("view", "mRoster"), ("view", "mTeam")], cookies=cookies)
        teams = []
        for team in body.get("teams", []):
            teams.append({
                "id": team.get("id"),
                "name": f"{team.get('location', '')} {team.get('nickname', '')}".strip(),
                "rosterSize": len(((team.get("roster") or {}).get("entries") or [])),
            })
        return {"connected": True, "leagueId": ESPN_LEAGUE_ID, "teams": teams}
    except Exception as exc:
        print(f"ESPN import failed: {exc}")
        return {"connected": False, "error": str(exc)}


def main():
    season, week, sleeper_players = load_sleeper()
    current = load_stats(season, week)
    previous = load_previous_season_stats(season)
    schedule = load_schedule(season)
    injuries = load_injuries(season)
    models, residuals = build_player_models(current, previous)
    matchups = build_matchups(schedule, week)
    injuries_by_name = injury_index(injuries)
    espn = espn_league(season)

    players = []
    for sleeper_id, player in sleeper_players.items():
        position = str(player.get("position") or "").upper()
        if position in {"DEF", "D/ST"}:
            position = "DST"
        name = player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        if not name or position not in POSITIONS:
            continue
        if player.get("active") is False and player.get("status") != "Active":
            continue

        key = clean_name(name)
        model = models.get(key)
        status = injuries_by_name.get(key) or player.get("injury_status") or "Healthy"
        multiplier = injury_multiplier(status)
        projection = None if not model else round(model["projection"] * multiplier, 2)
        floor, ceiling, boom, bust, probability_method = boom_bust(
            projection,
            None if not model else model["standardDeviation"],
            residuals.get(position, []),
        )

        confidence = 0
        if model:
            confidence = min(95, 45 + model["games"] * 5 + (10 if len(residuals.get(position, [])) >= 20 else 0))
            if multiplier < 1:
                confidence = max(20, confidence - 15)

        players.append({
            "id": str(sleeper_id),
            "sleeperId": str(sleeper_id),
            "name": name,
            "team": player.get("team") or "FA",
            "pos": position,
            "opp": matchups.get(player.get("team") or "", ""),
            "injury": status,
            "projection": projection,
            "floor": floor,
            "ceiling": ceiling,
            "boom": boom,
            "bust": bust,
            "confidence": confidence,
            "trend": None if not model else round(model["trend"], 2),
            "flexEligible": position in {"RB", "WR", "TE"},
            "probabilityMethod": probability_method,
            "sources": {"customNflverseModel": projection},
            "model": model or {},
        })

    players.sort(key=lambda item: (item["pos"], -(item["projection"] if item["projection"] is not None else -1), item["name"]))

    output = {
        "season": season,
        "week": week,
        "scoring": SCORING,
        "generatedAt": pd.Timestamp.utcnow().isoformat(),
        "isDemo": False,
        "providers": [
            {"id": "sleeper", "name": "Sleeper", "status": "live", "role": "Player universe and current NFL state"},
            {"id": "nflverse", "name": "nflverse through nflreadpy", "status": "live" if models else "unavailable", "role": "Historical player stats, schedule, injuries, and model inputs"},
            {"id": "espn", "name": "ESPN Fantasy", "status": "connected" if espn.get("connected") else "not-configured", "role": "Optional league context only"},
        ],
        "methodology": {
            "projection": "60% recency-weighted last-eight average, 30% last-four average, and 10% nonnegative trend estimate; small position regression and injury multiplier applied",
            "floorCeiling": "Projection plus or minus 1.04 times player historical standard deviation",
            "boomThreshold": "Projection plus the greater of five points or 35% of projection",
            "bustThreshold": "Projection minus the greater of five points or 35% of projection",
            "probabilities": "Empirical historical forecast-error distribution by position when at least 20 residuals exist; otherwise normal approximation",
            "limitations": "This is an independent historical-statistics model. It does not include a licensed market projection feed, betting lines, confirmed usage, or late-breaking news beyond available injury data.",
        },
        "espn": espn,
        "supportedPositions": ["QB", "RB", "WR", "TE", "K", "FLX", "DST"],
        "players": players,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {len(players)} players for {season} Week {week}.")
    print(f"Players with model projections: {sum(player['projection'] is not None for player in players)}")
    for position in sorted(POSITIONS):
        print(f"{position}: {sum(player['pos'] == position for player in players)}")


if __name__ == "__main__":
    main()
