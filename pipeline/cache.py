"""The raw cache: a SQLite file holding EIA's data exactly as fetched.

Only raw values live here. Anything computed (totals, shares, flags) is derived later by
process.py and is never written back into this database.

Re-running a fetch is safe: rows are keyed by (region, period, plant, fuel, prime mover)
and *upserted* - a row that already exists is updated in place rather than duplicated,
which also picks up EIA's revisions to past months.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_facility_fuel (
    region         TEXT NOT NULL,   -- which region's config fetched this row (e.g. 'oahu')
    period         TEXT NOT NULL,   -- month, 'YYYY-MM'
    plant_code     TEXT NOT NULL,
    plant_name     TEXT,
    fuel_code      TEXT NOT NULL,   -- EIA fuel2002 code; 'ALL' = plant total
    prime_mover    TEXT NOT NULL,
    state          TEXT,
    generation_mwh REAL,            -- NULL when EIA reports no value
    fetched_at     TEXT NOT NULL,   -- when this row was last fetched (UTC, ISO 8601)
    PRIMARY KEY (region, period, plant_code, fuel_code, prime_mover)
);

-- Tier 2 (hourly, modeled). One year of hours, keyed by month/day/hour in Hawaiʻi standard time.
CREATE TABLE IF NOT EXISTS raw_pvwatts_hourly (
    region     TEXT NOT NULL,
    month      INTEGER NOT NULL,
    day        INTEGER NOT NULL,
    hour       INTEGER NOT NULL,   -- hour beginning, 0-23
    ac_w       REAL NOT NULL,      -- watts produced by a 1 MW reference solar system
    poa_wm2    REAL NOT NULL,      -- sunlight on the panel plane, W/m2
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (region, month, day, hour)
);

-- EIA's estimate of statewide rooftop (small-scale) solar, by month. Used to size the modeled rooftop solar.
CREATE TABLE IF NOT EXISTS raw_small_scale_solar (
    region         TEXT NOT NULL,
    period         TEXT NOT NULL,   -- 'YYYY-MM'
    state          TEXT NOT NULL,
    generation_mwh REAL NOT NULL,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (region, period)
);

CREATE TABLE IF NOT EXISTS raw_building_load_hourly (
    region        TEXT NOT NULL,
    sector        TEXT NOT NULL,   -- 'residential' or 'commercial'
    building_type TEXT NOT NULL,
    month         INTEGER NOT NULL,
    day           INTEGER NOT NULL,
    hour          INTEGER NOT NULL,
    total_kwh     REAL NOT NULL,   -- electricity used in the hour by all buildings of this type
    pv_kwh        REAL NOT NULL,   -- rooftop solar they produced (residential only, else 0)
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (region, sector, building_type, month, day, hour)
);

-- Tier 3: curtailment as reported by Hawaiian Electric (rows exactly as in its Excel files).
CREATE TABLE IF NOT EXISTS raw_heco_curtailment (
    region     TEXT NOT NULL,
    dataset    TEXT NOT NULL,   -- 'quarterly_totals', 'quarterly_by_reason' or 'annual_by_reason'
    period     TEXT NOT NULL,   -- '2024-Q1' or '2024'
    series     TEXT NOT NULL,   -- e.g. 'delivered_mwh', 'curtailed_mwh', 'oversupply_mwh'
    value      REAL NOT NULL,   -- MWh
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (region, dataset, period, series)
);

-- Power-plant map / battery fleet: one row per generator, from EIA's monthly inventory (EIA-860M).
-- The whole file is one snapshot; each fetch replaces the region's rows (a unit moves from
-- 'operating' to 'retired' between releases, so rows are not merged).
CREATE TABLE IF NOT EXISTS raw_eia860m_generator (
    region        TEXT NOT NULL,
    sheet         TEXT NOT NULL,   -- 'operating', 'retired' or 'planned'
    plant_code    TEXT NOT NULL,
    generator_id  TEXT NOT NULL,
    plant_name    TEXT,
    owner         TEXT,
    sector        TEXT,
    county        TEXT,
    technology    TEXT,
    energy_source TEXT,
    prime_mover   TEXT,
    nameplate_mw  REAL,
    energy_mwh    REAL,            -- batteries only
    status        TEXT,
    online_year   INTEGER,
    online_month  INTEGER,
    retired_year  INTEGER,
    retired_month INTEGER,
    lat           REAL,
    lon           REAL,
    source_file   TEXT,
    as_of         TEXT,            -- the month of the inventory file, 'YYYY-MM'
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (region, sheet, plant_code, generator_id)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    region       TEXT NOT NULL,
    route        TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    rows_fetched INTEGER NOT NULL,
    rows_new     INTEGER NOT NULL,
    rows_changed INTEGER NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_facility_fuel(conn: sqlite3.Connection, region: str, route: str, rows: list[dict]) -> dict:
    """Store fetched rows. Returns counts: {'fetched', 'new', 'changed'}."""
    fetched_at = _now()
    new = changed = 0
    with conn:  # one transaction: either everything is stored or nothing is
        for r in rows:
            key = (region, r["period"], r["plant_code"], r["fuel_code"], r["prime_mover"])
            existing = conn.execute(
                "SELECT generation_mwh FROM raw_facility_fuel "
                "WHERE region=? AND period=? AND plant_code=? AND fuel_code=? AND prime_mover=?",
                key,
            ).fetchone()
            if existing is None:
                new += 1
            elif existing["generation_mwh"] != r["generation_mwh"]:
                changed += 1
            conn.execute(
                """
                INSERT INTO raw_facility_fuel
                    (region, period, plant_code, plant_name, fuel_code, prime_mover, state,
                     generation_mwh, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (region, period, plant_code, fuel_code, prime_mover) DO UPDATE SET
                    plant_name     = excluded.plant_name,
                    state          = excluded.state,
                    generation_mwh = excluded.generation_mwh,
                    fetched_at     = excluded.fetched_at
                """,
                (*key[:3], r["plant_name"], *key[3:], r["state"], r["generation_mwh"], fetched_at),
            )
        conn.execute(
            "INSERT INTO fetch_log (region, route, fetched_at, rows_fetched, rows_new, rows_changed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (region, route, fetched_at, len(rows), new, changed),
        )
    return {"fetched": len(rows), "new": new, "changed": changed}


def read_facility_fuel(conn: sqlite3.Connection, region: str) -> list[dict]:
    cur = conn.execute(
        "SELECT region, period, plant_code, plant_name, fuel_code, prime_mover, generation_mwh "
        "FROM raw_facility_fuel WHERE region = ? ORDER BY period, plant_code, fuel_code",
        (region,),
    )
    return [dict(row) for row in cur]


def _upsert_rows(
    conn: sqlite3.Connection,
    table: str,
    key_cols: tuple[str, ...],
    value_cols: tuple[str, ...],
    rows: list[dict],
) -> dict:
    """Generic upsert (used by every table but the Tier 1 one). `rows` hold every key and value column."""
    fetched_at = _now()
    where = " AND ".join(f"{c}=?" for c in key_cols)
    all_cols = key_cols + value_cols + ("fetched_at",)
    updates = ", ".join(f"{c}=excluded.{c}" for c in value_cols + ("fetched_at",))
    insert = (
        f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({', '.join('?' * len(all_cols))}) "
        f"ON CONFLICT ({', '.join(key_cols)}) DO UPDATE SET {updates}"
    )
    new = changed = 0
    with conn:
        for r in rows:
            key = tuple(r[c] for c in key_cols)
            values = tuple(r[c] for c in value_cols)
            existing = conn.execute(
                f"SELECT {', '.join(value_cols)} FROM {table} WHERE {where}", key
            ).fetchone()
            if existing is None:
                new += 1
            elif tuple(existing) != values:
                changed += 1
            conn.execute(insert, key + values + (fetched_at,))
    return {"fetched": len(rows), "new": new, "changed": changed}


def upsert_pvwatts_hourly(conn: sqlite3.Connection, region: str, rows: list[dict]) -> dict:
    rows = [{**r, "region": region} for r in rows]
    return _upsert_rows(
        conn, "raw_pvwatts_hourly", ("region", "month", "day", "hour"), ("ac_w", "poa_wm2"), rows
    )


def read_pvwatts_hourly(conn: sqlite3.Connection, region: str) -> list[dict]:
    cur = conn.execute(
        "SELECT month, day, hour, ac_w, poa_wm2 FROM raw_pvwatts_hourly "
        "WHERE region = ? ORDER BY month, day, hour",
        (region,),
    )
    return [dict(row) for row in cur]


def upsert_small_scale_solar(conn: sqlite3.Connection, region: str, rows: list[dict]) -> dict:
    rows = [{**r, "region": region} for r in rows]
    return _upsert_rows(conn, "raw_small_scale_solar", ("region", "period"), ("state", "generation_mwh"), rows)


def read_small_scale_solar(conn: sqlite3.Connection, region: str) -> dict[str, float]:
    """period ('YYYY-MM') -> statewide rooftop solar estimate in MWh."""
    cur = conn.execute("SELECT period, generation_mwh FROM raw_small_scale_solar WHERE region = ?", (region,))
    return {row["period"]: row["generation_mwh"] for row in cur}


def upsert_heco_curtailment(conn: sqlite3.Connection, region: str, fetched: dict[str, list[dict]]) -> dict:
    """Store what fetch_heco.fetch_curtailment returned."""
    rows = [{"region": region, "dataset": "quarterly_totals", **r} for r in fetched["quarterly_totals"]]
    rows += [
        {"region": region, "dataset": f"{r['frequency']}_by_reason", "period": r["period"],
         "series": r["series"], "value": r["value"]}
        for r in fetched["by_reason"]
    ]
    return _upsert_rows(
        conn, "raw_heco_curtailment", ("region", "dataset", "period", "series"), ("value",), rows
    )


def read_heco_curtailment(conn: sqlite3.Connection, region: str) -> list[dict]:
    cur = conn.execute(
        "SELECT dataset, period, series, value FROM raw_heco_curtailment WHERE region = ? "
        "ORDER BY dataset, period, series",
        (region,),
    )
    return [dict(row) for row in cur]


GENERATOR_COLUMNS = (
    "plant_name", "owner", "sector", "county", "technology", "energy_source", "prime_mover", "nameplate_mw",
    "energy_mwh", "status", "online_year", "online_month", "retired_year", "retired_month", "lat", "lon",
)


def replace_generators(conn: sqlite3.Connection, region: str, inventory: dict) -> dict:
    """Replace a region's generator rows with a new EIA-860M snapshot. Returns {'fetched', 'new', 'changed'}
    compared with the snapshot being replaced (so a rerun of an unchanged file reports 0 new, 0 changed)."""
    fetched_at = _now()
    key = lambda r: (r["sheet"], r["plant_code"], r["generator_id"])
    existing = {
        (row["sheet"], row["plant_code"], row["generator_id"]): tuple(row[c] for c in GENERATOR_COLUMNS)
        for row in conn.execute("SELECT * FROM raw_eia860m_generator WHERE region = ?", (region,))
    }
    # A generator can appear twice in a sheet (a re-registered unit); the last row wins, as in the file's order.
    rows = {key(r): r for r in inventory["rows"]}
    new = sum(1 for k in rows if k not in existing)
    changed = sum(1 for k, r in rows.items() if k in existing and existing[k] != tuple(r[c] for c in GENERATOR_COLUMNS))
    with conn:
        conn.execute("DELETE FROM raw_eia860m_generator WHERE region = ?", (region,))
        conn.executemany(
            f"INSERT INTO raw_eia860m_generator (region, sheet, plant_code, generator_id, {', '.join(GENERATOR_COLUMNS)}, "
            f"source_file, as_of, fetched_at) VALUES (?, ?, ?, ?, {', '.join('?' * len(GENERATOR_COLUMNS))}, ?, ?, ?)",
            [
                (region, r["sheet"], r["plant_code"], r["generator_id"], *(r[c] for c in GENERATOR_COLUMNS),
                 inventory["source_file"], inventory["as_of"], fetched_at)
                for r in rows.values()
            ],
        )
    return {"fetched": len(rows), "new": new, "changed": changed}


def read_generators(conn: sqlite3.Connection, region: str) -> dict:
    """The cached snapshot: {"source_file", "as_of", "rows": [...]} ('rows' empty if nothing is cached)."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM raw_eia860m_generator WHERE region = ? ORDER BY plant_code, sheet, generator_id", (region,))]
    return {
        "source_file": rows[0]["source_file"] if rows else None,
        "as_of": rows[0]["as_of"] if rows else None,
        "rows": rows,
    }


