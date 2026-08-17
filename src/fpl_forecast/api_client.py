"""Thin client for the official (unauthenticated) Fantasy Premier League API.

Every response is cached to disk under ``data/cache`` so repeated CLI runs
don't hammer the API and the tool still works offline against a previous
snapshot. Endpoints for data that changes during a gameweek (prices, live
scores, fixture status) use a short TTL; endpoints for data that is frozen
once a gameweek finishes (element-summary, a past event's live/dream-team)
are cached indefinitely unless ``force_refresh=True`` is passed.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from fpl_forecast.constants import BASE_URL

DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour, for data that can change mid-season


class FPLClient:
    def __init__(
        self,
        cache_dir: str | Path = "data/cache",
        use_cache: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.use_cache = use_cache
        self.timeout = timeout
        self.session = requests.Session()
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- low-level fetch -------------------------------------------------

    def _cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _read_cache(self, cache_key: str, ttl: float | None) -> Any | None:
        if not self.use_cache:
            return None
        path = self._cache_path(cache_key)
        if not path.exists():
            return None
        if ttl is not None and (time.time() - path.stat().st_mtime) > ttl:
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, cache_key: str, data: Any) -> None:
        if not self.use_cache:
            return
        path = self._cache_path(cache_key)
        path.write_text(json.dumps(data))

    def _get(
        self,
        url_path: str,
        cache_key: str,
        ttl: float | None = DEFAULT_TTL_SECONDS,
        force_refresh: bool = False,
    ) -> Any:
        if not force_refresh:
            cached = self._read_cache(cache_key, ttl)
            if cached is not None:
                return cached

        response = self.session.get(f"{BASE_URL}/{url_path}", timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        self._write_cache(cache_key, data)
        return data

    # -- endpoints ---------------------------------------------------------

    def get_bootstrap_static(self, force_refresh: bool = False) -> dict:
        return self._get("bootstrap-static/", "bootstrap_static", force_refresh=force_refresh)

    def get_fixtures(self, event: int | None = None, force_refresh: bool = False) -> list[dict]:
        if event is not None:
            return self._get(
                f"fixtures/?event={event}", f"fixtures_event_{event}", force_refresh=force_refresh
            )
        return self._get("fixtures/", "fixtures_all", force_refresh=force_refresh)

    def get_element_summary(self, element_id: int, force_refresh: bool = False) -> dict:
        # Frozen once a gameweek is over; no TTL needed, only manual refresh.
        return self._get(
            f"element-summary/{element_id}/",
            f"element_summary_{element_id}",
            ttl=None,
            force_refresh=force_refresh,
        )

    def get_element_summaries_bulk(
        self, element_ids: list[int], max_workers: int = 10, force_refresh: bool = False
    ) -> dict[int, dict]:
        """Fetch element-summary for many players concurrently.

        Cached entries are served from disk without hitting the network;
        only misses go through the thread pool.
        """
        results: dict[int, dict] = {}
        to_fetch: list[int] = []
        for eid in element_ids:
            if not force_refresh:
                cached = self._read_cache(f"element_summary_{eid}", ttl=None)
                if cached is not None:
                    results[eid] = cached
                    continue
            to_fetch.append(eid)

        if not to_fetch:
            return results

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self.get_element_summary, eid, force_refresh): eid for eid in to_fetch
            }
            for future in as_completed(futures):
                eid = futures[future]
                try:
                    results[eid] = future.result()
                except requests.RequestException:
                    # Skip players whose history can't be fetched; the scoring
                    # model falls back to season aggregates for them.
                    continue
        return results

    def get_live_event(self, event_id: int, force_refresh: bool = False) -> dict:
        return self._get(
            f"event/{event_id}/live/", f"live_event_{event_id}", ttl=None, force_refresh=force_refresh
        )

    def get_dream_team(self, event_id: int, force_refresh: bool = False) -> dict:
        return self._get(
            f"dream-team/{event_id}/", f"dream_team_{event_id}", ttl=None, force_refresh=force_refresh
        )

    def get_set_piece_notes(self, force_refresh: bool = False) -> dict:
        return self._get("team/set-piece-notes/", "set_piece_notes", force_refresh=force_refresh)

    def get_event_status(self, force_refresh: bool = False) -> dict:
        return self._get("event-status/", "event_status", ttl=60 * 10, force_refresh=force_refresh)
