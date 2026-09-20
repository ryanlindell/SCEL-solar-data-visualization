"""Shared plumbing for the API clients (EIA, NREL): GET with retries and safe error messages."""

import time

import requests

MAX_ATTEMPTS = 4


def get_json(
    session: requests.Session,
    url: str,
    params: list[tuple[str, str]],
    error_cls: type[Exception],
    key_env_var: str,
) -> dict:
    """GET a JSON document, retrying temporary problems (rate limit, server errors, timeouts).

    The API key travels in the URL, so exceptions from `requests` (which quote the URL) are
    never allowed to propagate - we raise `error_cls` with our own message instead.
    """
    last_problem = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            last_problem = f"network error ({type(exc).__name__})"
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 403):
                raise error_cls(
                    f"The request was rejected (HTTP {resp.status_code}). "
                    f"Check that {key_env_var} in .env is correct."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                last_problem = f"HTTP {resp.status_code}"
            else:
                raise error_cls(f"HTTP {resp.status_code}: {resp.text[:300]}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(2**attempt)  # 2s, 4s, 8s
    raise error_cls(f"Giving up after {MAX_ATTEMPTS} attempts: {last_problem}")
