import asyncio
import os
import sqlite3
import json
import numpy as np
from google import genai
from datetime import datetime
from typing import List, Tuple, Dict, Any
from fastapi import APIRouter, HTTPException, Query

from .models import (
    TMDBMovieCard, TMDBMovieDetails, SearchBundleResponse, 
    TFIDFRecItem, UserSignup, UserAuth, UserAction, 
    RatingAction, WatchlistAction
)
from ..core.tmdb import (
    tmdb_get, tmdb_cards_from_results, tmdb_movie_details, 
    tmdb_search_movies, tmdb_search_person, tmdb_get_movies_by_actor,
    tmdb_search_first, attach_tmdb_card_by_title
)
from ..core.recommender import (
    tfidf_recommend_titles, df, tfidf_obj, tfidf_matrix, 
    TITLE_TO_IDX, TMDB_TO_IDX, calculate_recency_weight
)
from ..core.utils import _norm_title
from ..core.config import DB_PATH
from ..db.database import get_db

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/home", response_model=List[TMDBMovieCard])
async def home(
    category: str = Query("popular"),
    limit: int = Query(24, ge=1, le=50),
):
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

@router.get("/tmdb/search")
async def tmdb_search(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1, le=10),
):
    return await tmdb_search_movies(query=query, page=page)

@router.get("/movie/id/{tmdb_id}", response_model=TMDBMovieDetails)
async def movie_details_route(tmdb_id: int):
    return await tmdb_movie_details(tmdb_id)

@router.get("/search/actor")
async def search_actor(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1, le=10),
):
    return await tmdb_search_person(query=query, page=page)

@router.get("/movies/actor/{actor_id}", response_model=List[TMDBMovieCard])
async def get_movies_by_actor_route(
    actor_id: int,
    limit: int = Query(20, ge=1, le=50),
):
    return await tmdb_get_movies_by_actor(actor_id=actor_id, limit=limit)

@router.get("/recommend/genre", response_model=List[TMDBMovieCard])
async def recommend_genre(
    tmdb_id: int = Query(...),
    limit: int = Query(18, ge=1, le=50),
):
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

@router.get("/recommend/tfidf")
async def recommend_tfidf(
    title: str = Query(..., min_length=1),
    top_n: int = Query(10, ge=1, le=50),
):
    recs = tfidf_recommend_titles(title, top_n=top_n)
    return [{"title": t, "score": s} for t, s in recs]

@router.get("/movie/similar/{tmdb_id}", response_model=List[TMDBMovieCard])
async def get_similar_movies(tmdb_id: int, limit: int = 12):
    details = await tmdb_movie_details(tmdb_id)
    
    tfidf_recs = []
    try:
        recs = tfidf_recommend_titles(details.title, top_n=limit)
        tasks = [attach_tmdb_card_by_title(title) for title, _ in recs]
        cards = await asyncio.gather(*tasks)
        for card in cards:
            if card and card.tmdb_id != tmdb_id:
                tfidf_recs.append(card)
    except Exception:
        pass

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

    combined = []
    seen = {tmdb_id}
    for i in range(max(len(tfidf_recs), len(genre_recs))):
        if i < len(tfidf_recs) and tfidf_recs[i].tmdb_id not in seen:
            combined.append(tfidf_recs[i])
            seen.add(tfidf_recs[i].tmdb_id)
        if i < len(genre_recs) and genre_recs[i].tmdb_id not in seen:
            combined.append(genre_recs[i])
            seen.add(genre_recs[i].tmdb_id)
            
    return combined[:limit]

@router.get("/movie/search", response_model=SearchBundleResponse)
async def search_bundle(
    query: str = Query(..., min_length=1),
    tfidf_top_n: int = Query(12, ge=1, le=30),
    genre_limit: int = Query(12, ge=1, le=30),
):
    best = await tmdb_search_first(query)
    if not best:
        raise HTTPException(status_code=404, detail=f"No TMDB movie found for query: {query}")

    tmdb_id = int(best["id"])
    details = await tmdb_movie_details(tmdb_id)
    tfidf_items: List[TFIDFRecItem] = []

    try:
        recs = tfidf_recommend_titles(details.title, top_n=tfidf_top_n)
        tasks = [attach_tmdb_card_by_title(t) for t, s in recs]
        cards = await asyncio.gather(*tasks)
        for (title, score), card in zip(recs, cards):
            tfidf_items.append(TFIDFRecItem(title=title, score=score, tmdb=card))
    except Exception:
        pass

    genre_recs = []
    if details.genres:
        gid = details.genres[0]["id"]
        disc = await tmdb_get("/discover/movie", {"with_genres": gid, "sort_by": "popularity.desc"})
        genre_recs = await tmdb_cards_from_results(disc.get("results", []), limit=genre_limit)

    return SearchBundleResponse(
        query=query,
        movie_details=details,
        tfidf_recommendations=tfidf_items,
        genre_recommendations=genre_recs
    )

