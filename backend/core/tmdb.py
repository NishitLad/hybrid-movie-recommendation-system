import httpx
import asyncio
import logging
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from cachetools import TTLCache
from fastapi import HTTPException
from .config import TMDB_API_KEY, TMDB_BASE
from .utils import make_img_url
from ..api.models import TMDBMovieCard, TMDBMovieDetails
from ..db.database import get_db

# Global Async Client for pooling
HTTP_CLIENT = httpx.AsyncClient(timeout=20, follow_redirects=True)
# RAM Cache for TMDB (1000 items, 1 hour TTL)
TMDB_CACHE = TTLCache(maxsize=1000, ttl=3600)

async def tmdb_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cached & Pooled TMDB GET (In-memory + Persistent DB Cache)
    """
    cache_key = f"{path}_{sorted(params.items())}"
    
    if cache_key in TMDB_CACHE:
        return TMDB_CACHE[cache_key]

    try:
        from ..db.database import P
        conn = get_db()
        c = conn.cursor()
        c.execute(f"SELECT response_json FROM tmdb_cache WHERE cache_key = {P}", (cache_key,))
        row = c.fetchone()
        if row:
            data = json.loads(row[0])
            TMDB_CACHE[cache_key] = data
            return data
    except Exception as e:
        logging.warning(f"DB Cache read error: {e}")

    q = dict(params)
    q["api_key"] = TMDB_API_KEY
    
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            r = await HTTP_CLIENT.get(f"{TMDB_BASE}{path}", params=q, timeout=10)
            if r.status_code == 200:
                data = r.json()
                TMDB_CACHE[cache_key] = data
                try:
                    from ..db.database import P, DATABASE_URL
                    conn = get_db()
                    c = conn.cursor()
                    if DATABASE_URL:
                        # PostgreSQL Upsert
                        c.execute(f"INSERT INTO tmdb_cache (cache_key, response_json, timestamp) VALUES ({P}, {P}, {P}) ON CONFLICT (cache_key) DO UPDATE SET response_json = EXCLUDED.response_json, timestamp = EXCLUDED.timestamp", 
                                  (cache_key, json.dumps(data), datetime.utcnow().isoformat()))
                    else:
                        # SQLite Upsert
                        c.execute(f"INSERT OR REPLACE INTO tmdb_cache (cache_key, response_json, timestamp) VALUES ({P}, {P}, {P})", 
                                  (cache_key, json.dumps(data), datetime.utcnow().isoformat()))
                    conn.commit()
                except Exception as db_e:
                    logging.warning(f"DB Cache write error: {db_e}")
                
                return data
            elif r.status_code == 429:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            else:
                logging.error(f"TMDB API error {r.status_code} for {path}")
                if r.status_code == 404: return {}
                raise HTTPException(status_code=502, detail=f"TMDB error {r.status_code}")
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                logging.error(f"TMDB connection failed: {e}")
                return {}
            await asyncio.sleep(0.1 * (attempt + 1))
    return {}

async def tmdb_cards_from_results(results: List[dict], limit: int = 20) -> List[TMDBMovieCard]:
    out: List[TMDBMovieCard] = []
    for m in (results or [])[:limit]:
        out.append(
            TMDBMovieCard(
                tmdb_id=int(m["id"]),
                title=m.get("title") or m.get("name") or "",
                poster_url=make_img_url(m.get("poster_path")),
                release_date=m.get("release_date"),
                vote_average=m.get("vote_average"),
            )
        )
    return out

async def tmdb_movie_details(movie_id: int) -> TMDBMovieDetails:
    data = await tmdb_get(f"/movie/{movie_id}", {"language": "en-US"})
    if not data:
        raise HTTPException(status_code=404, detail="Movie not found in TMDB")
    return TMDBMovieDetails(
        tmdb_id=int(data["id"]),
        title=data.get("title") or "",
        overview=data.get("overview"),
        release_date=data.get("release_date"),
        poster_url=make_img_url(data.get("poster_path")),
        backdrop_url=make_img_url(data.get("backdrop_path")),
        genres=data.get("genres", []) or [],
        vote_average=data.get("vote_average"),
    )

async def tmdb_search_movies(query: str, page: int = 1) -> Dict[str, Any]:
    return await tmdb_get(
        "/search/movie",
        {
            "query": query,
            "include_adult": "false",
            "language": "en-US",
            "page": page,
        },
    )

async def tmdb_search_first(query: str) -> Optional[dict]:
    data = await tmdb_search_movies(query=query, page=1)
    results = data.get("results", [])
    return results[0] if results else None

async def tmdb_search_person(query: str, page: int = 1) -> Dict[str, Any]:
    return await tmdb_get(
        "/search/person",
        {
            "query": query,
            "include_adult": "false",
            "language": "en-US",
            "page": page,
        },
    )

async def tmdb_get_movies_by_actor(actor_id: int, limit: int = 20) -> List[TMDBMovieCard]:
    try:
        data = await tmdb_get(
            "/discover/movie",
            {
                "with_cast": str(actor_id),
                "sort_by": "vote_count.desc",
                "include_adult": "false",
                "language": "en-US",
            },
        )
        results = data.get("results", [])
        return await tmdb_cards_from_results(results, limit=limit)
    except Exception as e:
        logging.error(f"Error fetching movies for actor {actor_id}: {e}")
        return []

async def attach_tmdb_card_by_title(title: str) -> Optional[TMDBMovieCard]:
    try:
        m = await tmdb_search_first(title)
        if not m:
            return None
        return TMDBMovieCard(
            tmdb_id=int(m["id"]),
            title=m.get("title") or title,
            poster_url=make_img_url(m.get("poster_path")),
            release_date=m.get("release_date"),
            vote_average=m.get("vote_average"),
        )
    except Exception:
        return None
