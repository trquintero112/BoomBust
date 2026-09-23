import json, os, re
from pathlib import Path
import requests

DATA = Path('public/data/latest.json')
LEAGUES = [
    ('Teenage Mutant Njigba Turtles', os.getenv('ESPN_LEAGUE_1','').strip()),
    ('Never Gonna Gibbs You Up', os.getenv('ESPN_LEAGUE_2','').strip()),
]
ESPN_S2 = os.getenv('ESPN_S2','').strip()
ESPN_SWID = os.getenv('ESPN_SWID','').strip()
SESSION = requests.Session()
SESSION.headers.update({'User-Agent':'Mozilla/5.0 fantasy-live-tool/1.0','Accept':'application/json'})

SLOT_NAMES = {
    0:'QB', 2:'RB', 4:'WR', 6:'TE', 16:'DST', 17:'K',
    20:'BENCH', 21:'IR', 23:'FLEX', 24:'OP', 25:'TQB'
}

def clean(value):
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]+','',str(value or '').lower())).strip()

def normalized_owner(value):
    return str(value or '').strip().strip('{}').lower()

def request_league(season, league_id):
    url = f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}'
    params = [('view','mSettings'),('view','mTeam'),('view','mRoster'),('view','mMatchup'),('view','mStandings')]
    cookies = {'espn_s2':ESPN_S2,'SWID':ESPN_SWID} if ESPN_S2 and ESPN_SWID else {}
    response = SESSION.get(url,params=params,cookies=cookies,timeout=90)
    response.raise_for_status()
    return response.json()

def player_record(entry):
    pool = entry.get('playerPoolEntry') or {}
    player = pool.get('player') or {}
    full_name = player.get('fullName') or player.get('name') or ''
    slot_id = entry.get('lineupSlotId')
    return {
        'espnId': str(player.get('id')) if player.get('id') is not None else None,
        'name': full_name,
        'nameKey': clean(full_name),
        'proTeamId': player.get('proTeamId'),
        'positionId': player.get('defaultPositionId'),
        'lineupSlotId': slot_id,
        'lineupSlot': SLOT_NAMES.get(slot_id, str(slot_id)),
        'starter': slot_id not in (20,21),
        'injuryStatus': player.get('injuryStatus') or 'ACTIVE',
        'eligibleSlots': player.get('eligibleSlots') or [],
        'acquisitionType': entry.get('acquisitionType'),
    }

def team_record(team):
    roster = [player_record(e) for e in ((team.get('roster') or {}).get('entries') or [])]
    return {
        'id': team.get('id'),
        'name': f"{team.get('location','')} {team.get('nickname','')}".strip() or f"Team {team.get('id')}",
        'abbrev': team.get('abbrev'),
        'owners': team.get('owners') or [],
        'wins': (team.get('record') or {}).get('overall',{}).get('wins'),
        'losses': (team.get('record') or {}).get('overall',{}).get('losses'),
        'pointsFor': (team.get('record') or {}).get('overall',{}).get('pointsFor'),
        'roster': roster,
    }

def find_my_team(teams):
    target = normalized_owner(ESPN_SWID)
    if target:
        for team in teams:
            if any(normalized_owner(owner)==target for owner in team.get('owners',[])):
                return team.get('id')
    return None

def current_matchup(body, my_team_id, week):
    if my_team_id is None: return None
    for game in body.get('schedule',[]):
        if int(game.get('matchupPeriodId') or 0) != int(week): continue
        home = (game.get('home') or {}).get('teamId')
        away = (game.get('away') or {}).get('teamId')
        if my_team_id in (home,away):
            return {
                'homeTeamId':home, 'awayTeamId':away,
                'homePoints':(game.get('home') or {}).get('totalPoints'),
                'awayPoints':(game.get('away') or {}).get('totalPoints'),
            }
    return None

def main():
    payload = json.loads(DATA.read_text(encoding='utf-8'))
    season, week = payload.get('season'), payload.get('week')
    projections = {clean(p.get('name')):p for p in payload.get('players',[])}
    results=[]
    for fallback_name, league_id in LEAGUES:
        if not league_id: continue
        try:
            body=request_league(season,league_id)
            teams=[team_record(t) for t in body.get('teams',[])]
            my_team_id=find_my_team(teams)
            for team in teams:
                for roster_player in team['roster']:
                    model=projections.get(roster_player['nameKey'])
                    roster_player['modelPlayerId']=model.get('id') if model else None
                    roster_player['projection']=model.get('projection') if model else None
                    roster_player['boom']=model.get('boom') if model else None
                    roster_player['bust']=model.get('bust') if model else None
                    roster_player['confidence']=model.get('confidence') if model else None
            results.append({
                'id':league_id,
                'name':body.get('settings',{}).get('name') or fallback_name,
                'scoringPeriodId':body.get('scoringPeriodId') or week,
                'myTeamId':my_team_id,
                'teams':teams,
                'matchup':current_matchup(body,my_team_id,body.get('scoringPeriodId') or week),
                'connected':True,
            })
            print(f'ESPN league {league_id}: {len(teams)} teams, myTeamId={my_team_id}')
        except Exception as exc:
            print(f'ESPN league {league_id} failed: {exc}')
            results.append({'id':league_id,'name':fallback_name,'connected':False,'error':str(exc),'teams':[]})
    payload['espnLeagues']=results
    provider=[p for p in payload.get('providers',[]) if p.get('id')!='espn']
    provider.append({'id':'espn','name':'ESPN Fantasy','status':'connected' if any(x.get('connected') for x in results) else 'error','role':'Multi-league rosters and matchups'})
    payload['providers']=provider
    DATA.write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
    print(f'Added {len(results)} ESPN leagues to {DATA}')

if __name__=='__main__': main()
