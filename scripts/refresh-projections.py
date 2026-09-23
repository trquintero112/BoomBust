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
http.headers.update({"User-Agent": "boom-bust-lab/5.0"})


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


def load_frame(loader, label):
    try:
        frame = loader().to_pandas()
        frame.columns = [str(column).lower() for column in frame.columns]
        return frame
    except Exception as exc:
        print(f"{label} failed: {exc}")
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
    regression = {"QB": .12, "RB": .20, "WR": .22, "TE": .24, "K": .16, "DST": .18}.get(position, .20)
    projection = (.60 * weighted_mean + .30 * recent_four + .10 * max(0.0, weighted_mean + trend))
    projection *= 1.0 - regression * .10
    sd = float(np.std(recent, ddof=1)) if len(recent) > 1 else max(2.5, weighted_mean * .25)
    return {
        "projection": max(0.0, projection), "weightedAverage": weighted_mean,
        "recentAverage": recent_four, "trend": trend,
        "standardDeviation": max(2.5, sd), "games": int(len(recent)),
        "method": "historical_position_model",
    }


def stats_before_week(frame, season, target_week):
    if frame.empty:
        return pd.DataFrame()
    season_values = pd.to_numeric(frame["season"], errors="coerce") if "season" in frame else pd.Series(season, index=frame.index)
    week_values = pd.to_numeric(frame["week"], errors="coerce") if "week" in frame else pd.Series(0, index=frame.index)
    return frame[(season_values < season) | ((season_values == season) & (week_values < target_week))].copy()


def build_skill_models(data):
    models, residuals = {}, {position: [] for position in POSITIONS}
    if data.empty:
        return models, residuals
    points_col, name_col, id_col = fantasy_column(data), player_name_column(data), player_id_column(data)
    if not points_col or not name_col or not id_col:
        return models, residuals
    data = data.copy()
    data[points_col] = pd.to_numeric(data[points_col], errors="coerce")
    data = data.dropna(subset=[points_col])
    sort_columns = [column for column in ("season", "week") if column in data]
    if sort_columns:
        data = data.sort_values(sort_columns)
    for _, group in data.groupby(id_col, dropna=False):
        latest = group.iloc[-1]
        name = clean_name(latest.get(name_col))
        position = str(latest.get("position") or "").upper()
        if not name or position not in {"QB", "RB", "WR", "TE"}:
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


