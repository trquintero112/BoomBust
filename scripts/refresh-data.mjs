import fs from "node:fs/promises";

const key = process.env.FANTASYPROS_API_KEY;
const season = process.env.NFL_SEASON || "2026";
const now = new Date();

const season = now.getFullYear();

const nflStart = new Date(`${season}-09-01`);

const days = Math.floor(
  (now - nflStart) / (1000 * 60 * 60 * 24)
);

const week = Math.max(
  1,
  Math.min(18, Math.floor(days / 7) + 1)
);
const scoring = process.env.NFL_SCORING || "PPR";

if (!key) {
  throw new Error("FANTASYPROS_API_KEY is missing");
}

async function getFantasyPros(path, params = {}) {
  const url = new URL(
    `https://api.fantasypros.com/public/v2/json${path}`
  );

  Object.entries(params).forEach(([name, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(name, String(value));
    }
  });

  const response = await fetch(url, {
    headers: {
      "x-api-key": key,
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `FantasyPros ${response.status}: ${body.slice(0, 500)}`
    );
  }

  return response.json();
}

async function getSleeperPlayers() {
  const response = await fetch(
    "https://api.sleeper.app/v1/players/nfl"
  );

  if (!response.ok) {
    throw new Error(`Sleeper returned ${response.status}`);
  }

  return response.json();
}

function normalizePosition(value) {
  return String(value || "").toUpperCase();
}

function playerName(player) {
  return (
    player.player_name ||
    player.name ||
    `${player.first_name || ""} ${player.last_name || ""}`.trim()
  );
}

function normalizeFantasyProsPlayer(player) {
  return {
    id: String(
      player.player_id ||
      player.fantasypros_id ||
      player.sportsdata_id ||
      playerName(player)
    ),
    fantasyprosId:
      player.player_id || player.fantasypros_id || null,
    name: playerName(player),
    team:
      player.player_team_id ||
      player.team_id ||
      player.team ||
      "FA",
    pos: normalizePosition(
      player.player_position_id ||
      player.position_id ||
      player.position
    ),
    rank:
      Number(
        player.rank_ecr ||
        player.rank ||
        player.pos_rank ||
        999
      ),
    projection:
      Number(
        player.fpts ||
        player.fantasy_points ||
        player.projected_points ||
        player.points ||
        0
      ),
    floor: Number(player.floor || 0),
    ceiling: Number(player.ceiling || 0),
    injury:
      player.injury_status ||
      player.injury_designation ||
      "Healthy",
  };
}

function buildSleeperNameIndex(players) {
  const index = new Map();

  Object.entries(players).forEach(([sleeperId, player]) => {
    const name = String(
      player.full_name ||
      `${player.first_name || ""} ${player.last_name || ""}`
    )
      .trim()
      .toLowerCase();

    if (name) {
      index.set(name, {
        sleeperId,
        team: player.team || null,
        position: player.position || null,
        status: player.status || null,
        injuryStatus: player.injury_status || null,
      });
    }
  });

  return index;
}

const [projectionsResponse, rankingsResponse, sleeperPlayers] =
  await Promise.all([
    getFantasyPros(`/nfl/${season}/projections`, {
      week,
      scoring,
    }),
    getFantasyPros(`/nfl/${season}/consensus-rankings`, {
      week,
      scoring,
    }),
    getSleeperPlayers(),
  ]);

const projectionRows =
  projectionsResponse.players ||
  projectionsResponse.projections ||
  [];

const rankingRows = rankingsResponse.players || [];
const sleeperIndex = buildSleeperNameIndex(sleeperPlayers);

const rankingByFantasyProsId = new Map(
  rankingRows.map((player) => [
    String(player.player_id || player.fantasypros_id),
    player,
  ])
);

const players = projectionRows
  .map((rawPlayer) => {
    const player = normalizeFantasyProsPlayer(rawPlayer);

    const ranking =
      rankingByFantasyProsId.get(String(player.fantasyprosId)) ||
      {};

    const sleeper = sleeperIndex.get(
      player.name.toLowerCase()
    );

    const projectedPoints = player.projection;

    return {
      ...player,
      id:
        sleeper?.sleeperId ||
        `fp-${player.fantasyprosId || player.name}`,
      sleeperId: sleeper?.sleeperId || null,
      team: player.team || sleeper?.team || "FA",
      pos: player.pos || sleeper?.position || "",
      rank: Number(
        ranking.rank_ecr ||
        ranking.rank ||
        player.rank ||
        999
      ),
      injury:
        player.injury ||
        sleeper?.injuryStatus ||
        "Healthy",

      /*
       * FantasyPros projection becomes the initial primary
       * projection source.
       */
      sources: {
        fantasypros: projectedPoints,
      },

      /*
       * These are temporary estimates unless your API response
       * supplies official floor, ceiling, boom, or bust fields.
       */
      floor:
        player.floor ||
        Number((projectedPoints * 0.65).toFixed(1)),
      ceiling:
        player.ceiling ||
        Number((projectedPoints * 1.4).toFixed(1)),
      boom: Math.max(
        5,
        Math.min(60, Math.round(20 + projectedPoints * 0.6))
      ),
      bust: Math.max(
        5,
        Math.min(50, Math.round(32 - projectedPoints * 0.5))
      ),
      trend: 0,
    };
  })
  .filter(
    (player) =>
      player.name &&
      ["QB", "RB", "WR", "TE"].includes(player.pos) &&
      Number.isFinite(player.projection)
  );

const output = {
  season: Number(season),
  week: Number(week),
  scoring,
  generatedAt: new Date().toISOString(),
  isDemo: false,
  providers: [
    {
      id: "fantasypros",
      name: "FantasyPros",
      status: "live",
      role: "Weekly projections and ECR",
    },
    {
      id: "sleeper",
      name: "Sleeper",
      status: "live",
      role: "Player identity and league data",
    },
  ],
  players,
};

await fs.mkdir("public/data", { recursive: true });
await fs.writeFile(
  "public/data/latest.json",
  JSON.stringify(output, null, 2)
);

console.log(
  `Wrote ${players.length} players for Week ${week}`
);