def upsert_building_load_hourly(
    conn: sqlite3.Connection, region: str, sector: str, building_type: str, rows: list[dict]
) -> dict:
    rows = [{**r, "region": region, "sector": sector, "building_type": building_type} for r in rows]
    return _upsert_rows(
        conn,
        "raw_building_load_hourly",
        ("region", "sector", "building_type", "month", "day", "hour"),
        ("total_kwh", "pv_kwh"),
        rows,
    )


def read_building_load_hourly(conn: sqlite3.Connection, region: str) -> list[dict]:
    cur = conn.execute(
        "SELECT sector, building_type, month, day, hour, total_kwh, pv_kwh FROM raw_building_load_hourly "
        "WHERE region = ? ORDER BY sector, building_type, month, day, hour",
        (region,),
    )
    return [dict(row) for row in cur]


def has_building_load(conn: sqlite3.Connection, region: str, sector: str, building_type: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM raw_building_load_hourly WHERE region=? AND sector=? AND building_type=? LIMIT 1",
            (region, sector, building_type),
        ).fetchone()
        is not None
    )


def has_pvwatts(conn: sqlite3.Connection, region: str) -> bool:
    return conn.execute("SELECT 1 FROM raw_pvwatts_hourly WHERE region=? LIMIT 1", (region,)).fetchone() is not None


def log_fetch(conn: sqlite3.Connection, region: str, route: str, counts: dict) -> None:
    with conn:
        conn.execute(
            "INSERT INTO fetch_log (region, route, fetched_at, rows_fetched, rows_new, rows_changed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (region, route, _now(), counts["fetched"], counts["new"], counts["changed"]),
        )


def cached_row_count(path: Path, region: str) -> int:
    """How many raw rows are cached for a region (0 if the database doesn't exist yet)."""
    if not path.exists():
        return 0
    with closing(connect(path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM raw_facility_fuel WHERE region = ?", (region,)).fetchone()[0]
