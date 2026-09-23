import fs from "node:fs/promises";

const FANTASYPROS_API_KEY = process.env.FANTASYPROS_API_KEY;
const SCORING = "PPR";
const PROJECTION_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"];
const RANKING_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST", "FLX"];

if (!FANTASYPROS_API_KEY) {
  throw new Error(
    "FANTASYPROS_API_KEY is missing. Add it under GitHub Settings > Secrets and variables > Actions."
  );
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);

  if (!response.ok) {
    const responseText = await response.text();
    throw new Error(
      `Request failed ${response.status} ${response.statusText}: ${responseText.slice(0, 1000)}`
    );
  }

  return response.json();
}

async function getNflState() {
  return fetchJson("https://api.sleeper.app/v1/state/nfl");
}

async function getSleeperPlayers() {
  return fetchJson("https://api.sleeper.app/v1/players/nfl");
}

async function getFantasyPros(endpointPath, parameters = {}) {
  const url = new URL(
    `https://api.fantasypros.com/public/v2/json${endpointPath}`
  );

  for (const [name, value] of Object.entries(parameters)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(name, String(value));
    }
  }

  console.log(`Requesting FantasyPros: ${url.pathname}${url.search}`);

  const response = await fetch(url, {
    headers: {
      "x-api-key": FANTASYPROS_API_KEY,
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    const responseText = await response.text();
    throw new Error(
      `FantasyPros ${response.status} for ${url.pathname}${url.search}: ${responseText.slice(0, 1000)}`
    );
  }

  return response.json();
}

async function getPositionProjections(season, week, position) {
  try {
    const response = await getFantasyPros(`/nfl/${season}/projections`, {
      week,
      scoring: SCORING,
      position,
    });

    const rows =
      response.players || response.projections || response.data || [];

    console.log(`${position} projections: ${rows.length}`);
    return rows.map((player) => ({ ...player, requested_position: position }));
  } catch (error) {
    console.error(`${position} projections failed: ${error.message}`);
    return [];
  }
}

async function getPositionRankings(season, week, position) {
  try {
    const response = await getFantasyPros(
      `/nfl/${season}/consensus-rankings`,
      { week, scoring: SCORING, position }
    );

    const rows = response.players || response.rankings || response.data || [];
    console.log(`${position} rankings: ${rows.length}`);
    return rows.map((player) => ({ ...player, requested_position: position }));
  } catch (error) {
    console.error(`${position} rankings failed: ${error.message}`);
    return [];
  }
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizePosition(value) {
  const position = String(value || "").toUpperCase();
  if (position === "DEF" || position === "D/ST") return "DST";
  return position;
}

function getPlayerName(player) {
  return (
    player.player_name ||
    player.full_name ||
    player.name ||
    `${player.first_name || ""} ${player.last_name || ""}`.trim()
  );
}

function getFantasyProsId(player) {
  const value =
    player.player_id || player.fantasypros_id || player.fp_id || null;
  return value === null || value === undefined || value === ""
    ? null
    : String(value);
}

function firstNumber(values, fallback = 0) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return fallback;
}

function getFantasyPoints(player) {
  return firstNumber([
    player.fpts,
    player.fantasy_points,
    player.projected_points,
    player.points,
    player.fantasy_pts,
    player.stats?.fpts,
    player.stats?.fantasy_points,
  ]);
}

function getRank(player) {
  return firstNumber(
    [
      player.rank_ecr,
      player.ecr,
      player.rank,
      player.pos_rank,
      player.position_rank,
    ],
    999
  );
}

function getTeam(player) {
  return (
    player.player_team_id ||
    player.team_id ||
    player.team ||
    player.team_abbr ||
    "FA"
  );
}

function getPosition(player) {
  return normalizePosition(
    player.player_position_id ||
      player.position_id ||
      player.position ||
      player.pos ||
      player.requested_position
  );
}

function buildSleeperIndex(sleeperPlayers) {
  const index = new Map();

  for (const [sleeperId, player] of Object.entries(sleeperPlayers)) {
    const name = normalizeText(
      player.full_name ||
        `${player.first_name || ""} ${player.last_name || ""}`
    );

    if (name) {
      index.set(name, {
        sleeperId,
        team: player.team || null,
        position: normalizePosition(player.position),
        injuryStatus: player.injury_status || null,
      });
    }
  }

  return index;
}

function buildRankingIndex(rows) {
  const index = new Map();

  for (const row of rows) {
    const id = getFantasyProsId(row);
    if (!id) continue;

    const current = index.get(id) || { rankings: {} };
    current.rankings[row.requested_position] = getRank(row);
    current.tier = row.tier ?? current.tier ?? null;
    index.set(id, current);
  }

  return index;
}

function calculateRisk(projection, floor, ceiling) {
  if (!Number.isFinite(projection) || projection <= 0) {
    return { boom: 5, bust: 50 };
  }

  const range = Math.max(1, ceiling - floor);
  const upside = ceiling - projection;
  const downside = projection - floor;

  return {
    boom: Math.max(
      5,
      Math.min(60, Math.round(15 + Math.min(40, (upside / range) * 45)))
    ),
    bust: Math.max(
      5,
      Math.min(50, Math.round(10 + Math.min(40, (downside / range) * 35)))
    ),
  };
}

const [nflState, sleeperPlayers] = await Promise.all([
  getNflState(),
  getSleeperPlayers(),
]);

const season = String(nflState.season || new Date().getFullYear());
const week = Math.max(1, Math.min(18, Number(nflState.week || 1)));

console.log(`NFL season ${season}, week ${week}, scoring ${SCORING}`);

const projectionRows = (
  await Promise.all(
    PROJECTION_POSITIONS.map((position) =>
      getPositionProjections(season, week, position)
    )
  )
).flat();

const rankingRows = (
  await Promise.all(
    RANKING_POSITIONS.map((position) =>
      getPositionRankings(season, week, position)
    )
  )
).flat();

if (projectionRows.length === 0) {
  throw new Error(
    "FantasyPros returned zero projections. Check the workflow log and confirm the API key allows weekly projection access."
  );
}

const sleeperIndex = buildSleeperIndex(sleeperPlayers);
const rankingIndex = buildRankingIndex(rankingRows);
const allowedPositions = new Set(PROJECTION_POSITIONS);

const normalizedPlayers = projectionRows
  .map((rawPlayer) => {
    const name = getPlayerName(rawPlayer);
    const normalizedName = normalizeText(name);
    const fantasyprosId = getFantasyProsId(rawPlayer);
    const sleeper = sleeperIndex.get(normalizedName);
    const position = normalizePosition(
      getPosition(rawPlayer) || sleeper?.position
    );
    const ranking = fantasyprosId ? rankingIndex.get(fantasyprosId) : null;
    const projection = getFantasyPoints(rawPlayer);
    const floor =
      Number(rawPlayer.floor) > 0
        ? Number(rawPlayer.floor)
        : Number((projection * 0.65).toFixed(1));
    const ceiling =
      Number(rawPlayer.ceiling) > 0
        ? Number(rawPlayer.ceiling)
        : Number((projection * 1.4).toFixed(1));
    const risk = calculateRisk(projection, floor, ceiling);

    return {
      id: sleeper?.sleeperId || `fp-${fantasyprosId || normalizedName}`,
      fantasyprosId,
      sleeperId: sleeper?.sleeperId || null,
      name,
      team: getTeam(rawPlayer) || sleeper?.team || "FA",
      pos: position,
      opp: String(
        rawPlayer.opponent || rawPlayer.opponent_id || rawPlayer.opp || ""
      ),
      rank: Number(ranking?.rankings?.[position] ?? 999),
      flexRank: ["RB", "WR", "TE"].includes(position)
        ? Number(ranking?.rankings?.FLX ?? 999)
        : null,
      tier: ranking?.tier ?? null,
      projection,
      floor,
      ceiling,
      boom: risk.boom,
      bust: risk.bust,
      injury:
        rawPlayer.injury_status ||
        rawPlayer.injury_designation ||
        sleeper?.injuryStatus ||
        "Healthy",
      confidence: Math.min(
        95,
        65 + (projection > 0 ? 10 : 0) + (ranking ? 10 : 0)
      ),
      trend: 0,
      sources: { fantasypros: projection },
    };
  })
  .filter(
    (player) =>
      player.name &&
      allowedPositions.has(player.pos) &&
      Number.isFinite(player.projection)
  );

const uniquePlayers = [
  ...new Map(
    normalizedPlayers.map((player) => [`${player.id}-${player.pos}`, player])
  ).values(),
].sort((a, b) => {
  if (a.pos !== b.pos) return a.pos.localeCompare(b.pos);
  return b.projection - a.projection;
});

const output = {
  season: Number(season),
  week,
  scoring: SCORING,
  generatedAt: new Date().toISOString(),
  isDemo: false,
  providers: [
    {
      id: "fantasypros",
      name: "FantasyPros",
      status: "live",
      role: "Weekly projections and rankings",
    },
    {
      id: "sleeper",
      name: "Sleeper",
      status: "live",
      role: "NFL state and player identity",
    },
  ],
  supportedPositions: ["QB", "RB", "WR", "TE", "K", "FLX", "DST"],
  players: uniquePlayers,
};

await fs.mkdir("public/data", { recursive: true });
await fs.writeFile(
  "public/data/latest.json",
  JSON.stringify(output, null, 2)
);

console.log(
  `Wrote ${uniquePlayers.length} players for ${season} Week ${week} ${SCORING}`
);

for (const position of PROJECTION_POSITIONS) {
  console.log(
    `${position}: ${uniquePlayers.filter((player) => player.pos === position).length}`
  );
}

console.log(
  `FLX ranked: ${uniquePlayers.filter((player) => player.flexRank < 999).length}`
);
