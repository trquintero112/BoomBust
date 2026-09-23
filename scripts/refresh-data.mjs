import fs from "node:fs/promises";

const FANTASYPROS_API_KEY =
  process.env.FANTASYPROS_API_KEY;

const SCORING = "PPR";

const PRIMARY_POSITIONS = [
  "QB",
  "RB",
  "WR",
  "TE",
  "K",
  "DST",
];

const RANKING_POSITIONS = [
  "QB",
  "RB",
  "WR",
  "TE",
  "K",
  "DST",
  "FLX",
];

if (!FANTASYPROS_API_KEY) {
  throw new Error(
    "FANTASYPROS_API_KEY is missing. Add it under GitHub Settings > Secrets and variables > Actions."
  );
}

/*
 * Fetch JSON and provide a useful error message.
 */
async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);

  if (*response.ok) {
    const responseText = await response.text();

    t*row new Error(
      `Request failed ${response.status} ${response.statusText}: ${responseText.slice(
        0,
        1000
      )}`
    );
  }

  return response.json();
}

/*
 * Sleeper supplies the current NFL season and week.
 */
async function getNflState() {
  return fetchJson(
    "https://api.sleeper.app/v1/state/nfl"
  );
}

/*
 * Sleeper supplies stable player IDs and player metadata.
 */
async function getSleeperPlayers() {
  return fetchJson(
    "https://api.sleeper.app/v1/players/nfl"
  );
}

/*
 * Send an authenticated request to FantasyPros.
 */
