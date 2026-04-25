import os
import sqlite3
import psycopg2
import logging
from .config import DB_PATH

DATABASE_URL = os.getenv("DATABASE_URL")

# Handle the postgres:// vs postgresql:// issue for SQLAlchemy/Psycopg2
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Global placeholder
P = "%s" if DATABASE_URL else "?"

def get_db():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
        return conn

def init_db():
    if DATABASE_URL:
        logging.info("Initializing PostgreSQL database...")
        try:
            conn = psycopg2.connect(DATABASE_URL)
            logging.info("Connected to PostgreSQL successfully.")
        except Exception as e:
            logging.error(f"Failed to connect to PostgreSQL: {e}")
            return
    else:
        logging.info("Initializing SQLite database...")
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()
    try:
        # Table definitions
        pk = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
        
        c.execute(f'''CREATE TABLE IF NOT EXISTS users (
            id {pk},
            username TEXT UNIQUE NOT NULL,
            full_name TEXT,
            password TEXT NOT NULL
        )''')

        c.execute(f'''CREATE TABLE IF NOT EXISTS user_history (
            id {pk},
            user_id INTEGER,
            tmdb_id INTEGER,
            action_type TEXT,
            query TEXT,
            timestamp TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS user_ratings (
            user_id INTEGER,
            tmdb_id INTEGER,
            rating REAL,
            PRIMARY KEY (user_id, tmdb_id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS user_watchlist (
            user_id INTEGER,
            tmdb_id INTEGER,
            PRIMARY KEY (user_id, tmdb_id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS tmdb_cache (
            endpoint TEXT PRIMARY KEY,
            data TEXT,
            expiry TEXT
        )''')

        conn.commit()
        logging.info(f"Database initialized successfully ({'PostgreSQL' if DATABASE_URL else 'SQLite'})")
    except Exception as e:
        logging.error(f"Error during table initialization: {e}")
    finally:
        conn.close()