def pbp_flag(frame, column):
    if column not in frame:
        return pd.Series(0.0, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def kicker_points_by_play(pbp):
    if pbp.empty:
        return pd.DataFrame(columns=["season", "week", "name", "team", "points"])
    rows = []
    for _, play in pbp.iterrows():
        season, week = play.get("season"), play.get("week")
        team = play.get("posteam")
        if not team or pd.isna(week):
            continue
        fg_result = str(play.get("field_goal_result") or "").lower()
        xp_result = str(play.get("extra_point_result") or "").lower()
        kicker = play.get("kicker_player_name") or play.get("kicker_player_id")
        points = 0.0
        if fg_result == "made":
            distance = numeric(play.get("kick_distance"), 0) or 0
            points = 5.0 if distance >= 50 else 4.0 if distance >= 40 else 3.0
        elif xp_result == "good":
            points = 1.0
        if kicker and points:
            rows.append({"season": int(season), "week": int(week), "name": clean_name(kicker), "team": team, "points": points})
    if not rows:
        return pd.DataFrame(columns=["season", "week", "name", "team", "points"])
    return pd.DataFrame(rows).groupby(["season", "week", "name", "team"], as_index=False)["points"].sum()


def points_allowed_score(points):
    if points <= 0: return 10.0
    if points <= 6: return 7.0
    if points <= 13: return 4.0
    if points <= 20: return 1.0
    if points <= 27: return 0.0
    if points <= 34: return -1.0
    return -4.0


def dst_weekly_points(pbp, schedule):
    if pbp.empty:
        return pd.DataFrame(columns=["season", "week", "team", "points"])
    data = pbp.copy()
    required = ["season", "week", "defteam"]
    if any(column not in data for column in required):
        return pd.DataFrame(columns=["season", "week", "team", "points"])
    data = data.dropna(subset=["defteam", "week"])
    data["sacks"] = pbp_flag(data, "sack")
    data["ints"] = pbp_flag(data, "interception")
    data["fumbles"] = pbp_flag(data, "fumble_lost")
    data["safeties"] = pbp_flag(data, "safety")
    data["def_tds"] = 0.0
    if "touchdown" in data:
        touchdown = pbp_flag(data, "touchdown")
        interception = pbp_flag(data, "interception")
        fumble_lost = pbp_flag(data, "fumble_lost")
        return_touchdown = pbp_flag(data, "return_touchdown")
        data["def_tds"] = touchdown * (((interception + fumble_lost + return_touchdown) > 0).astype(float))
    grouped = data.groupby(["season", "week", "defteam"], as_index=False).agg(
        sacks=("sacks", "sum"), ints=("ints", "sum"), fumbles=("fumbles", "sum"),
        safeties=("safeties", "sum"), def_tds=("def_tds", "sum")
    ).rename(columns={"defteam": "team"})
    grouped["points"] = grouped["sacks"] + 2*grouped["ints"] + 2*grouped["fumbles"] + 2*grouped["safeties"] + 6*grouped["def_tds"]
    if not schedule.empty and all(column in schedule for column in ("season", "week", "home_team", "away_team", "home_score", "away_score")):
        allowed = []
        for _, game in schedule.dropna(subset=["week"]).iterrows():
            allowed.extend([
                {"season": int(game["season"]), "week": int(game["week"]), "team": game["home_team"], "allowed": numeric(game["away_score"], 0)},
                {"season": int(game["season"]), "week": int(game["week"]), "team": game["away_team"], "allowed": numeric(game["home_score"], 0)},
            ])
        allowed = pd.DataFrame(allowed)
        grouped = grouped.merge(allowed, on=["season", "week", "team"], how="left")
        grouped["points"] += grouped["allowed"].fillna(0).map(points_allowed_score)
    return grouped[["season", "week", "team", "points"]]


def build_kicker_models(kicker_history):
    models, residuals = {}, []
    if kicker_history.empty:
        return models, residuals
    for name, group in kicker_history.sort_values(["season", "week"]).groupby("name"):
        values = group["points"].astype(float).to_numpy()
        model = regression_projection(values, "K")
        if model:
            models[name] = model
        if len(values) >= 5:
            for index in range(4, len(values)):
                estimate = regression_projection(values[max(0, index-8):index], "K")
                if estimate: residuals.append(float(values[index] - estimate["projection"]))
    return models, residuals


def build_dst_models(dst_history):
    models, residuals = {}, []
    if dst_history.empty:
        return models, residuals
    for team, group in dst_history.sort_values(["season", "week"]).groupby("team"):
        values = group["points"].astype(float).to_numpy()
        model = regression_projection(values, "DST")
        if model:
            models[str(team)] = model
        if len(values) >= 5:
            for index in range(4, len(values)):
                estimate = regression_projection(values[max(0, index-8):index], "DST")
                if estimate: residuals.append(float(values[index] - estimate["projection"]))
    return models, residuals


def build_matchups(schedule, week):
    if schedule.empty or "week" not in schedule:
        return {}
    games = schedule[pd.to_numeric(schedule["week"], errors="coerce") == week]
    result = {}
    for _, game in games.iterrows():
        home, away = str(game.get("home_team") or ""), str(game.get("away_team") or "")
        if home and away:
            result[home], result[away] = f"vs {away}", f"@ {home}"
    return result


def injury_index(frame):
    if frame.empty:
        return {}
    name_col = player_name_column(frame)
    if not name_col:
        return {}
    if "week" in frame: frame = frame.sort_values("week")
    return {clean_name(row.get(name_col)): str(row.get("report_status") or row.get("practice_status") or row.get("status") or "") for _, row in frame.iterrows() if clean_name(row.get(name_col))}


def injury_multiplier(status):
    status = str(status or "").lower()
    if any(token in status for token in ("out", "injured reserve", "pup", "suspend")) or status == "ir": return 0.0
    if "doubt" in status: return .55
    if "question" in status: return .85
    return 1.0


def boom_bust(mean, sd, residuals):
    if mean is None: return None, None, None, None, "no_projection"
    sd = max(2.5, numeric(sd, mean*.30))
    floor, ceiling = max(0, mean-1.04*sd), mean+1.04*sd
    boom_line, bust_line = mean+max(5,mean*.35), max(0,mean-max(5,mean*.35))
    if len(residuals) >= 20:
        errors=np.asarray(residuals,dtype=float); boom=float(np.mean(mean+errors>=boom_line)); bust=float(np.mean(mean+errors<=bust_line)); method="empirical_position_residuals"
    else:
        distribution=NormalDist(mean,sd); boom=1-distribution.cdf(boom_line); bust=distribution.cdf(bust_line); method="normal_approximation"
    return round(floor,1),round(ceiling,1),round(boom*100),round(bust*100),method


def make_players(sleeper_players, skill_models, kicker_models, dst_models, residuals, matchups, injuries, target_week, current_week):
    results=[]
    for sleeper_id, player in sleeper_players.items():
        position=str(player.get("position") or "").upper(); position="DST" if position in {"DEF","D/ST"} else position
        name=player.get("full_name") or f"{player.get('first_name','')} {player.get('last_name','')}".strip()
        if not name or position not in POSITIONS: continue
        key=clean_name(name); team=player.get("team") or "FA"
        if position == "K": model=kicker_models.get(key); position_residuals=residuals["K"]
        elif position == "DST": model=dst_models.get(team); position_residuals=residuals["DST"]
        else: model=skill_models.get(key); position_residuals=residuals[position]
        status=(injuries.get(key) or player.get("injury_status") or "Healthy") if target_week==current_week else "Historical"
        multiplier=injury_multiplier(status) if target_week==current_week else 1.0
        projection=None if not model else round(model["projection"]*multiplier,2)
        if team=="FA": status,projection="FREE AGENT",0.0
        floor,ceiling,boom,bust,method=boom_bust(projection,None if not model else model["standardDeviation"],position_residuals)
        if team=="FA": floor,ceiling,boom,bust,method=0.0,0.0,0,100,"inactive_player"
        confidence=0 if not model else min(95,45+model["games"]*5+(10 if len(position_residuals)>=20 else 0))
        results.append({"id":str(sleeper_id),"sleeperId":str(sleeper_id),"name":name,"team":team,"pos":position,"opp":matchups.get(team,""),"injury":status,"projection":projection,"floor":floor,"ceiling":ceiling,"boom":boom,"bust":bust,"confidence":confidence,"trend":None if not model else round(model["trend"],2),"flexEligible":position in {"RB","WR","TE"},"probabilityMethod":method,"sources":{"customNflverseModel":projection},"model":model or {},"projectionWeek":target_week})
    results.sort(key=lambda item:(item["pos"],-(item["projection"] if item["projection"] is not None else -1),item["name"]))
    return results


def main():
    season,current_week,sleeper_players=load_sleeper()
    player_stats=load_frame(lambda:nfl.load_player_stats([season-1,season]),"Player stats")
    pbp=load_frame(lambda:nfl.load_pbp([season-1,season]),"Play by play")
    schedule=load_frame(lambda:nfl.load_schedules([season-1,season]),"Schedules")
    injuries=load_frame(lambda:nfl.load_injuries([season]),"Injuries")
    all_kicker=kicker_points_by_play(pbp); all_dst=dst_weekly_points(pbp,schedule); injuries_by_name=injury_index(injuries)
    week_snapshots={}
    for target_week in range(1,current_week+1):
        training=stats_before_week(player_stats,season,target_week); skill_models,residuals=build_skill_models(training)
        k_training=stats_before_week(all_kicker,season,target_week); kicker_models,k_residuals=build_kicker_models(k_training)
        d_training=stats_before_week(all_dst,season,target_week); dst_models,d_residuals=build_dst_models(d_training)
        residuals["K"],residuals["DST"]=k_residuals,d_residuals
        players=make_players(sleeper_players,skill_models,kicker_models,dst_models,residuals,build_matchups(schedule,target_week),injuries_by_name,target_week,current_week)
        week_snapshots[str(target_week)]={"week":target_week,"generatedMode":"historical_backtest" if target_week<current_week else "current","players":players}
        print(f"Week {target_week}: K models={len(kicker_models)}, DST models={len(dst_models)}")
    output={"season":season,"week":current_week,"defaultWeek":current_week,"availableWeeks":list(range(1,current_week+1)),"scoring":SCORING,"generatedAt":pd.Timestamp.utcnow().isoformat(),"isDemo":False,"providers":[{"id":"sleeper","name":"Sleeper","status":"live"},{"id":"nflverse","name":"nflverse through nflreadpy","status":"live"}],"methodology":{"kicker":"Play-by-play field goals scored 3/4/5 by distance plus one per made extra point, projected from up to eight prior games","dst":"Sacks, interceptions, lost fumbles, safeties, defensive/return touchdowns, and points-allowed bands, projected from up to eight prior games","historicalWeeks":"Each week uses only data before that week"},"supportedPositions":["QB","RB","WR","TE","K","FLX","DST"],"players":week_snapshots[str(current_week)]["players"],"weekSnapshots":week_snapshots}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True); OUTPUT.write_text(json.dumps(output,indent=2,allow_nan=False),encoding="utf-8")


if __name__=="__main__": main()
