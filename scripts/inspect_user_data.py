import sqlite3
import pandas as pd
import os

# Path to the database
DB_PATH = 'app.db'

def inspect_user_data():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # 1. Get List of Tables
    print("--- Database Tables ---")
    tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table';", conn)
    print(tables)
    print("\n")

    # 2. Inspect Users
    if 'users' in tables['name'].values:
        print("--- User Accounts ---")
        users = pd.read_sql_query("SELECT id, username, full_name FROM users;", conn)
        print(users)
        print("\n")
    
    # 3. Inspect Ratings
    if 'user_ratings' in tables['name'].values:
        print("--- User Ratings (Sample) ---")
        ratings = pd.read_sql_query("SELECT * FROM user_ratings LIMIT 10;", conn)
        print(ratings)
        print("\n")

    # 4. Inspect Watchlist
    if 'user_watchlist' in tables['name'].values:
        print("--- User Watchlist (Sample) ---")
        watchlist = pd.read_sql_query("SELECT * FROM user_watchlist LIMIT 10;", conn)
        print(watchlist)
        print("\n")

    # 5. Inspect History
    if 'user_history' in tables['name'].values:
        print("--- User History (Sample) ---")
        history = pd.read_sql_query("SELECT * FROM user_history ORDER BY timestamp DESC LIMIT 10;", conn)
        print(history)
        print("\n")

    conn.close()

if __name__ == "__main__":
    inspect_user_data()
