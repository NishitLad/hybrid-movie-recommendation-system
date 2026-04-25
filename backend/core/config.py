import os
from dotenv import load_dotenv

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_500 = "https://image.tmdb.org/t/p/w500"

if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY missing. Put it in .env as TMDB_API_KEY=xxxx")

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # backend/
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROCESSED_DATA_DIR = os.path.join(DATA_DIR, "processed")
DB_DIR = os.path.join(DATA_DIR, "database")

DF_PATH = os.path.join(PROCESSED_DATA_DIR, "df.pkl")
INDICES_PATH = os.path.join(PROCESSED_DATA_DIR, "indices.pkl")
TFIDF_MATRIX_PATH = os.path.join(PROCESSED_DATA_DIR, "tfidf_matrix.pkl")
TFIDF_PATH = os.path.join(PROCESSED_DATA_DIR, "tfidf.pkl")

DB_PATH = os.path.join(DB_DIR, "app.db")
