import os
import sqlite3
import pickle
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import pandas as pd
import httpx
import asyncio
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai

# PostgreSQL Support for Render
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_POSTGRES = DATABASE_URL is not None
if IS_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    logging.info("Using PostgreSQL Database")
else:
    logging.info("Using SQLite Database")


# =========================
# ENV
# =========================
load_dotenv()
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_500 = "https://image.tmdb.org/t/p/w500"

if not TMDB_API_KEY:
    # Don't crash import-time in production if you prefer; but for you better fail early:
    raise RuntimeError("TMDB_API_KEY missing. Put it in .env as TMDB_API_KEY=xxxx")


# =========================
# FASTAPI APP
# =========================
from contextlib import asynccontextmanager

# Global Async Client for pooling
HTTP_CLIENT = httpx.AsyncClient(timeout=20, follow_redirects=True)
# RAM Cache for TMDB (1000 items, 1 hour TTL)
TMDB_CACHE = TTLCache(maxsize=1000, ttl=3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load resources during startup
    global df, indices_obj, tfidf_matrix, tfidf_obj, TITLE_TO_IDX, TMDB_TO_IDX, IDX_TO_TMDB
    
    # Initialize SQLite database
    init_db()

    # Load df
    with open(DF_PATH, "rb") as f:
        df = pickle.load(f)

    # Load indices
    with open(INDICES_PATH, "rb") as f:
        indices_obj = pickle.load(f)

    # Load TF-IDF matrix (usually scipy sparse)
    with open(TFIDF_MATRIX_PATH, "rb") as f:
        tfidf_matrix = pickle.load(f)

    # Load TF-IDF vectorizer
    with open(TFIDF_PATH, "rb") as f:
        tfidf_obj = pickle.load(f)

    # Build normalized map
    TITLE_TO_IDX = build_title_to_idx_map(indices_obj)
    
    TMDB_TO_IDX = {}
    IDX_TO_TMDB = {}
    if 'id' in df.columns:
        for i, row in df.iterrows():
            try:
                t_id = int(row['id'])
                TMDB_TO_IDX[t_id] = i
                IDX_TO_TMDB[i] = t_id
            except (ValueError, TypeError):
                continue

    # sanity
    if df is None or "title" not in df.columns:
        raise RuntimeError("df.pkl must contain a DataFrame with a 'title' column")

    yield
    # Clean up
    await HTTP_CLIENT.aclose()

app = FastAPI(title="Movie Recommender API", version="3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for local streamlit
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# PICKLE GLOBALS
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROCESSED_DATA_DIR = os.path.join(DATA_DIR, "processed")
DB_DIR = os.path.join(DATA_DIR, "database")

DF_PATH = os.path.join(PROCESSED_DATA_DIR, "df.pkl")
INDICES_PATH = os.path.join(PROCESSED_DATA_DIR, "indices.pkl")
TFIDF_MATRIX_PATH = os.path.join(PROCESSED_DATA_DIR, "tfidf_matrix.pkl")
TFIDF_PATH = os.path.join(PROCESSED_DATA_DIR, "tfidf.pkl")

DB_PATH = os.path.join(DB_DIR, "app.db")

df: Optional[pd.DataFrame] = None
indices_obj: Any = None
tfidf_matrix: Any = None
tfidf_obj: Any = None

TITLE_TO_IDX: Optional[Dict[str, int]] = None
TMDB_TO_IDX: Optional[Dict[int, int]] = None 
IDX_TO_TMDB: Optional[Dict[int, int]] = None


# =========================
# MODELS
# =========================
class UserAuth(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    full_name: str
    password: str

class UserAction(BaseModel):
    username: str
    tmdb_id: int
    action_type: str  # "like", "view", "search"
    query: Optional[str] = None

class RatingAction(BaseModel):
    username: str
    tmdb_id: int
    rating: float

class WatchlistAction(BaseModel):
    username: str
    tmdb_id: int

class TMDBMovieCard(BaseModel):
    tmdb_id: int
    title: str
    poster_url: Optional[str] = None
    release_date: Optional[str] = None
    vote_average: Optional[float] = None


class TMDBMovieDetails(BaseModel):
    tmdb_id: int
    title: str
    overview: Optional[str] = None
    release_date: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    genres: List[dict] = []
    vote_average: Optional[float] = None


class TFIDFRecItem(BaseModel):
    title: str
    score: float
    tmdb: Optional[TMDBMovieCard] = None


class SearchBundleResponse(BaseModel):
    query: str
    movie_details: TMDBMovieDetails
    tfidf_recommendations: List[TFIDFRecItem]
    genre_recommendations: List[TMDBMovieCard]

class MoodRecResponse(BaseModel):
    mood: str
    recommendations: List[TMDBMovieCard]

class ChatRequest(BaseModel):
    message: str
    username: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    recommendations: List[TMDBMovieCard]


# =========================
# UTILS
# =========================
def _norm_title(t: str) -> str:
    return str(t).strip().lower()


def make_img_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return f"{TMDB_IMG_500}{path}"


async def tmdb_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cached & Pooled TMDB GET (In-memory + Persistent DB Cache)
    """
    # Create cache key
    cache_key = f"{path}_{sorted(params.items())}"
    
    # 1. Check RAM Cache
    if cache_key in TMDB_CACHE:
        return TMDB_CACHE[cache_key]

    # 2. Check DB Cache (for movie details specifically, or all calls)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT response_json FROM tmdb_cache WHERE cache_key = ?", (cache_key,))
        row = c.fetchone()
        if row:
            import json
            data = json.loads(row[0])
            TMDB_CACHE[cache_key] = data
            return data
    except Exception as e:
        logging.warning(f"DB Cache read error: {e}")

    q = dict(params)
    q["api_key"] = TMDB_API_KEY
    
    # Increase retries slightly and optimize timeout
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            r = await HTTP_CLIENT.get(f"{TMDB_BASE}{path}", params=q, timeout=10)
            if r.status_code == 200:
                data = r.json()
                
                # Update caches
                TMDB_CACHE[cache_key] = data
                try:
                    import json
                    conn = get_db()
                    c = conn.cursor()
                    c.execute("INSERT OR REPLACE INTO tmdb_cache (cache_key, response_json, timestamp) VALUES (?, ?, ?)", 
                              (cache_key, json.dumps(data), datetime.utcnow().isoformat()))
                    conn.commit()
                except Exception as db_e:
                    logging.warning(f"DB Cache write error: {db_e}")
                
                return data
            elif r.status_code == 429:
                # Rate limit hit - wait and retry
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            else:
                # Log error and return empty instead of crashing
                logging.error(f"TMDB API error {r.status_code} for {path}")
                if r.status_code == 404: return {}
                raise HTTPException(status_code=502, detail=f"TMDB error {r.status_code}")
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                logging.error(f"TMDB connection failed: {e}")
                return {} # Failure resilience
            await asyncio.sleep(0.1 * (attempt + 1))
    return {}


async def tmdb_cards_from_results(
    results: List[dict], limit: int = 20
) -> List[TMDBMovieCard]:
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
    """
    Raw TMDB response for keyword search (MULTIPLE results).
    Streamlit will use this for suggestions and grid.
    """
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
    """
    Search for actors/cast by name.
    Returns TMDB person data (actors, directors, etc).
    """
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
    """
    Get all movies featuring a specific actor by their TMDB actor ID.
    Uses /discover/movie with with_cast parameter.
    """
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
        print(f"Error fetching movies for actor {actor_id}: {e}")
        return []


# =========================
# TF-IDF Helpers
# =========================
def build_title_to_idx_map(indices: Any) -> Dict[str, int]:
    """
    indices.pkl can be:
    - dict(title -> index)
    - pandas Series (index=title, value=index)
    We normalize into TITLE_TO_IDX.
    """
    title_to_idx: Dict[str, int] = {}

    if isinstance(indices, dict):
        for k, v in indices.items():
            title_to_idx[_norm_title(k)] = int(v)
        return title_to_idx

    # pandas Series or similar mapping
    try:
        for k, v in indices.items():
            title_to_idx[_norm_title(k)] = int(v)
        return title_to_idx
    except Exception:
        # last resort: if it's a list-like etc.
        raise RuntimeError(
            "indices.pkl must be dict or pandas Series-like (with .items())"
        )


def get_local_idx_by_title(title: str) -> int:
    global TITLE_TO_IDX
    if TITLE_TO_IDX is None:
        raise HTTPException(status_code=500, detail="TF-IDF index map not initialized")
    key = _norm_title(title)
    if key in TITLE_TO_IDX:
        return int(TITLE_TO_IDX[key])
    raise HTTPException(
        status_code=404, detail=f"Title not found in local dataset: '{title}'"
    )


def tfidf_recommend_titles(
    query_title: str, top_n: int = 10
) -> List[Tuple[str, float]]:
    """
    Returns list of (title, score) from local df using cosine similarity on TF-IDF matrix.
    IMPROVED: Filters low-quality results and ensures diversity
    Safe against missing columns/rows.
    """
    global df, tfidf_matrix
    if df is None or tfidf_matrix is None:
        raise HTTPException(status_code=500, detail="TF-IDF resources not loaded")

    idx = get_local_idx_by_title(query_title)

    # query vector
    qv = tfidf_matrix[idx]
    scores = (tfidf_matrix @ qv.T).toarray().ravel()

    # Normalize scores to [0, 1]
    max_score = np.max(scores)
    if max_score > 0:
        scores = scores / max_score
    
    # Apply quality threshold - only keep scores > 0.15
    quality_threshold = 0.15
    
    # Sort descending
    order = np.argsort(-scores)

    out: List[Tuple[str, float]] = []
    seen_genres = set()
    
    for i in order:
        if int(i) == int(idx):
            continue
        
        score = scores[int(i)]
        
        # Skip very low scores early, but allow some low scores if not enough results
        if score < quality_threshold and len(out) >= top_n // 2:
            continue
            
        try:
            title_i = str(df.iloc[int(i)]["title"])
            
            # Add diversity: prefer unseen genres
            if len(out) < top_n:
                out.append((title_i, float(score)))
            
            if len(out) >= top_n * 2:  # Get extra candidates
                break
                
        except Exception:
            continue
    
    # Filter to top_n after diversity pass
    return sorted(out, key=lambda x: x[1], reverse=True)[:top_n]


async def attach_tmdb_card_by_title(title: str) -> Optional[TMDBMovieCard]:
    """
    Uses TMDB search by title to fetch poster for a local title.
    If not found, returns None (never crashes the endpoint).
    """
    try:
        m = await tmdb_search_first(title)
        if not m:
            return None
        return TMDBMovieCard(
            tmdb_id=int(m["id"]),
            title=m.get("title") or title,
            poster_url=make_img_url(m.get("poster_path")),
            release_date=m.get("release_date"),
    except Exception:
        return None

# Global connection pool
_DB_CONN = None

class DbCursor:
    def __init__(self, cursor):
        self.cursor = cursor
    
    def execute(self, query, params=None):
        if params is None:
            params = ()
        
        if IS_POSTGRES:
            # Convert ? to %s for PostgreSQL
            query = query.replace('?', '%s')
            
            # Handle dialect-specific replacements
            if 'INSERT OR REPLACE INTO tmdb_cache' in query:
                query = query.replace('INSERT OR REPLACE INTO tmdb_cache (cache_key, response_json, timestamp) VALUES (%s, %s, %s)', 
                                    'INSERT INTO tmdb_cache (cache_key, response_json, timestamp) VALUES (%s, %s, %s) ON CONFLICT (cache_key) DO UPDATE SET response_json=EXCLUDED.response_json, timestamp=EXCLUDED.timestamp')
            
            # Handle DATETIME -> TIMESTAMP if needed (Postgres uses TIMESTAMP)
            query = query.replace('DATETIME', 'TIMESTAMP')
            
        return self.cursor.execute(query, params)
    
    def fetchone(self):
        return self.cursor.fetchone()
        
    def fetchall(self):
        return self.cursor.fetchall()
        
    def __getattr__(self, name):
        return getattr(self.cursor, name)

class DbConn:
    def __init__(self, conn):
        self.conn = conn
        
    def cursor(self):
        if IS_POSTGRES:
            # Return a RealDictCursor-like behavior if we want, but keeping it simple
            return DbCursor(self.conn.cursor())
        return DbCursor(self.conn.cursor())
        
    def commit(self):
        return self.conn.commit()
        
    def close(self):
        # In a managed pool, we might not want to close, 
        # but for fresh connections we should.
        return self.conn.close()
        
    def __getattr__(self, name):
        return getattr(self.conn, name)

def get_db():
    if IS_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    else:
        conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    return DbConn(conn)

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    if IS_POSTGRES:
        # PostgreSQL schema
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                full_name TEXT,
                password TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                tmdb_id INTEGER,
                action_type TEXT,
                query TEXT,
                timestamp TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_ratings (
                user_id INTEGER REFERENCES users(id),
                tmdb_id INTEGER,
                rating FLOAT,
                UNIQUE(user_id, tmdb_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_watchlist (
                user_id INTEGER REFERENCES users(id),
                tmdb_id INTEGER,
                UNIQUE(user_id, tmdb_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS tmdb_cache (
                cache_key TEXT PRIMARY KEY,
                response_json TEXT,
                timestamp TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP
            )
        ''')
    else:
        # SQLite schema
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                full_name TEXT,
                password TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tmdb_id INTEGER,
                action_type TEXT,
                query TEXT,
                timestamp DATETIME,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_ratings (
                user_id INTEGER,
                tmdb_id INTEGER,
                rating FLOAT,
                UNIQUE(user_id, tmdb_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_watchlist (
                user_id INTEGER,
                tmdb_id INTEGER,
                UNIQUE(user_id, tmdb_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS tmdb_cache (
                cache_key TEXT PRIMARY KEY,
                response_json TEXT,
                timestamp DATETIME
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')
    conn.commit()
    conn.close()


# =========================
# ROUTES
# =========================
@app.get("/api")
def api_root():
    return {"message": "Movie Recommendation API is Running"}

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- HOME FEED (TMDB) ----------
@app.get("/home", response_model=List[TMDBMovieCard])
async def home(
    category: str = Query("popular"),
    limit: int = Query(24, ge=1, le=50),
):
    """
    Home feed for Streamlit (posters).
    category:
      - trending (trending/movie/day)
      - popular, top_rated, upcoming, now_playing  (movie/{category})
    """
    try:
        if category == "trending":
            data = await tmdb_get("/trending/movie/day", {"language": "en-US"})
            return await tmdb_cards_from_results(data.get("results", []), limit=limit)

        if category not in {"popular", "top_rated", "upcoming", "now_playing"}:
            raise HTTPException(status_code=400, detail="Invalid category")

        data = await tmdb_get(f"/movie/{category}", {"language": "en-US", "page": 1})
        return await tmdb_cards_from_results(data.get("results", []), limit=limit)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Home route failed: {e}")


# ---------- TMDB KEYWORD SEARCH (MULTIPLE RESULTS) ----------
@app.get("/tmdb/search")
async def tmdb_search(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1, le=10),
):
    """
    Returns RAW TMDB shape with 'results' list.
    Streamlit will use it for:
      - dropdown suggestions
      - grid results
    """
    return await tmdb_search_movies(query=query, page=page)


# ---------- MOVIE DETAILS (SAFE ROUTE) ----------
@app.get("/movie/id/{tmdb_id}", response_model=TMDBMovieDetails)
async def movie_details_route(tmdb_id: int):
    return await tmdb_movie_details(tmdb_id)


# ---------- ACTOR/CAST SEARCH ----------
@app.get("/search/actor")
async def search_actor(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1, le=10),
):
    """
    Search for actors/cast members by name.
    Returns TMDB person search results with actor info.
    """
    return await tmdb_search_person(query=query, page=page)


# ---------- MOVIES BY ACTOR ----------
@app.get("/movies/actor/{actor_id}", response_model=List[TMDBMovieCard])
async def get_movies_by_actor(
    actor_id: int,
    limit: int = Query(20, ge=1, le=50),
):
    """
    Get all movies featuring a specific actor.
    actor_id: TMDB person ID (obtained from /search/actor)
    limit: number of movies to return (max 50)
    """
    return await tmdb_get_movies_by_actor(actor_id=actor_id, limit=limit)


# ---------- GENRE RECOMMENDATIONS ----------
@app.get("/recommend/genre", response_model=List[TMDBMovieCard])
async def recommend_genre(
    tmdb_id: int = Query(...),
    limit: int = Query(18, ge=1, le=50),
):
    """
    Given a TMDB movie ID:
    - fetch details
    - pick first genre
    - discover movies in that genre (popular)
    """
    details = await tmdb_movie_details(tmdb_id)
    if not details.genres:
        return []

    genre_id = details.genres[0]["id"]
    discover = await tmdb_get(
        "/discover/movie",
        {
            "with_genres": genre_id,
            "language": "en-US",
            "sort_by": "popularity.desc",
            "page": 1,
        },
    )
    cards = await tmdb_cards_from_results(discover.get("results", []), limit=limit)
    return [c for c in cards if c.tmdb_id != tmdb_id]


# ---------- TF-IDF ONLY (debug/useful) ----------
@app.get("/recommend/tfidf")
async def recommend_tfidf(
    title: str = Query(..., min_length=1),
    top_n: int = Query(10, ge=1, le=50),
):
    recs = tfidf_recommend_titles(title, top_n=top_n)
    return [{"title": t, "score": s} for t, s in recs]


# ---------- ROBUST SIMILAR MOVIES (CONTENT + GENRE) ----------
@app.get("/movie/similar/{tmdb_id}", response_model=List[TMDBMovieCard])
async def get_similar_movies(tmdb_id: int, limit: int = 12):
    """
    Finds movies similar to the given tmdb_id using:
    1. Local TF-IDF (Content similarity on plot/overview)
    2. TMDB Genre discovery (Genre similarity)
    """
    details = await tmdb_movie_details(tmdb_id)
    
    # 1. Content Similarity (TF-IDF)
    tfidf_recs = []
    try:
        recs = tfidf_recommend_titles(details.title, top_n=limit)
        
        # Parallelize TMDB lookups for posters
        import asyncio
        tasks = [attach_tmdb_card_by_title(title) for title, _ in recs]
        cards = await asyncio.gather(*tasks)
        
        for card in cards:
            if card and card.tmdb_id != tmdb_id:
                tfidf_recs.append(card)
    except Exception as e:
        print(f"TF-IDF recommendation error: {e}")
        pass

    # 2. Genre Similarity
    genre_recs = []
    if details.genres:
        genre_id = details.genres[0]["id"]
        discover = await tmdb_get(
            "/discover/movie",
            {
                "with_genres": genre_id,
                "language": "en-US",
                "sort_by": "popularity.desc",
                "page": 1,
            },
        )
        genre_cards = await tmdb_cards_from_results(discover.get("results", []), limit=limit)
        genre_recs = [c for c in genre_cards if c.tmdb_id != tmdb_id]

    # Combine and deduplicate
    combined = []
    seen = {tmdb_id}
    
    # Interleave results
    for i in range(max(len(tfidf_recs), len(genre_recs))):
        if i < len(tfidf_recs) and tfidf_recs[i].tmdb_id not in seen:
            combined.append(tfidf_recs[i])
            seen.add(tfidf_recs[i].tmdb_id)
        if i < len(genre_recs) and genre_recs[i].tmdb_id not in seen:
            combined.append(genre_recs[i])
            seen.add(genre_recs[i].tmdb_id)
            
    return combined[:limit]


# ---------- BUNDLE: Details + TF-IDF recs + Genre recs ----------
@app.get("/movie/search", response_model=SearchBundleResponse)
async def search_bundle(
    query: str = Query(..., min_length=1),
    tfidf_top_n: int = Query(12, ge=1, le=30),
    genre_limit: int = Query(12, ge=1, le=30),
):
    """
    This endpoint is for when you have a selected movie and want:
      - movie details
      - TF-IDF recommendations (local) + posters
      - Genre recommendations (TMDB) + posters

    NOTE:
    - It selects the BEST match from TMDB for the given query.
    - If you want MULTIPLE matches, use /tmdb/search
    """
    best = await tmdb_search_first(query)
    if not best:
        raise HTTPException(
            status_code=404, detail=f"No TMDB movie found for query: {query}"
        )

    tmdb_id = int(best["id"])
    details = await tmdb_movie_details(tmdb_id)

    # 1) TF-IDF recommendations (never crash endpoint)
    tfidf_items: List[TFIDFRecItem] = []

    recs: List[Tuple[str, float]] = []
    try:
        # try local dataset by TMDB title
        recs = tfidf_recommend_titles(details.title, top_n=tfidf_top_n)
    except Exception:
        # fallback to user query
        try:
            recs = tfidf_recommend_titles(query, top_n=tfidf_top_n)
        except Exception:
            recs = []

    import asyncio
    # Parallelize poster lookups
    tasks = [attach_tmdb_card_by_title(title) for title, _ in recs]
    cards = await asyncio.gather(*tasks)

    for i, card in enumerate(cards):
        title, score = recs[i]
        tfidf_items.append(TFIDFRecItem(title=title, score=score, tmdb=card))

    # 2) Genre recommendations (TMDB discover by first genre)
    genre_recs: List[TMDBMovieCard] = []
    if details.genres:
        genre_id = details.genres[0]["id"]
        discover = await tmdb_get(
            "/discover/movie",
            {
                "with_genres": genre_id,
                "language": "en-US",
                "sort_by": "popularity.desc",
                "page": 1,
            },
        )
        cards = await tmdb_cards_from_results(
            discover.get("results", []), limit=genre_limit
        )
        genre_recs = [c for c in cards if c.tmdb_id != details.tmdb_id]

    return SearchBundleResponse(
        query=query,
        movie_details=details,
        tfidf_recommendations=tfidf_items,
        genre_recommendations=genre_recs,
    )


# =========================
# NEW ENDPOINTS (AUTH & BEHAVIOR)
# =========================

@app.post("/signup")
async def signup(user: UserSignup):
    # Validate input
    if not user.username or not user.password or not user.full_name:
        raise HTTPException(status_code=400, detail="All fields are required")
    if len(user.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    if len(user.username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters long")
    
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, full_name, password) VALUES (?, ?, ?)", 
                  (user.username, user.full_name, user.password))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already registered")
    conn.close()
    return {"message": "User created successfully", "username": user.username, "full_name": user.full_name}


@app.post("/login")
async def login(user: UserAuth):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ? AND password = ?", (user.username, user.password))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"message": "Login successful", "username": user.username}
    raise HTTPException(status_code=400, detail="Invalid username or password")


@app.post("/user-action")
async def store_user_action(action: UserAction):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE username = ?", (action.username,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
        
    user_id = row[0]
    
    c.execute(
        "INSERT INTO user_history (user_id, tmdb_id, action_type, query, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, action.tmdb_id, action.action_type, action.query, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "message": f"Action '{action.action_type}' recorded"}


@app.post("/api/client-error")
async def log_client_error(payload: Dict[str, Any]):
    logging.error(f"CLIENT ERROR: {payload}")
    return {"status": "recorded"}


def calculate_recency_weight(timestamp_str: str) -> float:
    """
    Calculate weight based on recency. Recent actions have higher weight.
    Decays over time (7 days half-life)
    """
    from datetime import timedelta
    try:
        ts = datetime.fromisoformat(timestamp_str)
        age = datetime.utcnow() - ts
        days_old = age.total_seconds() / 86400
        
        # Exponential decay: 4 days half-life for search, 10 days for likes
        # We'll use a standard 7-day half-life for general actions
        weight = 2 ** (-days_old / 7.0)
        return max(weight, 0.15)
    except Exception as e:
        return 1.0


@app.get("/recommendations/{username}", response_model=List[TMDBMovieCard])
async def get_personalized_recommendations(username: str, limit: int = 18):
    """
    Enhanced Hybrid Recommendation: Content + Behavior with Recency + Diversity
    Optimized: Uses local data for bonuses and parallelizes TMDB fetches.
    """
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row:
        return await home(category="popular", limit=limit)
        
    user_id = u_row[0]
    
    # Get ratings early to use for weighting
    c.execute("SELECT tmdb_id, rating FROM user_ratings WHERE user_id = ?", (user_id,))
    ratings = {row[0]: row[1] for row in c.fetchall()}
    
    # Get actions
    c.execute("SELECT tmdb_id, action_type, timestamp, query FROM user_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 500", (user_id,))
    actions = c.fetchall()
    
    # Cold start check
    if not actions and not ratings:
        return await home(category="trending", limit=limit)
        
    # Calculate weighted scores with recency
    scores = {}
    search_intents = []
    
    # Weights for different action types
    action_weights = {
        "like": 10.0,      # Strong signal
        "watchlist": 7.0,   # High intent
        "view": 3.0,        # Medium signal
        "search": 2.0,      # Interest signal
        "rating_update": 5.0 # Explicit signal
    } 
    
    for tmdb_id, action_type, timestamp, query in actions:
        base_weight = action_weights.get(action_type, 1.0)
        recency_mult = calculate_recency_weight(timestamp)
        
        if action_type == "search" and query:
            search_intents.append((query, recency_mult))
            continue

        rating_mult = 1.0
        if tmdb_id in ratings:
            r = ratings[tmdb_id]
            if r >= 4: rating_mult = 2.5
            elif r == 3: rating_mult = 1.0
            else: rating_mult = -2.0
                
        final_weight = base_weight * recency_mult * rating_mult
        scores[tmdb_id] = scores.get(tmdb_id, 0) + final_weight

    global tfidf_matrix, TMDB_TO_IDX, df, tfidf_obj
    if tfidf_matrix is None or TMDB_TO_IDX is None:
         return await home(category="popular", limit=limit)

    user_vector = None
    interacted_indices = set()
    
    for t_id, weight in scores.items():
        idx = None
        if TMDB_TO_IDX and t_id in TMDB_TO_IDX:
            idx = TMDB_TO_IDX[t_id]
        else:
            # Fallback: Get title from TMDB and use TITLE_TO_IDX
            try:
                # Use cache-heavy details fetch
                details = await tmdb_movie_details(t_id)
                n_title = _norm_title(details.title)
                if TITLE_TO_IDX and n_title in TITLE_TO_IDX:
                    idx = TITLE_TO_IDX[n_title]
            except Exception: pass
            
        if idx is not None:
            interacted_indices.add(idx)
            raw_vec = tfidf_matrix[idx]
            scaled = raw_vec * weight
            if user_vector is None:
                user_vector = scaled.copy()
            else:
                user_vector = user_vector + scaled

    if search_intents and tfidf_obj:
        for query_text, weight in search_intents:
            try:
                query_vec = tfidf_obj.transform([query_text.lower()])
                user_vector = user_vector + (query_vec * (weight * 3.5)) if user_vector is not None else (query_vec * (weight * 3.5))
            except Exception: pass

    if user_vector is None:
        return await home(category="popular", limit=limit)
    
    norm = np.linalg.norm(user_vector.toarray())
    if norm > 0: user_vector = user_vector / norm
    
    sim_scores = (tfidf_matrix @ user_vector.T).toarray().ravel()
    
    pos_scores = [s for s in sim_scores if s > 0]
    quality_threshold = np.percentile(pos_scores, 30) if pos_scores else 0
    order = np.argsort(-sim_scores)
    
    # Optimized: Extract recent genres once
    recent_genres = set()
    if actions:
        try:
            latest_id = actions[0][0]
            # Try to get genre from TMDB if not in local df
            if TMDB_TO_IDX and latest_id in TMDB_TO_IDX:
                l_idx = TMDB_TO_IDX[latest_id]
                local_genres = str(df.iloc[l_idx].get('genre_names', '')).lower().split()
                recent_genres = set(local_genres)
            else:
                # Fallback to TMDB if not in local
                ld = await tmdb_movie_details(latest_id)
                recent_genres = set(g["name"].lower() for g in ld.genres)
        except Exception: pass

    recs = []
    seen_genres = set()
    for i in order:
        if i in interacted_indices: continue
        
        score = sim_scores[int(i)]
        if score < quality_threshold and len(recs) >= limit // 2: continue 
            
        try:
            row = df.iloc[int(i)]
            m_title = str(row["title"])
            m_id = row["id"] if "id" in df.columns else None
            
            # Optimized Bonus: Use local genre data instead of tmdb_movie_details
            bonus = 0
            if 'genre_names' in df.columns:
                m_genres = str(row['genre_names']).lower().split()
                curr_genres_set = set(m_genres)
                
                if recent_genres and (curr_genres_set & recent_genres):
                    bonus += 0.25
                
                unseen = len(curr_genres_set - seen_genres)
                if unseen > 0: bonus += (0.05 * unseen)
                seen_genres.update(curr_genres_set)
            
            recs.append((m_title, int(m_id) if m_id else None, score + bonus))
        except Exception: continue
            
        if len(recs) >= limit * 2.5: break 
    
    recs.sort(key=lambda x: x[2], reverse=True)
    
    # ACCURACY: DIVERSITY PASS
    # Penalize movies that are too similar in genre to already selected top ones
    diversified = []
    seen_genres_count = {}
    
    for title, m_id, original_score in recs:
        try:
            # get genre names for this candidate
            # We can use the df row we already have if we pass it around, 
            # or just look it up again (fast since it's in memory)
            if TMDB_TO_IDX and m_id in TMDB_TO_IDX:
                idx = TMDB_TO_IDX[m_id]
                m_genres = str(df.iloc[idx].get('genre_names', '')).lower().split()
                
                # Calculate penalty based on how many of these genres we've already seen
                penalty = 0
                for g in m_genres:
                    count = seen_genres_count.get(g, 0)
                    penalty += (count * 0.05) # 5% penalty per previous occurrence
                
                new_score = original_score - penalty
                diversified.append((title, m_id, new_score, m_genres))
            else:
                diversified.append((title, m_id, original_score, []))
        except:
            diversified.append((title, m_id, original_score, []))

    # Re-sort after diversity penalty
    diversified.sort(key=lambda x: x[2], reverse=True)
    
    final_recs_meta = []
    for title, m_id, score, genres in diversified[:limit]:
        final_recs_meta.append((title, m_id))
        # Update seen counts for better diversity in next checks (optional if sorted)
        for g in genres:
            seen_genres_count[g] = seen_genres_count.get(g, 0) + 1

    # PARALLEL FETCHING for the final list
    tasks = []
    for title, m_id in final_recs_meta:
        if m_id:
            tasks.append(tmdb_movie_details(m_id))
        else:
            tasks.append(attach_tmdb_card_by_title(title))
                
    results = await asyncio.gather(*tasks, return_exceptions=True)
    final_cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            final_cards.append(TMDBMovieCard(
                tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, 
                release_date=res.release_date, vote_average=res.vote_average
            ))
        elif isinstance(res, TMDBMovieCard):
            final_cards.append(res)
                
    return final_cards[:limit] or await home(category="popular", limit=limit)



# ---------- GENRE-BASED RECOMMENDATIONS FROM USER BEHAVIOR (IMPROVED) ----------
@app.get("/recommendations/genres/{username}", response_model=List[TMDBMovieCard])
async def get_genre_based_recommendations(username: str, limit: int = 18):
    """
    Extract genres from user's liked movies and recommend trending movies in those genres.
    IMPROVED: Analyzes all genres, not just top 3, and prioritizes user preferences
    """
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        return await home(category="trending", limit=limit)
        
    user_id = u_row[0]
    
    # Get user's LIKED movies (weighted higher than views)
    c.execute(
        "SELECT tmdb_id FROM user_history WHERE user_id = ? AND action_type = 'like' LIMIT 20",
        (user_id,)
    )
    liked_movies = [row[0] for row in c.fetchall()]
    
    if not liked_movies:
        # Fallback to most viewed movies if no likes
        c.execute(
            "SELECT DISTINCT tmdb_id FROM user_history WHERE user_id = ? LIMIT 15",
            (user_id,)
        )
        liked_movies = [row[0] for row in c.fetchall()]
    
    conn.close()
    
    if not liked_movies:
        return await home(category="trending", limit=limit)
    
    # Collect genres from liked movies with frequency weighting
    genre_freq = {}
    for tmdb_id in liked_movies:
        try:
            details = await tmdb_movie_details(tmdb_id)
            for genre in details.genres:
                genre_id = genre["id"]
                genre_freq[genre_id] = genre_freq.get(genre_id, 0) + 1
        except Exception as e:
            continue
    
    if not genre_freq:
        return await home(category="trending", limit=limit)
    
    # Sort genres by frequency (user's top preferences)
    top_genres = sorted(genre_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    genre_ids = [g[0] for g in top_genres]
    
    # Discover movies by TOP genres (not just first 3)
    genre_str = ",".join(str(g) for g in genre_ids)
    try:
        discover = await tmdb_get(
            "/discover/movie",
            {
                "with_genres": genre_str,
                "language": "en-US",
                "sort_by": "popularity.desc",
                "vote_count.gte": 100,  # Quality filter
                "page": 1,
            },
        )
        cards = await tmdb_cards_from_results(discover.get("results", []), limit=limit)
        # Filter out movies user already interacted with
        return [c for c in cards if c.tmdb_id not in liked_movies]
    except Exception as e:
        return await home(category="trending", limit=limit)


# ---------- TRENDING IN USER'S FAVORITE GENRES ----------
@app.get("/recommendations/trending-genres/{username}", response_model=List[TMDBMovieCard])
async def get_trending_in_user_genres(username: str, limit: int = 12):
    """
    Show what's trending right now in genres the user likes.
    Great for time-sensitive recommendations.
    """
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        return await home(category="trending", limit=limit)
        
    user_id = u_row[0]
    c.execute(
        "SELECT tmdb_id FROM user_history WHERE user_id = ? LIMIT 20",
        (user_id,)
    )
    viewed_movies = [row[0] for row in c.fetchall()]
    conn.close()
    
    if not viewed_movies:
        return await home(category="trending", limit=limit)
    
    # Get trending movies
    trending_data = await tmdb_get("/trending/movie/week", {"language": "en-US"})
    all_trending = await tmdb_cards_from_results(trending_data.get("results", []), limit=50)
    
    # Filter to show new ones user hasn't seen
    return [m for m in all_trending if m.tmdb_id not in viewed_movies][:limit]


# ---------- COLLABORATIVE: MOVIES LIKED BY SIMILAR USERS (IMPROVED) ----------
@app.get("/recommendations/collaborative/{username}", response_model=List[TMDBMovieCard])
async def get_collaborative_recommendations(username: str, limit: int = 12):
    """
    Find movies liked by users with similar taste. Optimized with parallel fetching.
    """
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row: return await home(category="popular", limit=limit)
        
    user_id = u_row[0]
    c.execute("SELECT tmdb_id FROM user_history WHERE user_id = ? AND action_type IN ('view', 'like')", (user_id,))
    user_movies = set(row[0] for row in c.fetchall())
    
    if not user_movies: return await home(category="popular", limit=limit)
    
    watched_list = list(user_movies)[:20]
    placeholders = ",".join("?" * len(watched_list))
    c.execute(f"""
        SELECT user_id, COUNT(*) as common_count FROM user_history 
        WHERE tmdb_id IN ({placeholders})
        GROUP BY user_id HAVING common_count >= 2 AND user_id != ?
        ORDER BY common_count DESC LIMIT 50
    """, watched_list + [user_id])
    similar_users = [row[0] for row in c.fetchall()]
    
    if not similar_users: return await home(category="popular", limit=limit)
    
    placeholders = ",".join("?" * len(similar_users))
    c.execute(f"""
        SELECT tmdb_id, COUNT(*) as like_count FROM user_history 
        WHERE user_id IN ({placeholders}) AND action_type = 'like'
        GROUP BY tmdb_id ORDER BY like_count DESC LIMIT ?
    """, similar_users + [limit * 3])
    collab_movies = [row[0] for row in c.fetchall() if row[0] not in user_movies]
    
    # Parallel Fetch
    tasks = [tmdb_movie_details(m_id) for m_id in collab_movies[:limit]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            final_cards.append(TMDBMovieCard(
                tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, 
                release_date=res.release_date, vote_average=res.vote_average
            ))
            
    return final_cards or await home(category="popular", limit=limit)


# ---------- USER STATS / ANALYTICS ----------
@app.get("/user/stats/{username}")
async def get_user_stats(username: str):
    """
    Get user engagement stats for analytics/profile.
    """
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    user_id = u_row[0]

    # Count actions by type
    c.execute(
        "SELECT action_type, COUNT(*) FROM user_history WHERE user_id = ? GROUP BY action_type",
        (user_id,)
    )
    action_counts = {row[0]: row[1] for row in c.fetchall()}

    # Get favorite genres from ALL interactions (views + likes)
    # Weight likes 3x more than views for the DNA profile
    c.execute("""
        SELECT tmdb_id, action_type FROM user_history 
        WHERE user_id = ? AND action_type IN ('like', 'view')
        ORDER BY timestamp DESC LIMIT 100
    """, (user_id,))
    recent_actions = c.fetchall()
    conn.close()
    
    # Parallel Fetching for details
    tasks = [tmdb_movie_details(m_id) for m_id, _ in recent_actions]
    details_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    genre_freq = {}
    total_weighted_count = 0
    
    for i, (tmdb_id, action_type) in enumerate(recent_actions):
        weight = 3 if action_type == 'like' else 1
        details = details_results[i]
        
        if isinstance(details, TMDBMovieDetails):
            for genre in details.genres:
                g_name = genre.get("name", "Unknown")
                genre_freq[g_name] = genre_freq.get(g_name, 0) + weight
                total_weighted_count += weight
    
    # Sort and calculate true percentages
    top_genres = sorted(genre_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    
    dna_results = []
    if total_weighted_count > 0:
        # We normalize to the top genre to fill the bar better
        max_v = top_genres[0][1] if top_genres else 1
        for g, v in top_genres:
            dna_results.append({
                "genre": g, 
                "count": v, 
                "percent": int((v / max_v) * 100)
            })
    
    # Calculate meaningful level and score
    views = action_counts.get('view', 0)
    likes = action_counts.get('like', 0)
    score = views + (likes * 10) # Likes are weighted heavily
    level = (score // 100) + 1  # Professional leveling
    
    return {
        "username": username,
        "total_interactions": sum(action_counts.values()),
        "action_breakdown": action_counts,
        "favorite_genres": dna_results,
        "level": level,
        "score": score,
        "diversity_score": len(genre_freq)
    }


# ---------- ACCURATE GENRE-BASED RECOMMENDATIONS (EXACT MATCH) ----------
@app.get("/recommendations/genre-accurate/{username}", response_model=List[TMDBMovieCard])
async def get_accurate_genre_recommendations(username: str, limit: int = 18):
    """
    ACCURATE GENRE SYSTEM optimized with parallel fetches.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row: return await home(category="trending", limit=limit)
    
    user_id = u_row[0]
    c.execute("SELECT DISTINCT tmdb_id FROM user_history WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    all_movie_ids = set(row[0] for row in c.fetchall())
    
    c.execute("SELECT tmdb_id FROM user_history WHERE user_id = ? AND action_type = 'like' ORDER BY timestamp DESC LIMIT 30", (user_id,))
    liked_movies = [row[0] for row in c.fetchall()]
    
    if not liked_movies:
        c.execute("SELECT tmdb_id FROM user_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20", (user_id,))
        liked_movies = [row[0] for row in c.fetchall()]
    
    if not liked_movies: return await home(category="trending", limit=limit)
    
    # Parallel genre extraction
    tasks = [tmdb_movie_details(tmdb_id) for tmdb_id in liked_movies]
    details_list = await asyncio.gather(*tasks, return_exceptions=True)
    
    genre_ids = {}
    for details in details_list:
        if isinstance(details, TMDBMovieDetails):
            for genre in details.genres:
                g_id = genre["id"]
                genre_ids[g_id] = genre_ids.get(g_id, 0) + 1
    
    if not genre_ids: return await home(category="trending", limit=limit)
    
    sorted_genres = sorted(genre_ids.items(), key=lambda x: x[1], reverse=True)
    top_genre_ids = [g[0] for g in sorted_genres]
    genre_str = ",".join(str(g) for g in top_genre_ids[:8])
    
    try:
        discover_params = {"with_genres": genre_str, "language": "en-US", "sort_by": "vote_count.desc", "vote_count.gte": 300, "page": 1}
        discover_result = await tmdb_get("/discover/movie", discover_params)
        results = discover_result.get("results", [])
        
        recs = []
        for movie in results:
            if movie.get("id") in all_movie_ids: continue
            poster_path = movie.get("poster_path")
            recs.append(TMDBMovieCard(
                tmdb_id=movie["id"], title=movie.get("title", ""),
                poster_url=f"{TMDB_IMG_500}{poster_path}" if poster_path else None,
                release_date=movie.get("release_date", ""), vote_average=movie.get("vote_average")
            ))
            if len(recs) >= limit: break
        return recs or await home(category="trending", limit=limit)
    except Exception:
        return await home(category="trending", limit=limit)


# ---------- GET MOVIES BY SPECIFIC GENRES ----------
@app.get("/movies/genres", response_model=List[TMDBMovieCard])
async def get_movies_by_genres(
    genre_ids: str = Query("28"),  # Default: Action (28)
    sort_by: str = Query("popularity.desc"),
    limit: int = Query(18),
    min_votes: int = Query(300)
):
    """
    Get movies filtered by genres.
    genre_ids: comma-separated TMDB genre IDs
    sort_by: vote_count.desc, popularity.desc, release_date.desc
    min_votes: minimum vote count for quality
    
    Example: /movies/genres?genre_ids=28,878&sort_by=popularity.desc&limit=18
    - 28 = Action
    - 878 = Science Fiction
    """
    try:
        discover_params = {
            "with_genres": genre_ids,
            "language": "en-US",
            "sort_by": sort_by,
            "vote_count.gte": min_votes,
            "page": 1,
        }
        
        result = await tmdb_get("/discover/movie", discover_params)
        cards = await tmdb_cards_from_results(result.get("results", []), limit=limit)
        return cards
    except Exception as e:
        print(f"Genre filter error: {e}")
        return []


# ---------- GET ALL GENRES ----------
@app.get("/genres")
async def get_all_genres():
    """
    Get list of all TMDB movie genres for filtering.
    Returns: {"genres": [{"id": 28, "name": "Action"}, ...]}
    """
    try:
        result = await tmdb_get("/genre/movie/list", {"language": "en-US"})
        return result.get("genres", [])
    except Exception as e:
        print(f"Genre list error: {e}")
        return []

# ---------- RATINGS ----------
@app.post("/rating")
async def store_rating(action: RatingAction):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (action.username,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
        
    user_id = row[0]
    c.execute(
        "INSERT INTO user_ratings (user_id, tmdb_id, rating) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, tmdb_id) DO UPDATE SET rating=excluded.rating",
        (user_id, action.tmdb_id, action.rating)
    )
    c.execute(
        "INSERT INTO user_history (user_id, tmdb_id, action_type, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, action.tmdb_id, "rating_update", datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "message": "Rating saved"}

# ---------- WATCHLIST ----------
@app.post("/watchlist/toggle")
async def toggle_watchlist(action: WatchlistAction):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (action.username,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    user_id = row[0]

    c.execute("SELECT user_id FROM user_watchlist WHERE user_id = ? AND tmdb_id = ?", (user_id, action.tmdb_id))
    exists = c.fetchone()
    
    if exists:
        c.execute("DELETE FROM user_watchlist WHERE user_id = ? AND tmdb_id = ?", (user_id, action.tmdb_id))
        status = "removed"
    else:
        c.execute("INSERT INTO user_watchlist (user_id, tmdb_id) VALUES (?, ?)", 
                  (user_id, action.tmdb_id))
        status = "added"
        # Track for behavior recommendations
        c.execute(
            "INSERT INTO user_history (user_id, tmdb_id, action_type, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, action.tmdb_id, "watchlist", datetime.utcnow().isoformat())
        )
        
    conn.commit()
    conn.close()
    return {"status": "ok", "action": status}

@app.get("/watchlist/{username}", response_model=List[TMDBMovieCard])
async def get_watchlist(username: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row: return []
        
    user_id = u_row[0]
    c.execute("SELECT tmdb_id FROM user_watchlist WHERE user_id = ? LIMIT 50", (user_id,))
    wl_movies = [row[0] for row in c.fetchall()]
    
    tasks = [tmdb_movie_details(m_id) for m_id in wl_movies]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            cards.append(TMDBMovieCard(
                tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, 
                release_date=res.release_date, vote_average=res.vote_average
            ))
    return cards

@app.get("/history/{username}", response_model=List[TMDBMovieCard])
async def get_watch_history(username: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row: return []
        
    user_id = u_row[0]
    c.execute("SELECT DISTINCT tmdb_id FROM user_history WHERE user_id = ? AND action_type = 'view' ORDER BY timestamp DESC LIMIT 20", (user_id,))
    history_movies = [row[0] for row in c.fetchall()]
    
    tasks = [tmdb_movie_details(m_id) for m_id in history_movies]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            cards.append(TMDBMovieCard(
                tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, 
                release_date=res.release_date, vote_average=res.vote_average
            ))
    return cards

@app.get("/trending-custom", response_model=List[TMDBMovieCard])
async def trending_custom(limit: int = 15):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT tmdb_id, COUNT(*) as watch_count FROM user_history WHERE action_type='view' GROUP BY tmdb_id ORDER BY watch_count DESC LIMIT ?", (limit,))
    movies = c.fetchall()
    conn.close()
    
    if not movies:
        return await home(category="trending", limit=limit)
        
    cards = []
    for m_id, count in movies:
        try:
            details = await tmdb_movie_details(m_id)
            cards.append(TMDBMovieCard(
                tmdb_id=m_id, title=details.title, poster_url=details.poster_url, 
                release_date=details.release_date, vote_average=details.vote_average
            ))
        except Exception as e:
            pass
    return cards

@app.get("/recommendations/recent/{username}", response_model=List[TMDBMovieCard])
async def get_recent_recommendations(username: str, limit: int = 15):
    """
    Finds the VERY LAST movie the user opened and recommends similar titles.
    This provides immediate feedback after opening a movie.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        return []
    
    user_id = u_row[0]
    # Get the latest viewed movie
    c.execute(
        "SELECT tmdb_id FROM user_history WHERE user_id = ? AND action_type = 'view' ORDER BY timestamp DESC LIMIT 1",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    
    if not row:
        return []
        
    last_tmdb_id = row[0]
    # Reuse the robust similarity logic
    return await get_similar_movies(last_tmdb_id, limit=limit)

@app.get("/rating/{username}/{tmdb_id}")
async def get_user_rating(username: str, tmdb_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        return {"rating": None, "in_watchlist": False}
    user_id = u_row[0]
    
    c.execute("SELECT rating FROM user_ratings WHERE user_id = ? AND tmdb_id = ?", (user_id, tmdb_id))
    row = c.fetchone()
    
    c.execute("SELECT tmdb_id FROM user_watchlist WHERE user_id = ? AND tmdb_id = ?", (user_id, tmdb_id))
    wl_row = c.fetchone()
    conn.close()
    
    return {
        "rating": row[0] if row else None,
        "in_watchlist": bool(wl_row)
    }


# ---------- SEMANTIC MOOD & CHAT AI ----------

MOOD_MAP = {
    "happy": "joyful funny upbeat lighthearted cheerful comedy animation family feel-good laugh bright positive humor fun friendly optimistic",
    "sad": "emotional moving dramatic melancholy touching tearjerker tragedy meaningful crying grief heartbreak sorrow poignant deep",
    "motivated": "inspiring triumph success struggle perseverance goal ambitious winner persistence determination strong power dream achievement courage heroic",
    "romantic": "love romance dating relationship passion marriage intimate destiny sweet soulmate couple kissing heart together blossom",
    "thrilled": "suspense mystery intense thriller crime chase investigation shock twist adrenaline dark tension excitement heist secret racing",
    "scared": "horror dark scary ghost supernatural nightmare fear survival scream creature creepy haunted slasher paranormal spooky",
    "epic": "adventure war grand journey historical battle hero legendary world fantasy colossal myth empire struggle kingdom",
    "chill": "peaceful calm artistic document nature slow ambient relaxing life mellow zen slow-paced travel scenery quiet",
}

@app.get("/recommend/mood")
async def recommend_by_mood(mood: str = Query(...), limit: int = 18):
    """
    Advanced Mood System: Uses semantic search on overviews to find the "vibe".
    """
    mood_key = mood.lower()
    # Handle the specific user requests like "motivated" or "happy"
    search_text = MOOD_MAP.get(mood_key, mood_key)
    
    global tfidf_obj, tfidf_matrix, df
    if not tfidf_obj or tfidf_matrix is None:
        return await home(category="popular", limit=limit)

    try:
        # 1. Get initial semantic matches from local DB
        query_vec = tfidf_obj.transform([search_text])
        sim_scores = (tfidf_matrix @ query_vec.T).toarray().ravel()
        
        # Boost movies that match typical genres for this mood
        # (e.g. if happy, boost Comedy/Animation)
        genre_boost = {
            "happy": ["comedy", "animation", "family"],
            "sad": ["drama", "romance"],
            "motivated": ["documentary", "drama", "action"],
            "romantic": ["romance", "drama", "comedy"],
            "thrilled": ["thriller", "mystery", "crime"],
            "scared": ["horror", "thriller"],
            "epic": ["adventure", "fantasy", "war"],
            "chill": ["documentary", "animation"]
        }
        
        boosted_scores = sim_scores.copy()
        target_genres = genre_boost.get(mood_key, [])
        
        if target_genres and 'genre_names' in df.columns:
            # OPTIMIZED: Vectorized genre boosting
            genre_pattern = '|'.join(target_genres)
            mask = df['genre_names'].str.lower().str.contains(genre_pattern, na=False).values
            boosted_scores[mask] *= 1.25 # Boosted for better "vibe" match
        
        order = np.argsort(-boosted_scores)[:limit*2]
        
        recs = []
        for i in order:
            row = df.iloc[int(i)]
            m_id = row['id'] if 'id' in df.columns else None
            if row.get('vote_average', 0) < 5.0: continue
            recs.append((str(row['title']), m_id, boosted_scores[int(i)]))
        
        # Parallel fetch posters
        tasks = []
        for title, m_id, score in recs:
            if m_id: tasks.append(tmdb_movie_details(m_id))
            else: tasks.append(attach_tmdb_card_by_title(title))
            if len(tasks) >= limit: break
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final = []
        for res in results:
            if isinstance(res, TMDBMovieDetails):
                final.append(TMDBMovieCard(
                    tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url,
                    release_date=res.release_date, vote_average=res.vote_average
                ))
            elif isinstance(res, TMDBMovieCard): final.append(res)
        return final
    except Exception as e:
        print(f"Mood recommendation error: {e}")
        return await home(category="popular", limit=limit)

@app.post("/chat")
async def chat_assistant(payload: Dict[str, str]):
    """
    ULTRA POWERFUL AI ASSISTANT:
    - Persistent Personality (Master Critic)
    - Full Conversational Memory
    - User Taste Awareness (DNA Profile)
    - Structured Constraint Filtering
    """
    user_msg = payload.get("message", "").strip()
    username = payload.get("username", "guest")
    
    if not user_msg: 
        return {"reply": "I'm listening! What kind of cinematic journey are we going on today?"}
    
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or "YOUR_GEMINI" in gemini_key:
        return await fallback_chat_logic(user_msg)

    # 1. FETCH USER CONTEXT (DNA & HISTORY)
    conn = get_db()
    c = conn.cursor()
    user_id = None
    user_dna = ""
    history_context = ""
    
    c.execute("SELECT id FROM users WHERE username = ?", (username,))
    u_row = c.fetchone()
    if u_row:
        user_id = u_row[0]
        # Get DNA (Top Genres)
        stats = await get_user_stats(username)
        if stats.get("favorite_genres"):
            user_dna = f"User's favorite genres: {', '.join([g['genre'] for g in stats['favorite_genres']])}. "
        
        # Get Chat History (Last 10 messages)
        c.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10", (user_id,))
        rows = c.fetchall()[::-1] # Reverse to chronological
        for role, content in rows:
            history_context += f"{role.upper()}: {content}\n"
    
    # 2. CALL AI
    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel('gemini-flash-latest')
        
        prompt = f"""
        You are the 'StreamFlix Cinematic Master', an elite film critic and a highly collaborative cinema partner. 
        You aren't just a recommendation bot; you are a talking encyclopedia of world cinema.
        
        SYSTEM CONTEXT (User Behavior DNA):
        {user_dna}
        
        CONVERSATION HISTORY:
        {history_context}
        
        CURRENT MESSAGE:
        USER: "{user_msg}"
        
        Your objective:
        1. BE EMOTIONALLY INTELLIGENT: Analyze the user's mood. 
           - If ANGRY/STRESSED: Recommend lighthearted COMEDIES or FEEL-GOOD movies to diffuse the tension.
           - If SAD: Recommend INSPIRATIONAL or HEARTWARMING stories.
           - If HAPPY: Recommend VIBRANT, EXHILARATING, or CELEBRATORY cinema.
        2. BE COLLABORATIVE: Discuss how these choices align with their current mood AND their lifelong behavioral DNA (e.g., "I know you love Noir, but since you're feeling a bit frustrated, let's try a dry British comedy instead...").
        3. ANSWER QUESTIONS: Directly answer factual questions about cinema.
        4. REFERENCE MEMORY: Maintain a flowing, multi-turn partnership.
        
        Return JSON ONLY:
        {{
          "reply": "A collaborative, expert response. Max 3-4 sentences. Include facts if requested.",
          "search_query": "Optimized keywords for verification",
          "intent": "qa" | "recommendation" | "search" | "similar" | "person" | "action",
          "action_command": "watchlist_add" | null,
          "target_title": "string",
          "target_person": "string",
          "constraints": {{
            "genres": ["string"],
            "year_range": [start, end],
            "max_runtime": minutes,
            "min_vote_average": 7.5
          }}
        }}
        """
        
        response = await asyncio.to_thread(
            model.generate_content, 
            prompt, 
            generation_config={"response_mime_type": "application/json"}
        )
        
        import json
        import re
        text = response.text
        # Robust JSON extraction
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            ai_data = json.loads(json_match.group(0))
        else:
            ai_data = json.loads(text)
        
        # 3. PERSIST HISTORY
        if user_id:
            now = datetime.utcnow().isoformat()
            c.execute("INSERT INTO chat_history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)", (user_id, "user", user_msg, now))
            c.execute("INSERT INTO chat_history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)", (user_id, "ai", ai_data["reply"], now))
            conn.commit()

        # 4. EXECUTE ACTIONS
        if ai_data.get("action_command") == "watchlist_add" and ai_data.get("target_title"):
            best = await tmdb_search_first(ai_data["target_title"])
            if best:
                await toggle_watchlist(WatchlistAction(username=username, tmdb_id=int(best["id"])))

        # 5. FETCH & FILTER RECOMMENDATIONS
        reply_intro = ai_data.get("reply", "I've personalized these picks for you.")
        search_query = ai_data.get("search_query", user_msg)
        target_title = ai_data.get("target_title")
        target_person = ai_data.get("target_person")
        constraints = ai_data.get("constraints", {})
        
        candidates = []
        if target_person or ai_data.get("intent") == "person":
             person_data = await tmdb_search_person(target_person or search_query)
             if person_data.get("results"):
                 candidates.extend(await tmdb_get_movies_by_actor(person_data["results"][0]["id"], limit=20))
        
        if target_title:
            best_match = await tmdb_search_first(target_title)
            if best_match:
                tmdb_id = int(best_match["id"])
                if ai_data.get("intent") == "search":
                    candidates.append(TMDBMovieCard(tmdb_id=tmdb_id, title=best_match.get("title", ""), poster_url=make_img_url(best_match.get("poster_path")), release_date=best_match.get("release_date"), vote_average=best_match.get("vote_average")))
                candidates.extend(await get_similar_movies(tmdb_id, limit=12))

        if len(candidates) < 10:
            try:
                query_vec = tfidf_obj.transform([search_query])
                sim_scores = (tfidf_matrix @ query_vec.T).toarray().ravel()
                for idx in np.argsort(-sim_scores)[:15]:
                    card = await attach_tmdb_card_by_title(str(df.iloc[int(idx)]['title']))
                    if card: candidates.append(card)
            except: pass

        if not candidates:
            tmdb_data = await tmdb_search_movies(search_query)
            candidates = await tmdb_cards_from_results(tmdb_data.get("results", []), limit=12)

        # Apply Filters
        filtered_recs = []
        seen_ids = set()
        tasks = [tmdb_movie_details(c.tmdb_id) for c in candidates[:40]]
        details_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        for details in details_list:
            if not isinstance(details, TMDBMovieDetails) or details.tmdb_id in seen_ids: continue
            match = True
            if "year_range" in constraints and constraints["year_range"]:
                yr = details.release_date[:4] if details.release_date else None
                if yr and yr.isdigit() and not (constraints["year_range"][0] <= int(yr) <= constraints["year_range"][1]): match = False
            if match and "genres" in constraints and constraints["genres"]:
                movie_genres = [g["name"].lower() for g in details.genres]
                if not any(tg.lower() in " ".join(movie_genres) for tg in constraints["genres"]): match = False
            
            if match:
                filtered_recs.append(TMDBMovieCard(tmdb_id=details.tmdb_id, title=details.title, poster_url=details.poster_url, release_date=details.release_date, vote_average=details.vote_average))
                seen_ids.add(details.tmdb_id)
            if len(filtered_recs) >= 6: break

        return {
            "reply": reply_intro,
            "recommendations": filtered_recs or candidates[:6]
        }
            
    except Exception as e:
        logging.error(f"Gemini Chat Error: {e}")
        return await fallback_chat_logic(user_msg)

@app.get("/movie/ai-insight/{tmdb_id}")
async def get_movie_ai_insight(tmdb_id: int, username: str = "guest"):
    """
    GENIUS CONTEXTUAL ASSISTANT:
    Provides a personalized "Cinematic Master's Take" on a specific movie
    based on the user's DNA profile.
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or "YOUR_GEMINI" in gemini_key:
        return {"insight": "Unlock your personalized Cinematic Master's Take by adding your Gemini API key."}

    try:
        details = await tmdb_movie_details(tmdb_id)
        stats = await get_user_stats(username)
        user_dna = ""
        if stats.get("favorite_genres"):
            user_dna = f"The user loves {', '.join([g['genre'] for g in stats['favorite_genres']])}."

        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel('gemini-flash-latest')
        
        prompt = f"""
        You are the 'StreamFlix Cinematic Master'. 
        USER CONTEXT: {user_dna}
        MOVIE TO ANALYZE: "{details.title}"
        OVERVIEW: {details.overview}
        
        Task: Provide a 1-sentence "Master's Take" on why this movie specifically suits (or challenges) the user's unique taste. 
        Be sophisticated, brief, and incredibly insightful.
        """
        
        response = await asyncio.to_thread(model.generate_content, prompt)
        return {"insight": response.text.strip()}
    except Exception as e:
        logging.error(f"AI Insight Error: {e}")
        return {"insight": "A cinematic mystery remains... (Ensure your API key is valid)"}

async def fallback_chat_logic(user_msg: str):
    """
    Expert Fallback Engine: 
    Maintains the 'Master Critic' persona even without LLM connectivity.
    """
    user_msg_lower = user_msg.lower()
    import re
    
    # Identify key keywords for 'Master' persona
    moods = ["happy", "sad", "thrilling", "scary", "epic", "dark", "light"]
    detected_mood = next((m for m in moods if m in user_msg_lower), None)
    
    final_recs = []
    
    # 1. Regex Title Extraction
    like_match = re.search(r"like ([\w\s':\-]+)", user_msg_lower)
    if like_match:
        target_title = like_match.group(1).strip()
        best = await tmdb_search_first(target_title)
        if best:
            final_recs = await get_similar_movies(int(best["id"]), limit=12)
            reply = f"Ah, a connoisseur of **{best['title']}**! That narrative structure is truly sublime. Here are some selections that share that same cinematic heartbeat."
            return {"reply": reply, "recommendations": final_recs[:6]}

    # 2. Mood/Content Fallback
    query_vec = tfidf_obj.transform([user_msg_lower])
    sim_scores = (tfidf_matrix @ query_vec.T).toarray().ravel()
    top_indices = np.argsort(-sim_scores)[:15]
    for idx in top_indices:
        card = await attach_tmdb_card_by_title(str(df.iloc[int(idx)]['title']))
        if card: final_recs.append(card)
    
    mood_reply = f"In this {detected_mood or 'particular'} mood, one must look for cinema that resonates with the soul. I have curated these selections to match your vibration."
    default_reply = "Cinematic mastery is about discovery. I've analyzed the visual and narrative threads of your request and found these fascinating candidates."
    
    return {
        "reply": mood_reply if detected_mood else default_reply,
        "recommendations": final_recs[:6]
    }


# ---------- DASHBOARD BUNDLE (ULTRA PERFORMANCE) ----------
@app.get("/dashboard/{username}")
async def get_dashboard_bundle(username: str):
    """
    Returns ALL data needed for the home screen in a single request.
    Massively reduces round-trips and frontend waiting time.
    """
    # Fetch all recommendations in parallel
    tasks = {
        "foryou": get_personalized_recommendations(username, limit=15),
        "trending": home(category="trending", limit=15),
        "popular": home(category="top_rated", limit=15),
        "collab": get_collaborative_recommendations(username, limit=15),
        "watchlist": get_watchlist(username),
        "history": get_watch_history(username),
        "stats": get_user_stats(username),
        "mood_picks": recommend_by_mood(mood="happy", limit=15), # Pure joy for home feed
        "recent": get_recent_recommendations(username, limit=15) # Show actual recently viewed similar titles
    }

    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    result = {}
    for i, key in enumerate(keys):
        val = values[i]
        if isinstance(val, Exception):
            logging.error(f"Dashboard error for {key}: {val}")
            result[key] = [] if key != "stats" else {}
        else:
            result[key] = val
            
    return result

# Mount Static Files (at the end to avoid route conflicts)
app.mount("/", StaticFiles(directory="web_ui", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
