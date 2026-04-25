import os
import sqlite3
import logging
import psycopg2
from ..core.config import DB_PATH

# Detect if we should use PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
P = "%s" if DATABASE_URL else "?"

def get_db():
    """
    Returns a connection. Use database.P for the placeholder (?, %s)
    """
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
        return conn

def init_db():
    conn = get_db()
    p = P
    c = conn.cursor()
    
    pk_serial = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    
    # Tables
    tables = [
        f"CREATE TABLE IF NOT EXISTS users (id {pk_serial}, username TEXT UNIQUE, full_name TEXT, password TEXT)",
        f"CREATE TABLE IF NOT EXISTS user_history (id {pk_serial}, user_id INTEGER, tmdb_id INTEGER, action_type TEXT, query TEXT, timestamp TEXT)",
        "CREATE TABLE IF NOT EXISTS user_ratings (user_id INTEGER, tmdb_id INTEGER, rating FLOAT, UNIQUE(user_id, tmdb_id))",
        "CREATE TABLE IF NOT EXISTS user_watchlist (user_id INTEGER, tmdb_id INTEGER, UNIQUE(user_id, tmdb_id))",
        "CREATE TABLE IF NOT EXISTS tmdb_cache (cache_key TEXT PRIMARY KEY, response_json TEXT, timestamp TEXT)",
        f"CREATE TABLE IF NOT EXISTS chat_history (id {pk_serial}, user_id INTEGER, role TEXT, content TEXT, timestamp TEXT)"
    ]
    
    for table in tables:
        c.execute(table)
    
    conn.commit()
    conn.close()
    logging.info(f"Database initialized successfully ({'PostgreSQL' if DATABASE_URL else 'SQLite'})")
