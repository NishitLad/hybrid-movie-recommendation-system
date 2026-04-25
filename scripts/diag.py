
import os
import pickle
import sqlite3
import httpx
import asyncio
import pandas as pd
from dotenv import load_dotenv
import sys

# Ensure UTF-8 output if possible
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def run_diagnostic():
    print("MOVIEFLIX SYSTEM DIAGNOSTIC")
    print("==============================\n")

    # 1. Environment
    load_dotenv()
    tmdb_key = os.getenv("TMDB_API_KEY")
    print(f"Checking .env... {'[OK] Found TMDB Key' if tmdb_key else '[ERROR] TMDB Key Missing'}")
    
    # 2. Files
    files = ["main.py", "df.pkl", "indices.pkl", "tfidf.pkl", "tfidf_matrix.pkl", "app.db", "web_ui/index.html"]
    for f in files:
        path = os.path.join(os.getcwd(), f)
        exists = os.path.exists(path)
        print(f"File {f}: {'[OK] Exists' if exists else '[ERROR] MISSING'}")
        if exists and f.endswith(".pkl"):
            try:
                with open(path, "rb") as p:
                    obj = pickle.load(p)
                print(f"  -> Pickle {f}: [OK] Loadable ({type(obj)})")
            except Exception as e:
                print(f"  -> Pickle {f}: [ERROR] CORRUPTED ({e})")

    # 3. Database
    try:
        conn = sqlite3.connect("app.db")
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        print(f"Database: [OK] Connected. Tables: {tables}")
        conn.close()
    except Exception as e:
        print(f"Database: [ERROR] FAILED ({e})")

    # 4. Connectivity
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"https://api.themoviedb.org/3/movie/popular?api_key={tmdb_key}")
            if r.status_code == 200:
                print(f"TMDB API: [OK] Reachable (Status 200)")
            else:
                print(f"TMDB API: [ERROR] ERROR (Status {r.status_code})")
    except Exception as e:
        print(f"TMDB API: [ERROR] CONNECTION FAILED ({e})")

    print("\n==============================")
    print("DIAGNOSTIC COMPLETE")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
