import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Search,
  RefreshCw,
  Zap,
  TrendingUp,
  TrendingDown,
} from "lucide-react";
import "./style.css";

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "FLX", "DST"];

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value, decimals = 1) {
  const number = finiteNumber(value);
  return number === null ? "N/A" : number.toFixed(decimals);
}

function formatPercent(value) {
  const number = finiteNumber(value);
  return number === null ? "N/A" : `${Math.round(number)}%`;
}

function App() {
  const [data, setData] = useState(null);
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState("ALL");
  const [sort, setSort] = useState("projection");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setBusy(true);
    setError("");

    try {
      const response = await fetch(`./data/latest.json?v=${Date.now()}`, {
        cache: "no-store",
      });

      if (!response.ok) {
        throw new Error(`Data request failed with status ${response.status}`);
      }

      const payload = await response.json();
      setData(payload);
    } catch (loadError) {
      setError(loadError.message || "Unable to load projection data.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const players = useMemo(() => {
    if (!data?.players) return [];

    return data.players
      .map((player) => ({
        ...player,
        projectionValue: finiteNumber(player.projection),
        boomValue: finiteNumber(player.boom),
        bustValue: finiteNumber(player.bust),
        trendValue: finiteNumber(player.trend),
      }))
      .filter((player) => {
        const positionMatch =
          position === "ALL" ||
          (position === "FLX"
            ? Boolean(player.flexEligible) || ["RB", "WR", "TE"].includes(player.pos)
            : player.pos === position);

        const searchMatch = `${player.name || ""} ${player.team || ""} ${
          player.pos || ""
        }`
          .toLowerCase()
          .includes(query.toLowerCase());

        return positionMatch && searchMatch;
      })
      .sort((a, b) => {
        if (sort === "boom") {
          return (b.boomValue ?? -1) - (a.boomValue ?? -1);
        }

        if (sort === "bust") {
          return (a.bustValue ?? 101) - (b.bustValue ?? 101);
        }

        if (sort === "name") {
          return String(a.name || "").localeCompare(String(b.name || ""));
        }

        return (b.projectionValue ?? -1) - (a.projectionValue ?? -1);
      });
  }, [data, query, position, sort]);

  if (!data && !error) {
    return <div className="loading">Loading projections...</div>;
  }

  return (
    <>
      <header>
        <div className="brand">
          <span>
            <Zap />
          </span>
          <div>
            <h1>BOOM/BUST LAB</h1>
            <small>Custom nflverse projection model</small>
          </div>
        </div>

        <button className="primary" onClick={load} disabled={busy}>
          <RefreshCw className={busy ? "spin" : ""} />
          {busy ? "Refreshing" : "Refresh"}
        </button>
      </header>

      <main>
        {error && <div className="notice">{error}</div>}

        {data && (
          <>
            {data.isDemo && (
              <div className="notice">
                Demo data is active. Run the refresh workflow to publish live data.
              </div>
            )}

            <section className="cards">
              <Card label="Players" value={players.length} />
              <Card label="Week" value={data.week ?? "N/A"} />
              <Card label="Scoring" value={data.scoring ?? "N/A"} />
              <Card label="Sources" value={data.providers?.length ?? 0} />
            </section>

            <section className="tools">
              <label>
                <Search />
                <input
                  placeholder="Search players"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>

              <div>
                {POSITIONS.map((item) => (
                  <button
                    key={item}
                    className={position === item ? "active" : ""}
                    onClick={() => setPosition(item)}
                  >
                    {item}
                  </button>
                ))}

                <select value={sort} onChange={(event) => setSort(event.target.value)}>
                  <option value="projection">Projected points</option>
                  <option value="boom">Boom %</option>
                  <option value="bust">Lowest bust %</option>
                  <option value="name">Player name</option>
                </select>
              </div>
            </section>

            <section className="table">
              <table>
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>Matchup</th>
                    <th>Projection</th>
                    <th>Floor / Ceiling</th>
                    <th>Boom</th>
                    <th>Bust</th>
                    <th>Trend</th>
                    <th>Confidence</th>
                    <th>Injury</th>
                  </tr>
                </thead>

                <tbody>
                  {players.map((player) => {
                    const trend = player.trendValue;

                    return (
                      <tr key={`${player.id}-${player.pos}`}>
                        <td>
                          <strong>{player.name}</strong>
                          <small>
                            {player.pos} · {player.team}
                            {player.flexEligible ? " · FLX eligible" : ""}
                          </small>
                        </td>
                        <td>{player.opp || "N/A"}</td>
                        <td className="points">
                          {formatNumber(player.projectionValue)}
                        </td>
                        <td>
                          {formatNumber(player.floor)} / {formatNumber(player.ceiling)}
                        </td>
                        <td className="boom">{formatPercent(player.boomValue)}</td>
                        <td className="bust">{formatPercent(player.bustValue)}</td>
                        <td className={trend !== null && trend >= 0 ? "boom" : "bust"}>
                          {trend === null ? (
                            "N/A"
                          ) : (
                            <>
                              {trend >= 0 ? <TrendingUp /> : <TrendingDown />}
                              {Math.abs(trend).toFixed(2)}
                            </>
                          )}
                        </td>
                        <td>{formatPercent(player.confidence)}</td>
                        <td>{player.injury || "Healthy"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          </>
        )}
      </main>
    </>
  );
}

function Card({ label, value }) {
  return (
    <div>
      <small>{label}</small>
      <b>{value}</b>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
