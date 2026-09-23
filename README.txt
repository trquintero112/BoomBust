ESPN TWO-LEAGUE UPDATE

UPLOAD/REPLACE
1. scripts/augment-espn.py
2. .github/workflows/refresh.yml
3. src/main.jsx

KEEP
- scripts/refresh-projections.py
- requirements.txt
- src/style.css
- deploy.yml
- package.json
- index.html
- vite.config.js

REQUIRED GITHUB ACTIONS SECRETS
ESPN_LEAGUE_1 = 2062109136
ESPN_LEAGUE_2 = 991905298
ESPN_S2 = your private ESPN session cookie
ESPN_SWID = your ESPN SWID including braces if copied that way

RUN
Actions > Refresh projections and ESPN leagues > Run workflow
Then allow Deploy GitHub Pages to run, or run it manually.

FEATURES
- League selector for both leagues
- My Team roster view
- Waiver view of players not rostered in the selected league
- Weekly matchup and projected totals
- All-player view
- K, FLEX, and DST filters

OWNER MATCHING
The script matches ESPN_SWID to each team's owners list. If My Team says Not identified, verify ESPN_SWID is the SWID for the account that owns the fantasy team.
