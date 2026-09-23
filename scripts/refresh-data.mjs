import fs from "node:fs/promises";

const key = process.env.FANTASYPROS_API_KEY;

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

const scoring = "PPR";

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
      player.player_id ||
      player.fantasypros_id ||
      null,

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

    rank: Number(
      player.rank_ecr ||
      player.rank ||
      player.pos
