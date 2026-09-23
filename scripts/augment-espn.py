import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

import requests

DATA = Path("public/data/latest.json")
LEAGUES = [
    ("Teenage Mutant Njigba Turtles", os.getenv("ESPN_LEAGUE_1", "").strip()),
    ("Never Gonna Gibbs You Up", os.getenv("ESPN_LEAGUE_2", "").strip()),
]
ESPN_S2 = os.getenv("ESPN_S2", "").strip()
ESPN_SWID = os.getenv("ESPN_SWID", "").strip()

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 fantasy-live-tool/2.0",
    "Accept": "application/json",
})

SLOT_NAMES = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DST", 17: "K",
    20: "BENCH", 21: "IR", 23: "FLEX", 24: "OP", 25: "TQB",
}

ESPN_POSITION = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST",
}

ESPN_TEAM = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC",
    13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
    19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX",
    33: "BAL", 34: "HOU",
}

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def basic_name(value):
    value = str(value or "").lower().replace("d/st", " dst ")
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def canonical_name(value):
    tokens = [token for token in basic_name(value).split() if token not in SUFFIXES]
    return " ".join(tokens)


def normalized_owner(value):
    return str(value or "").strip().strip("{}").lower()


def normalize_position(value):
    value = str(value or "").upper()
    return "DST" if value in {"DEF", "D/ST", "DST"} else value


