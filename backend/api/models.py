from pydantic import BaseModel
from typing import Optional, List, Dict, Any

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
