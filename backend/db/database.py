import sqlite3
import logging
from datetime import datetime
from ..core.config import DB_PATH

# Global connection pool (simple version for SQLite)
_DB_CONN = None

def get_db():
    global _DB_CONN
    if _DB_CONN is None:
        _DB_CONN = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    return _DB_CONN

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            full_name TEXT,
            password TEXT
        )
    ''')
    # Add full_name column to existing table if it doesn't exist
    try:
        c.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    
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
    
    # Add query column to existing table if it doesn't exist
    try:
        c.execute("ALTER TABLE user_history ADD COLUMN query TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
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

    # ADD PERSISTENT TMDB CACHE TABLE
    c.execute('''
        CREATE TABLE IF NOT EXISTS tmdb_cache (
            cache_key TEXT PRIMARY KEY,
            response_json TEXT,
            timestamp DATETIME
        )
    ''')

    # ADD CHAT HISTORY TABLE FOR CONTEXT/MEMORY
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
