CUSTOM MODEL UPDATE, NO FANTASYPROS

ADD OR REPLACE
- requirements.txt
- scripts/refresh-projections.py
- .github/workflows/refresh.yml

KEEP
- Existing website files
- .github/workflows/deploy.yml
- package.json
- src/main.jsx
- src/style.css
- index.html
- vite.config.js

AFTER SUCCESS
Delete scripts/refresh-data.mjs and any older unused refresh scripts only after this new workflow completes successfully.

RUN
Actions > Refresh custom fantasy projections > Run workflow

OPTIONAL ESPN SECRETS
- ESPN_LEAGUE_ID
- ESPN_S2
- ESPN_SWID
ESPN is used for optional league context only, not as a projection source. Never commit ESPN_S2 or ESPN_SWID to source control.

MODEL
- Sleeper supplies the player universe and current NFL week.
- nflverse through nflreadpy supplies player game stats, schedules, and injuries.
- Projection blends last-eight recency-weighted scoring, last-four scoring, and recent trend.
- Boom/bust uses actual historical model errors by position when there is enough history.
- Full PPR is hard-coded.

LIMITATION
This is a custom historical model, not a multi-vendor market consensus. It deliberately removes FantasyPros and does not scrape projection sites.
