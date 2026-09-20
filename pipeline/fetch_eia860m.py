"""Fetch EIA's monthly generator inventory (Form EIA-860M) and read one region's units out of it.

What EIA-860 is
---------------
Form EIA-860 is EIA's inventory of the country's power plants. A plant must report if its generators
add up to 1 megawatt (MW) or more of nameplate capacity AND are connected to the local or regional
electric grid (able to draw power from it or deliver power to it). The plant's operator files it; there
is an annual form and a monthly update (EIA-860M). It records each generator ("unit"): its size, fuel
and technology, when it entered service, when it was retired, its location and, for batteries, how much
energy they can store. So it does not include rooftop solar or other small systems, generators that are
not connected to the grid, or anything that closed before EIA's records begin (see the README).

This module only fetches and parses. It never touches the database.
"""

import io
import time
from datetime import date

import requests
from openpyxl import load_workbook

from .config import Eia860m, Region
from .http import MAX_ATTEMPTS

MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
SHEETS = {"Operating": "operating", "Retired": "retired", "Planned": "planned"}
XLSX_MAGIC = b"PK"  # every .xlsx file is a zip archive and starts with these two bytes


class Eia860Error(Exception):
    """The generator inventory could not be found, downloaded or understood."""


def to_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def to_int(value) -> int | None:
    n = to_number(value)
    return int(n) if n is not None else None


def _first(row: dict, *columns: str):
    """The first of these columns that has a value (the Planned sheet names its date columns differently)."""
    for c in columns:
        v = row.get(c)
        if v is not None and str(v).strip() != "":
            return v
    return None


def candidate_files(today: date, months_back: int) -> list[tuple[str, str]]:
    """(label, file name) for this month and the months before it, newest first: ('july 2026', 'july_generator2026.xlsx')."""
    out = []
    year, month = today.year, today.month
    for _ in range(months_back + 1):
        name = MONTH_NAMES[month - 1]
        out.append((f"{year}-{month:02d}", f"{name}_generator{year}.xlsx"))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return out


def _download(url: str, session: requests.Session) -> bytes | None:
    """The file's bytes, or None if there is no such file. EIA answers a missing file with a normal-looking
    HTML page (status 200), so the content, not the status code, decides."""
    last_problem = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(url, timeout=120)
        except requests.RequestException as exc:
            last_problem = f"network error ({type(exc).__name__})"
        else:
            if resp.status_code == 404:
                return None
            if resp.status_code == 200:
                return resp.content if resp.content[:2] == XLSX_MAGIC else None
            if resp.status_code == 429 or resp.status_code >= 500:
                last_problem = f"HTTP {resp.status_code}"
            else:
                raise Eia860Error(f"HTTP {resp.status_code} downloading {url.rsplit('/', 1)[-1]}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(2**attempt)
    raise Eia860Error(f"Giving up on {url.rsplit('/', 1)[-1]} after {MAX_ATTEMPTS} attempts: {last_problem}")


def parse_workbook(content: bytes, state: str, county: str) -> list[dict]:
    """Every unit in the region, from the workbook's Operating, Retired and Planned sheets.

    Rows: sheet ('operating'/'retired'/'planned'), plant_code, plant_name, owner, sector, county, generator_id,
    technology, energy_source, prime_mover, nameplate_mw, energy_mwh (batteries), status, online_year/month,
    retired_year/month, lat, lon. For a planned unit the online month is the operator's expected in-service month
    (the Planned sheet's "Planned Operation" columns), and 'status' says how far along it is.
    """
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types for a bad file
        raise Eia860Error(f"Could not read the Excel file ({type(exc).__name__}).") from None

    out: list[dict] = []
    for sheet_name, sheet_key in SHEETS.items():
        if sheet_name not in wb.sheetnames:
            raise Eia860Error(f"Sheet '{sheet_name}' not found; the workbook has {wb.sheetnames}.")
        rows = wb[sheet_name].iter_rows(values_only=True)
        header = None
        for i, row in enumerate(rows):
            if header is None:
                if row and "Plant ID" in [str(c) for c in row if c is not None]:
                    header = [str(c) if c is not None else "" for c in row]
                elif i > 8:
                    raise Eia860Error(f"Unexpected layout: no header row found on the '{sheet_name}' sheet.")
                continue
            if not row or row[0] is None:
                continue
            r = dict(zip(header, row))
            try:
                if str(r["Plant State"]).strip() != state or str(r["County"]).strip().lower() != county.lower():
                    continue
                out.append(
                    {
                        "sheet": sheet_key,
                        "plant_code": str(r["Plant ID"]).strip(),
                        "plant_name": str(r["Plant Name"]).strip(),
                        "owner": str(r["Entity Name"]).strip(),
                        "sector": str(r["Sector"]).strip(),
                        "county": str(r["County"]).strip(),
                        "generator_id": str(r["Generator ID"]).strip(),
                        "technology": str(r["Technology"]).strip(),
                        "energy_source": str(r["Energy Source Code"]).strip(),
                        "prime_mover": str(r["Prime Mover Code"]).strip(),
                        "nameplate_mw": to_number(r["Nameplate Capacity (MW)"]),
                        "energy_mwh": to_number(r.get("Nameplate Energy Capacity (MWh)")),
                        "status": str(r.get("Status") or "").strip(),
                        "online_year": to_int(_first(r, "Operating Year", "Planned Operation Year")),
                        "online_month": to_int(_first(r, "Operating Month", "Planned Operation Month")),
                        "retired_year": to_int(r.get("Retirement Year")),
                        "retired_month": to_int(r.get("Retirement Month")),
                        "lat": to_number(r.get("Latitude")),
                        "lon": to_number(r.get("Longitude")),
                    }
                )
            except KeyError as exc:
                raise Eia860Error(f"Unexpected layout: column {exc} not found on the '{sheet_name}' sheet.") from None
        if header is None:
            raise Eia860Error(f"Unexpected layout: the '{sheet_name}' sheet is empty.")
    return out


def fetch_generator_inventory(
    region: Region,
    files: Eia860m,
    session: requests.Session | None = None,
    today: date | None = None,
) -> dict:
    """Download the newest available EIA-860M workbook and return {"source_file", "as_of", "rows"}."""
    session = session or requests.Session()
    today = today or date.today()
    tried = []
    for as_of, name in candidate_files(today, files.max_months_back):
        content = _download(files.base_url + name, session)
        if content is None:
            tried.append(name)
            continue
        return {"source_file": name, "as_of": as_of, "rows": parse_workbook(content, region.state, region.county)}
    raise Eia860Error(
        f"No EIA-860M file found for the last {files.max_months_back + 1} months (tried {', '.join(tried)}). "
        "Check https://www.eia.gov/electricity/data/eia860m/ and that base_url in config/regions.yaml is right."
    )