# ---------- AUTH & USER ACTIONS ----------

@router.post("/signup")
async def signup(user: UserSignup):
    if not user.username or not user.password or not user.full_name:
        raise HTTPException(status_code=400, detail="All fields are required")
    if len(user.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(f"INSERT INTO users (username, full_name, password) VALUES ({P}, {P}, {P})", 
                  (user.username, user.full_name, user.password))
        conn.commit()
    except Exception as e:
        conn.close()
        if "unique" in str(e).lower() or "already registered" in str(e).lower():
            raise HTTPException(status_code=400, detail="Username already registered")
        raise HTTPException(status_code=500, detail=f"Signup failed: {e}")
    conn.close()
    return {"message": "User created successfully", "username": user.username, "full_name": user.full_name}

@router.post("/login")
async def login(user: UserAuth):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P} AND password = {P}", (user.username, user.password))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"message": "Login successful", "username": user.username}
    raise HTTPException(status_code=400, detail="Invalid username or password")

@router.post("/user-action")
async def store_user_action(action: UserAction):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (action.username,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    user_id = row[0]
    c.execute(
        f"INSERT INTO user_history (user_id, tmdb_id, action_type, query, timestamp) VALUES ({P}, {P}, {P}, {P}, {P})",
        (user_id, action.tmdb_id, action.action_type, action.query, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "message": f"Action '{action.action_type}' recorded"}

# ---------- PERSONALIZED RECOMMENDATIONS ----------

@router.get("/recommendations/{username}", response_model=List[TMDBMovieCard])
async def get_personalized_recommendations(username: str, limit: int = 18):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (username,))
    u_row = c.fetchone()
    if not u_row: return await home(category="popular", limit=limit)
    user_id = u_row[0]

    c.execute(f"SELECT tmdb_id, rating FROM user_ratings WHERE user_id = {P}", (user_id,))
    ratings = {row[0]: row[1] for row in c.fetchall()}
    c.execute(f"SELECT tmdb_id, action_type, timestamp, query FROM user_history WHERE user_id = {P} ORDER BY timestamp DESC LIMIT 500", (user_id,))
    actions = c.fetchall()

    if not actions and not ratings: return await home(category="trending", limit=limit)

    scores = {}
    search_intents = []
    action_weights = {"like": 10.0, "watchlist": 7.0, "view": 3.0, "search": 2.0, "rating_update": 5.0}

    for tmdb_id, action_type, timestamp, query in actions:
        base_weight = action_weights.get(action_type, 1.0)
        recency_mult = calculate_recency_weight(timestamp)
        if action_type == "search" and query:
            search_intents.append((query, recency_mult))
            continue
        rating_mult = 1.0
        if tmdb_id in ratings:
            r = ratings[tmdb_id]
            rating_mult = 2.5 if r >= 4 else (1.0 if r == 3 else -2.0)
        scores[tmdb_id] = scores.get(tmdb_id, 0) + (base_weight * recency_mult * rating_mult)

    if tfidf_matrix is None or TMDB_TO_IDX is None: return await home(category="popular", limit=limit)

    user_vector = None
    interacted_indices = set()
    for t_id, weight in scores.items():
        idx = TMDB_TO_IDX.get(t_id)
        if idx is None:
            try:
                details = await tmdb_movie_details(t_id)
                idx = TITLE_TO_IDX.get(_norm_title(details.title))
            except: pass
        if idx is not None:
            interacted_indices.add(idx)
            scaled = tfidf_matrix[idx] * weight
            user_vector = (user_vector + scaled) if user_vector is not None else scaled.copy()

    if search_intents and tfidf_obj:
        for q, w in search_intents:
            try:
                user_vector = (user_vector + (tfidf_obj.transform([q.lower()]) * (w * 3.5))) if user_vector is not None else (tfidf_obj.transform([q.lower()]) * (w * 3.5))
            except: pass

    if user_vector is None: return await home(category="popular", limit=limit)
    norm = np.linalg.norm(user_vector.toarray())
    if norm > 0: user_vector = user_vector / norm
    
    sim_scores = (tfidf_matrix @ user_vector.T).toarray().ravel()
    order = np.argsort(-sim_scores)
    
    recs = []
    for i in order:
        if i in interacted_indices: continue
        if len(recs) >= limit: break
        try:
            row = df.iloc[int(i)]
            recs.append((str(row["title"]), int(row["id"]) if "id" in df.columns else None))
        except: continue

    tasks = [tmdb_movie_details(m_id) if m_id else attach_tmdb_card_by_title(title) for title, m_id in recs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            final_cards.append(TMDBMovieCard(tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, release_date=res.release_date, vote_average=res.vote_average))
        elif isinstance(res, TMDBMovieCard): final_cards.append(res)
    return final_cards[:limit]

@router.get("/recommendations/genres/{username}", response_model=List[TMDBMovieCard])
async def get_genre_based_recommendations(username: str, limit: int = 18):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        return await home(category="trending", limit=limit)
    user_id = u_row[0]
    c.execute(f"SELECT tmdb_id FROM user_history WHERE user_id = {P} AND action_type = 'like' LIMIT 20", (user_id,))
    liked_movies = [row[0] for row in c.fetchall()]
    conn.close()
    if not liked_movies: return await home(category="trending", limit=limit)
    
    genre_freq = {}
    for tid in liked_movies:
        try:
            d = await tmdb_movie_details(tid)
            for g in d.genres: genre_freq[g["id"]] = genre_freq.get(g["id"], 0) + 1
        except: continue
    
    if not genre_freq: return await home(category="trending", limit=limit)
    top_genres = sorted(genre_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    genre_str = ",".join(str(g[0]) for g in top_genres)
    
    disc = await tmdb_get("/discover/movie", {"with_genres": genre_str, "sort_by": "popularity.desc", "page": 1})
    cards = await tmdb_cards_from_results(disc.get("results", []), limit=limit)
    return [c for c in cards if c.tmdb_id not in liked_movies]

@router.get("/recommendations/collaborative/{username}", response_model=List[TMDBMovieCard])
async def get_collaborative_recommendations(username: str, limit: int = 12):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (username,))
    u_row = c.fetchone()
    if not u_row: return await home(category="popular", limit=limit)
    user_id = u_row[0]
    c.execute(f"SELECT tmdb_id FROM user_history WHERE user_id = {P} AND action_type IN ('view', 'like')", (user_id,))
    user_movies = set(row[0] for row in c.fetchall())
    if not user_movies: return await home(category="popular", limit=limit)
    
    watched_list = list(user_movies)[:20]
    placeholders = ",".join(P for _ in range(len(watched_list)))
    c.execute(f"SELECT user_id, COUNT(*) as c FROM user_history WHERE tmdb_id IN ({placeholders}) GROUP BY user_id HAVING c >= 2 AND user_id != {P} ORDER BY c DESC LIMIT 50", watched_list + [user_id])
    sim_users = [row[0] for row in c.fetchall()]
    if not sim_users: return await home(category="popular", limit=limit)
    
    placeholders = ",".join(P for _ in range(len(sim_users)))
    c.execute(f"SELECT tmdb_id, COUNT(*) as lc FROM user_history WHERE user_id IN ({placeholders}) AND action_type = 'like' GROUP BY tmdb_id ORDER BY lc DESC LIMIT {P}", sim_users + [limit * 3])
    collab_ids = [row[0] for row in c.fetchall() if row[0] not in user_movies]
    
    tasks = [tmdb_movie_details(m_id) for m_id in collab_ids[:limit]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            cards.append(TMDBMovieCard(tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, release_date=res.release_date, vote_average=res.vote_average))
    return cards or await home(category="popular", limit=limit)

# ---------- STATS & ANALYTICS ----------
@router.get("/user/stats/{username}")
async def get_user_stats(username: str):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (username,))
    u_row = c.fetchone()
    if not u_row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    user_id = u_row[0]
    c.execute(f"SELECT action_type, COUNT(*) FROM user_history WHERE user_id = {P} GROUP BY action_type", (user_id,))
    counts = {row[0]: row[1] for row in c.fetchall()}
    c.execute(f"SELECT tmdb_id, action_type FROM user_history WHERE user_id = {P} AND action_type IN ('like', 'view') ORDER BY timestamp DESC LIMIT 50", (user_id,))
    recent = c.fetchall()
    conn.close()
    
    tasks = [tmdb_movie_details(m_id) for m_id, _ in recent]
    details = await asyncio.gather(*tasks, return_exceptions=True)
    
    genre_freq = {}
    for i, d in enumerate(details):
        if isinstance(d, TMDBMovieDetails):
            w = 3 if recent[i][1] == 'like' else 1
            for g in d.genres: genre_freq[g["name"]] = genre_freq.get(g["name"], 0) + w
    
    top_genres = sorted(genre_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    max_v = top_genres[0][1] if top_genres else 1
    dna = [{"genre": g, "count": v, "percent": int((v/max_v)*100)} for g, v in top_genres]
    
    score = counts.get('view', 0) + (counts.get('like', 0) * 10)
    return {"username": username, "total_interactions": sum(counts.values()), "action_breakdown": counts, "favorite_genres": dna, "level": (score // 100) + 1, "score": score}

# ---------- MOOD & CHAT ----------

MOOD_MAP = {
    "happy": "joyful funny upbeat lighthearted cheerful comedy animation family feel-good",
    "sad": "emotional moving dramatic melancholy touching tearjerker tragedy",
    "motivated": "inspiring triumph success struggle perseverance goal ambitious",
    "romantic": "love romance dating relationship passion marriage intimate",
    "thrilled": "suspense mystery intense thriller crime chase investigation shock twist",
    "scared": "horror dark scary ghost supernatural nightmare fear survival",
    "epic": "adventure war grand journey historical battle hero legendary world",
    "chill": "peaceful calm artistic document nature slow ambient relaxing",
}

@router.get("/recommend/mood")
async def recommend_by_mood(mood: str = Query(...), limit: int = 18):
    mood_key = mood.lower()
    search_text = MOOD_MAP.get(mood_key, mood_key)
    if tfidf_obj is None or tfidf_matrix is None: return await home(category="popular", limit=limit)
    
    try:
        qv = tfidf_obj.transform([search_text])
        scores = (tfidf_matrix @ qv.T).toarray().ravel()
        order = np.argsort(-scores)[:limit*2]
        
        recs = []
        for i in order:
            row = df.iloc[int(i)]
            recs.append((str(row['title']), int(row['id']) if 'id' in df.columns else None))
            if len(recs) >= limit: break
            
        tasks = [tmdb_movie_details(m_id) if m_id else attach_tmdb_card_by_title(title) for title, m_id in recs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final = []
        for res in results:
            if isinstance(res, TMDBMovieDetails):
                final.append(TMDBMovieCard(tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, release_date=res.release_date, vote_average=res.vote_average))
            elif isinstance(res, TMDBMovieCard): final.append(res)
        return final
    except: return await home(category="popular", limit=limit)

@router.get("/rating/{username}/{tmdb_id}")
async def get_user_movie_rating(username: str, tmdb_id: int):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (username,))
    u_row = c.fetchone()
    if not u_row: return {"rating": 0, "in_watchlist": False}
    user_id = u_row[0]
    
    c.execute(f"SELECT rating FROM user_ratings WHERE user_id = {P} AND tmdb_id = {P}", (user_id, tmdb_id))
    r_row = c.fetchone()
    rating = r_row[0] if r_row else 0
    
    c.execute(f"SELECT 1 FROM user_watchlist WHERE user_id = {P} AND tmdb_id = {P}", (user_id, tmdb_id))
    in_watchlist = bool(c.fetchone())
    conn.close()
    return {"rating": rating, "in_watchlist": in_watchlist}

@router.get("/movie/ai-insight/{tmdb_id}")
async def get_ai_insight(tmdb_id: int, username: str = "guest"):
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or "YOUR_GEMINI" in gemini_key:
        return {"insight": "Our AI Master is currently resting. He says this movie looks promising!"}
    
    try:
        details = await tmdb_movie_details(tmdb_id)
        client = genai.Client(api_key=gemini_key)
        prompt = f"User is asking about '{details.title}' ({details.overview}). Give a one-sentence witty cinematic insight why they should or shouldn't watch it."
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return {"insight": response.text.strip()}
    except Exception as e:
        logging.error(f"AI Insight error: {e}")
        return {"insight": "The cinematic oracle is hazy. Trust your gut on this one!"}

@router.post("/chat")
async def chat_assistant(payload: Dict[str, str]):
    user_msg = payload.get("message", "").strip()
    username = payload.get("username", "guest")
    gemini_key = os.getenv("GEMINI_API_KEY")
    
    if not gemini_key or "YOUR_GEMINI" in gemini_key:
        return {"reply": "I'm your cinematic master! Without my AI brain (API key), I can only offer limited advice.", "recommendations": []}

    try:
        client = genai.Client(api_key=gemini_key)
        prompt = f"You are a cinematic expert. User says: {user_msg}. Respond briefly and suggest search terms. Return JSON: {{'reply': '...', 'search_query': '...'}}"
        response = client.models.generate_content(
            model='gemini-2.0-flash', 
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        ai_data = json.loads(response.text)
        
        tmdb_data = await tmdb_search_movies(ai_data.get("search_query", user_msg))
        recs = await tmdb_cards_from_results(tmdb_data.get("results", []), limit=6)
        return {"reply": ai_data["reply"], "recommendations": recs}
    except Exception as e:
        logging.error(f"Chat error: {e}")
        return {"reply": "Cinema is vast and mysterious. I'm having trouble thinking clearly right now.", "recommendations": []}

# ---------- DASHBOARD ----------
@router.get("/dashboard/{username}")
async def get_dashboard_bundle(username: str):
    tasks = {
        "foryou": get_personalized_recommendations(username, limit=15),
        "trending": home(category="trending", limit=15),
        "popular": home(category="top_rated", limit=15),
        "collab": get_collaborative_recommendations(username, limit=15),
        "stats": get_user_stats(username)
    }
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values(), return_exceptions=True)
    result = {}
    for i, key in enumerate(keys):
        result[key] = values[i] if not isinstance(values[i], Exception) else ([] if key != "stats" else {})
    return result

# ---------- ACTIONS ----------
@router.post("/rating")
async def store_rating(action: RatingAction):
    from ..db.database import P, DATABASE_URL
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (action.username,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    user_id = row[0]
    if DATABASE_URL:
        # PostgreSQL Upsert
        c.execute(f"INSERT INTO user_ratings (user_id, tmdb_id, rating) VALUES ({P}, {P}, {P}) ON CONFLICT(user_id, tmdb_id) DO UPDATE SET rating=EXCLUDED.rating", (user_id, action.tmdb_id, action.rating))
    else:
        # SQLite Upsert
        c.execute(f"INSERT INTO user_ratings (user_id, tmdb_id, rating) VALUES ({P}, {P}, {P}) ON CONFLICT(user_id, tmdb_id) DO UPDATE SET rating=excluded.rating", (user_id, action.tmdb_id, action.rating))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@router.post("/watchlist/toggle")
async def toggle_watchlist(action: WatchlistAction):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (action.username,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    user_id = row[0]
    c.execute(f"SELECT 1 FROM user_watchlist WHERE user_id = {P} AND tmdb_id = {P}", (user_id, action.tmdb_id))
    if c.fetchone():
        c.execute(f"DELETE FROM user_watchlist WHERE user_id = {P} AND tmdb_id = {P}", (user_id, action.tmdb_id))
        s = "removed"
    else:
        c.execute(f"INSERT INTO user_watchlist (user_id, tmdb_id) VALUES ({P}, {P})", (user_id, action.tmdb_id))
        s = "added"
    conn.commit()
    conn.close()
    return {"status": "ok", "action": s}

@router.get("/watchlist/{username}", response_model=List[TMDBMovieCard])
async def get_watchlist(username: str):
    from ..db.database import P
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT id FROM users WHERE username = {P}", (username,))
    u_row = c.fetchone()
    if not u_row: 
        conn.close()
        return []
    c.execute(f"SELECT tmdb_id FROM user_watchlist WHERE user_id = {P} LIMIT 50", (u_row[0],))
    ids = [row[0] for row in c.fetchall()]
    conn.close()
    tasks = [tmdb_movie_details(m_id) for m_id in ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    cards = []
    for res in results:
        if isinstance(res, TMDBMovieDetails):
            cards.append(TMDBMovieCard(tmdb_id=res.tmdb_id, title=res.title, poster_url=res.poster_url, release_date=res.release_date, vote_average=res.vote_average))
    return cards