async function getFantasyPros(
  endpointPath,
  parameters = {}
) {
  const url = new URL(
    `https://api.fantasypros.com/public/v2/json${endpointPath}`
  );

  for (const [name, value] of Object.entries(
    parameters
  )) {
    if (
      value !== undefined &&
      value !== null &&
      value !== ""
    ) {
      url.searchParams.set(
        name,
        String(value)
      );
    }
  }

  console.log(
    `Requesting FantasyPros: ${url.pathname}${url.search}`
  );

  const response = await fetch(url, {
    headers: {
      "x-api-key":
        FANTASYPROS_API_KEY,
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    const responseText =
      await response.text();

    throw new Error(
      `FantasyPros ${response.status} for ${url.pathname}${url.search}: ${responseText.slice(
        0,
        1000
      )}`
    );
  }

  return response.json();
}

/*
 * Get FantasyPros projections for one position.
 *
 * FantasyPros requires valid position values.
 * This function is called once for every primary position.
 */
async function getPositionProjections(
  season,
  week,
  position
) {
  try {
    const response =
      await getFantasyPros(
        `/nfl/${season}/projections`,
        {
          week,
          scoring: SCORING,
          position,
        }
      );

    const rows =
      response.players ||
      response.projections ||
      response.data ||
      [];

    console.log(
      `FantasyPros ${position} projections: ${rows.length}`
    );

    return rows.map((player) => ({
      ...player,
      requested_position:
        position,
    }));
  } catch (error) {
    console.error(
      `Could not retrieve ${position} projections:`,
      error.message
    );

    return [];
  }
}

/*
 * Get FantasyPros consensus rankings for one position.
 *
 * FLX is requested separately because FLX represents
 * eligible RB, WR, and TE players rather than a separate
 * NFL roster position.
 */
async function getPositionRankings(
  season,
  week,
  position
) {
  try {
    const response =
      await getFantasyPros(
        `/nfl/${season}/consensus-rankings`,
        {
          week,
          scoring: SCORING,
          position,
        }
      );

    const rows =
      response.players ||
      response.rankings ||
      response.data ||
      [];

    console.log(
      `FantasyPros ${position} rankings: ${rows.length}`
    );

    return rows.map((player) => ({
      ...player,
      requested_position:
        position,
    }));
  } catch (error) {
    console.error(
      `Could not retrieve ${position} rankings:`,
      error.message
    );

    return [];
  }
}

function normalizeText(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}

function normalizePosition(value) {
  const position = String(
    value || ""
  ).toUpperCase();

  if (
    position === "DEF" ||
    position === "D/ST"
  ) {
    return "DST";
  }

  return position;
}

function getPlayerName(player) {
  return (
    player.player_name ||
    player.full_name ||
    player.name ||
    `${player.first_name || ""} ${
      player.last_name || ""
    }`.trim()
  );
}

function getFantasyProsId(player) {
  const value =
    player.player_id ||
    player.fantasypros_id ||
    player.fp_id ||
    null;

  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return null;
  }

  return String(value);
}

function getFantasyPoints(player) {
  const candidates = [
    player.fpts,
    player.fantasy_points,
    player.projected_points,
    player.points,
    player.fantasy_pts,
    player.stats?.fpts,
    player.stats?.fantasy_points,
  ];

  for (const value of candidates) {
    const numericValue = Number(value);

    if (Number.isFinite(numericValue)) {
      return numericValue;
    }
  }

  return 0;
}

function getRank(player) {
  const candidates = [
    player.rank_ecr,
    player.ecr,
    player.rank,
    player.pos_rank,
    player.position_rank,
  ];

  for (const value of candidates) {
    const numericValue = Number(value);

    if (Number.isFinite(numericValue)) {
      return numericValue;
    }
  }

  return 999;
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

function getPlayerPosition(player) {
  return normalizePosition(
    player.player_position_id ||
      player.position_id ||
      player.position ||
      player.pos ||
      player.requested_position
  );
}

function getOpponent(player) {
  const opponent =
    player.opponent ||
    player.opponent_id ||
    player.opp ||
    player.matchup ||
    "";

  return String(opponent);
}

function getInjuryStatus(player) {
  return (
    player.injury_status ||
    player.injury_designation ||
    player.status ||
    "Healthy"
  );
}

/*
 * Create a Sleeper lookup by normalized name.
 */
function buildSleeperIndex(
  sleeperPlayers
) {
  const index = new Map();

  for (const [
    sleeperId,
    sleeperPlayer,
  ] of Object.entries(
    sleeperPlayers
  )) {
    const fullName = normalizeText(
      sleeperPlayer.full_name ||
        `${
          sleeperPlayer.first_name || ""
        } ${
          sleeperPlayer.last_name || ""
        }`
    );

    if (!fullName) {
      continue;
    }

    index.set(fullName, {
      sleeperId,
      team:
        sleeperPlayer.team || null,
      position: normalizePosition(
        sleeperPlayer.position
      ),
      injuryStatus:
        sleeperPlayer.injury_status ||
        null,
      status:
        sleeperPlayer.status || null,
    });
  }

  return index;
}

/*
 * Create a ranking lookup by FantasyPros player ID.
 */
function buildRankingIndex(
  rankingRows
) {
  const index = new Map();

  for (const ranking of rankingRows) {
    const playerId =
      getFantasyProsId(ranking);

    if (!playerId) {
      continue;
    }

    const position =
      ranking.requested_position;

    const existing =
      index.get(playerId) || {
        rankings: {},
      };

    existing.rankings[position] =
      getRank(ranking);

    existing.tier =
      ranking.tier ??
      existing.tier ??
      null;

    existing.rankMin =
      Number(
        ranking.rank_min ||
          ranking.min_rank
      ) ||
      existing.rankMin ||
      null;

    existing.rankMax =
      Number(
        ranking.rank_max ||
          ranking.max_rank
      ) ||
      existing.rankMax ||
      null;

    existing.rankAverage =
      Number(
        ranking.rank_ave ||
          ranking.avg_rank ||
          ranking.rank_average
      ) ||
      existing.rankAverage ||
      null;

    index.set(playerId, existing);
  }

  return index;
}

function calculateConfidence(
  projection,
  rank,
  floor,
  ceiling
) {
  let confidence = 65;

  if (projection > 0) {
    confidence += 10;
  }

  if (
    Number.isFinite(rank) &&
    rank < 999
  ) {
    confidence += 10;
  }

  if (
    floor > 0 &&
    ceiling > floor
  ) {
    confidence += 8;
  }

  return Math.max(
    40,
    Math.min(95, confidence)
  );
}

/*
 * These boom and bust values are model estimates.
 *
 * FantasyPros projection is the baseline.
 * Floor and ceiling are used to express the likely range.
 */
function calculateRisk(
  projectedPoints,
  floor,
  ceiling
) {
  if (
    !Number.isFinite(
      projectedPoints
    ) ||
    projectedPoints <= 0
  ) {
    return {
      boom: 5,
      bust: 50,
    };
  }

  const range = Math.max(
    1,
    ceiling - floor
  );

  const upside =
    ceiling - projectedPoints;

  const downside =
    projectedPoints - floor;

  const boom = Math.round(
    15 +
      Math.min(
        40,
        (upside / range) * 45
      )
  );

  const bust = Math.round(
    10 +
      Math.min(
        40,
        (downside / range) * 35
      )
  );

  return {
    boom: Math.max(
      5,
      Math.min(60, boom)
    ),
    bust: Math.max(
      5,
      Math.min(50, bust)
    ),
  };
}

/*
 * Load the current NFL state first.
 */
const [
  nflState,
  sleeperPlayers,
] = await Promise.all([
  getNflState(),
  getSleeperPlayers(),
]);

const season = String(
  nflState.season ||
    new Date().getFullYear()
);

const week = Math.max(
  1,
  Math.min(
    18,
    Number(nflState.week || 1)
  )
);

console.log(
  `Current NFL state: season ${season}, week ${week}`
);

/*
 * Request every primary projection position.
 */
const projectionResponses =
  await Promise.all(
    PRIMARY_POSITIONS.map(
      (position) =>
        getPositionProjections(
          season,
          week,
          position
        )
    )
  );

/*
 * Request every ranking position, including FLX.
 */
const rankingResponses =
  await Promise.all(
    RANKING_POSITIONS.map(
      (position) =>
        getPositionRankings(
          season,
          week,
          position
        )
    )
  );

const projectionRows =
  projectionResponses.flat();

const rankingRows =
  rankingResponses.flat();

if (projectionRows.length === 0) {
  throw new Error(
    "FantasyPros returned zero projection rows for QB, RB, WR, TE, K, and DST. Review the FantasyPros error messages above and confirm that the API key includes weekly projection access."
  );
}

const sleeperIndex =
  buildSleeperIndex(
    sleeperPlayers
  );

const rankingIndex =
  buildRankingIndex(
    rankingRows
  );

const includedPositions =
  new Set(PRIMARY_POSITIONS);

const players = projectionRows
  .map((rawPlayer) => {
    const name =
      getPlayerName(rawPlayer);

    const normalizedName =
      normalizeText(name);

    const fantasyprosId =
      getFantasyProsId(rawPlayer);

    const sleeper =
      sleeperIndex.get(
        normalizedName
      );

    const position =
      normalizePosition(
        getPlayerPosition(
          rawPlayer
        ) ||
          sleeper?.position
      );

    const ranking =
      fantasyprosId
        ? rankingIndex.get(
            fantasyprosId
          )
        : null;

    const positionRank =
      ranking?.rankings?.[
        position
      ] ?? 999;

    const flexRank =
      ["RB", "WR", "TE"].includes(
        position
      )
        ? ranking?.rankings?.FLX ??
          null
        : null;

    const projectedPoints =
      getFantasyPoints(
        rawPlayer
      );

    const suppliedFloor =
      Number(rawPlayer.floor);

    const suppliedCeiling =
      Number(rawPlayer.ceiling);

    const floor =
      Number.isFinite(
        suppliedFloor
      ) &&
      suppliedFloor > 0
        ? suppliedFloor
        : Number(
            (
              projectedPoints * 0.65
            ).toFixed(1)
          );

    const ceiling =
      Number.isFinite(
        suppliedCeiling
      ) &&
      suppliedCeiling > 0
        ? suppliedCeiling
        : Number(
            (
              projectedPoints * 1.4
            ).toFixed(1)
          );

    const risk =
      calculateRisk(
        projectedPoints,
        floor,
        ceiling
      );

    return {
      id:
        sleeper?.sleeperId ||
        `fp-${
          fantasyprosId ||
          normalizedName
        }`,

      fantasyprosId,
      sleeperId:
        sleeper?.sleeperId ||
        null,

      name,

      team:
        getTeam(rawPlayer) ||
        sleeper?.team ||
        "FA",

      pos: position,

      opp:
        getOpponent(rawPlayer),

      rank:
        Number(positionRank),

      flexRank:
        flexRank === null
          ? null
          : Number(flexRank),

      tier:
        ranking?.tier || null,

      rankMin:
        ranking?.rankMin || null,

      rankMax:
        ranking?.rankMax || null,

      rankAverage:
        ranking?.rankAverage ||
        null,

      projection:
        projectedPoints,

      floor,
      ceiling,

      boom: risk.boom,
      bust: risk.bust,

      injury:
        getInjuryStatus(
          rawPlayer
        ) ||
        sleeper?.injuryStatus ||
        "Healthy",

      confidence:
        calculateConfidence(
          projectedPoints,
          positionRank,
          floor,
          ceiling
        ),

      trend: 0,

      sources: {
        fantasypros:
          projectedPoints,
      },
    };
  })
  .filter((player) => {
    return (
      player.name &&
      includedPositions.has(
        player.pos
      ) &&
      Number.isFinite(
        player.projection
      )
    );
  });

/*
 * Remove duplicate player-position records.
 */
const uniquePlayerMap =
  new Map();

for (const player of players) {
  const duplicateKey =
    `${player.id}-${player.pos}`;

  const existing =
    uniquePlayerMap.get(
      duplicateKey
    );

  if (
    !existing ||
    player.projection >
      existing.projection
  ) {
    uniquePlayerMap.set(
      duplicateKey,
      player
    );
  }
}

const uniquePlayers = [
  ...uniquePlayerMap.values(),
].sort((a, b) => {
  if (a.pos !== b.pos) {
    return a.pos.localeCompare(
      b.pos
    );
  }

  return (
    b.projection -
    a.projection
  );
});

const output = {
  season: Number(season),
  week,
  scoring: SCORING,

  generatedAt:
    new Date().toISOString(),

  isDemo: false,

  providers: [
    {
      id: "fantasypros",
      name: "FantasyPros",
      status: "live",
      role:
        "Weekly projections, positional rankings, and FLX rankings",
    },
    {
      id: "sleeper",
      name: "Sleeper",
      status: "live",
      role:
        "Current NFL state and player identity",
    },
  ],

  supportedPositions: [
    "QB",
    "RB",
    "WR",
    "TE",
    "K",
    "FLX",
    "DST",
  ],

  players: uniquePlayers,
};

await fs.mkdir(
  "public/data",
  {
    recursive: true,
  }
);

await fs.writeFile(
  "public/data/latest.json",
  JSON.stringify(
    output,
    null,
    2
  )
);

console.log(
  `Successfully wrote ${uniquePlayers.length} players for NFL season ${season}, Week ${week}, ${SCORING} scoring.`
);

for (const position of PRIMARY_POSITIONS) {
  const count =
    uniquePlayers.filter(
      (player) =>
        player.pos === position
    ).length;

  console.log(
    `${position}: ${count} players`
  );
}

const flexCount =
  uniquePlayers.filter(
    (player) =>
      ["RB", "WR", "TE"].includes(
        player.pos
      ) &&
      player.flexRank !== null
  ).length;

console.log(
  `FLX-ranked players: ${flexCount}`
);