def request_json(url, **kwargs):
    response = SESSION.get(url, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


def request_league(season, league_id):
    url = (
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
        f"seasons/{season}/segments/0/leagues/{league_id}"
    )
    params = [
        ("view", "mSettings"), ("view", "mTeam"), ("view", "mRoster"),
        ("view", "mMatchup"), ("view", "mStandings"),
    ]
    cookies = {"espn_s2": ESPN_S2, "SWID": ESPN_SWID} if ESPN_S2 and ESPN_SWID else {}
    return request_json(url, params=params, cookies=cookies)


def sleeper_indexes(sleeper_players):
    by_espn_id = {}
    by_exact = {}
    by_canonical = {}
    profiles = []

    for sleeper_id, player in sleeper_players.items():
        name = player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        position = normalize_position(player.get("position"))
        team = str(player.get("team") or "").upper()
        profile = {
            "sleeperId": str(sleeper_id),
            "name": name,
            "exact": basic_name(name),
            "canonical": canonical_name(name),
            "position": position,
            "team": team,
        }
        profiles.append(profile)

        espn_id = player.get("espn_id")
        if espn_id is not None:
            by_espn_id[str(espn_id)] = profile
        if profile["exact"]:
            by_exact.setdefault(profile["exact"], []).append(profile)
        if profile["canonical"]:
            by_canonical.setdefault(profile["canonical"], []).append(profile)

    return by_espn_id, by_exact, by_canonical, profiles


def choose_candidate(candidates, position, team):
    if not candidates:
        return None
    filtered = candidates
    if position:
        same_position = [item for item in filtered if item["position"] == position]
        if same_position:
            filtered = same_position
    if team:
        same_team = [item for item in filtered if item["team"] == team]
        if same_team:
            filtered = same_team
    return filtered[0] if len(filtered) == 1 else None


def match_sleeper_player(espn_player, indexes):
    by_espn_id, by_exact, by_canonical, profiles = indexes
    espn_id = str(espn_player.get("id")) if espn_player.get("id") is not None else ""
    name = espn_player.get("fullName") or espn_player.get("name") or ""
    position = ESPN_POSITION.get(espn_player.get("defaultPositionId"), "")
    team = ESPN_TEAM.get(espn_player.get("proTeamId"), "")

    # 1. Best match: ESPN's player ID stored in the Sleeper player dictionary.
    if espn_id and espn_id in by_espn_id:
        return by_espn_id[espn_id], "espn_id", 1.0

    # 2. Exact normalized full name, constrained by position/team if needed.
    exact = basic_name(name)
    candidate = choose_candidate(by_exact.get(exact, []), position, team)
    if candidate:
        return candidate, "exact_name", 0.99

    # 3. Suffix-insensitive matching handles Jr., Sr., II, III, IV, and V.
    canonical = canonical_name(name)
    candidate = choose_candidate(by_canonical.get(canonical, []), position, team)
    if candidate:
        return candidate, "canonical_name", 0.97

    # 4. DST records use the pro-team mapping instead of display-name spelling.
    if position == "DST" and team:
        candidates = [item for item in profiles if item["position"] == "DST" and item["team"] == team]
        if len(candidates) == 1:
            return candidates[0], "dst_team", 1.0

    # 5. Final conservative fuzzy fallback. Position and team must agree when known.
    pool = profiles
    if position:
        pool = [item for item in pool if item["position"] == position]
    if team:
        team_pool = [item for item in pool if item["team"] == team]
        if team_pool:
            pool = team_pool

    scored = sorted(
        ((SequenceMatcher(None, canonical, item["canonical"]).ratio(), item) for item in pool),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.92:
        second = scored[1][0] if len(scored) > 1 else 0.0
        if scored[0][0] - second >= 0.04:
            return scored[0][1], "fuzzy_name", round(scored[0][0], 3)

    return None, "unmatched", 0.0


def player_record(entry, indexes):
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or {}
    match, method, confidence = match_sleeper_player(player, indexes)
    full_name = player.get("fullName") or player.get("name") or ""
    slot_id = entry.get("lineupSlotId")
    return {
        "espnId": str(player.get("id")) if player.get("id") is not None else None,
        "sleeperId": match.get("sleeperId") if match else None,
        "name": full_name,
        "nameKey": basic_name(full_name),
        "canonicalName": canonical_name(full_name),
        "proTeam": ESPN_TEAM.get(player.get("proTeamId"), ""),
        "position": ESPN_POSITION.get(player.get("defaultPositionId"), ""),
        "lineupSlotId": slot_id,
        "lineupSlot": SLOT_NAMES.get(slot_id, str(slot_id)),
        "starter": slot_id not in (20, 21),
        "injuryStatus": player.get("injuryStatus") or "ACTIVE",
        "eligibleSlots": player.get("eligibleSlots") or [],
        "matchMethod": method,
        "matchConfidence": confidence,
    }


def team_record(team, indexes):
    roster = [player_record(entry, indexes) for entry in ((team.get("roster") or {}).get("entries") or [])]
    return {
        "id": team.get("id"),
        "name": f"{team.get('location', '')} {team.get('nickname', '')}".strip() or f"Team {team.get('id')}",
        "abbrev": team.get("abbrev"),
        "owners": team.get("owners") or [],
        "roster": roster,
    }


def find_my_team(teams):
    target = normalized_owner(ESPN_SWID)
    for team in teams:
        if target and any(normalized_owner(owner) == target for owner in team.get("owners", [])):
            return team.get("id")
    return None


def current_matchup(body, my_team_id, week):
    if my_team_id is None:
        return None
    for game in body.get("schedule", []):
        if int(game.get("matchupPeriodId") or 0) != int(week):
            continue
        home = (game.get("home") or {}).get("teamId")
        away = (game.get("away") or {}).get("teamId")
        if my_team_id in (home, away):
            return {"homeTeamId": home, "awayTeamId": away}
    return None


def main():
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    season, week = payload.get("season"), payload.get("week")
    sleeper_players = request_json("https://api.sleeper.app/v1/players/nfl")
    indexes = sleeper_indexes(sleeper_players)
    model_by_sleeper = {str(player.get("sleeperId") or player.get("id")): player for player in payload.get("players", [])}
    model_by_name = {canonical_name(player.get("name")): player for player in payload.get("players", [])}

    results = []
    for fallback_name, league_id in LEAGUES:
        if not league_id:
            continue
        try:
            body = request_league(season, league_id)
            teams = [team_record(team, indexes) for team in body.get("teams", [])]
            my_team_id = find_my_team(teams)
            ownership = {}
            unmatched = []

            for team in teams:
                for roster_player in team["roster"]:
                    model = None
                    if roster_player.get("sleeperId"):
                        model = model_by_sleeper.get(str(roster_player["sleeperId"]))
                    if model is None:
                        model = model_by_name.get(roster_player["canonicalName"])
                    if model:
                        roster_player["modelPlayerId"] = model.get("id")
                        roster_player["projection"] = model.get("projection")
                        roster_player["boom"] = model.get("boom")
                        roster_player["bust"] = model.get("bust")
                        roster_player["confidence"] = model.get("confidence")
                        ownership[str(model.get("id"))] = {
                            "isRostered": True,
                            "ownerTeamId": team["id"],
                            "ownerTeamName": team["name"],
                            "isMyTeam": str(team["id"]) == str(my_team_id),
                            "matchMethod": roster_player["matchMethod"],
                            "matchConfidence": roster_player["matchConfidence"],
                        }
                    else:
                        roster_player["modelPlayerId"] = None
                        unmatched.append({
                            "name": roster_player["name"],
                            "team": team["name"],
                            "espnId": roster_player["espnId"],
                            "method": roster_player["matchMethod"],
                        })

            results.append({
                "id": league_id,
                "name": body.get("settings", {}).get("name") or fallback_name,
                "scoringPeriodId": body.get("scoringPeriodId") or week,
                "myTeamId": my_team_id,
                "teams": teams,
                "ownership": ownership,
                "unmatchedPlayers": unmatched,
                "matchup": current_matchup(body, my_team_id, body.get("scoringPeriodId") or week),
                "connected": True,
            })
            print(f"ESPN league {league_id}: {len(teams)} teams, {len(ownership)} ownership matches, {len(unmatched)} unmatched")
        except Exception as exc:
            print(f"ESPN league {league_id} failed: {exc}")
            results.append({"id": league_id, "name": fallback_name, "connected": False, "error": str(exc), "teams": [], "ownership": {}})

    payload["espnLeagues"] = results
    providers = [provider for provider in payload.get("providers", []) if provider.get("id") != "espn"]
    providers.append({
        "id": "espn",
        "name": "ESPN Fantasy",
        "status": "connected" if any(item.get("connected") for item in results) else "error",
        "role": "Multi-league rosters, ownership, and matchups",
    })
    payload["providers"] = providers
    DATA.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
