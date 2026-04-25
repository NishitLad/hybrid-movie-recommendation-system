import pickle
import numpy as np
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from fastapi import HTTPException
from .utils import _norm_title
from .config import DF_PATH, INDICES_PATH, TFIDF_MATRIX_PATH, TFIDF_PATH

# State
df: Optional[pd.DataFrame] = None
indices_obj: Any = None
tfidf_matrix: Any = None
tfidf_obj: Any = None
TITLE_TO_IDX: Optional[Dict[str, int]] = None
TMDB_TO_IDX: Optional[Dict[int, int]] = None 
IDX_TO_TMDB: Optional[Dict[int, int]] = None

def load_resources():
    global df, indices_obj, tfidf_matrix, tfidf_obj, TITLE_TO_IDX, TMDB_TO_IDX, IDX_TO_TMDB
    
    with open(DF_PATH, "rb") as f:
        df = pickle.load(f)

    with open(INDICES_PATH, "rb") as f:
        indices_obj = pickle.load(f)

    with open(TFIDF_MATRIX_PATH, "rb") as f:
        tfidf_matrix = pickle.load(f)

    with open(TFIDF_PATH, "rb") as f:
        tfidf_obj = pickle.load(f)

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

    if df is None or "title" not in df.columns:
        raise RuntimeError("df.pkl must contain a DataFrame with a 'title' column")

def build_title_to_idx_map(indices: Any) -> Dict[str, int]:
    title_to_idx: Dict[str, int] = {}
    if isinstance(indices, dict):
        for k, v in indices.items():
            title_to_idx[_norm_title(k)] = int(v)
        return title_to_idx

    try:
        for k, v in indices.items():
            title_to_idx[_norm_title(k)] = int(v)
        return title_to_idx
    except Exception:
        raise RuntimeError("indices.pkl must be dict or pandas Series-like")

def get_local_idx_by_title(title: str) -> int:
    if TITLE_TO_IDX is None:
        raise HTTPException(status_code=500, detail="TF-IDF index map not initialized")
    key = _norm_title(title)
    if key in TITLE_TO_IDX:
        return int(TITLE_TO_IDX[key])
    raise HTTPException(status_code=404, detail=f"Title not found in local dataset: '{title}'")

def calculate_recency_weight(timestamp_str: str) -> float:
    """
    Calculate weight based on recency. Recent actions have higher weight.
    Decays over time (7 days half-life)
    """
    try:
        ts = datetime.fromisoformat(timestamp_str)
        age = datetime.utcnow() - ts
        days_old = age.total_seconds() / 86400
        
        # Exponential decay: 7-day half-life for general actions
        weight = 2 ** (-days_old / 7.0)
        return max(weight, 0.15)
    except Exception:
        return 1.0

def tfidf_recommend_titles(query_title: str, top_n: int = 10) -> List[Tuple[str, float]]:
    global df, tfidf_matrix
    if df is None or tfidf_matrix is None:
        raise HTTPException(status_code=500, detail="TF-IDF resources not loaded")

    idx = get_local_idx_by_title(query_title)
    qv = tfidf_matrix[idx]
    scores = (tfidf_matrix @ qv.T).toarray().ravel()

    max_score = np.max(scores)
    if max_score > 0:
        scores = scores / max_score
    
    quality_threshold = 0.15
    order = np.argsort(-scores)
    out: List[Tuple[str, float]] = []
    
    for i in order:
        if int(i) == int(idx):
            continue
        
        score = scores[int(i)]
        if score < quality_threshold and len(out) >= top_n // 2:
            continue
            
        try:
            title_i = str(df.iloc[int(i)]["title"])
            if len(out) < top_n:
                out.append((title_i, float(score)))
            if len(out) >= top_n * 2:
                break
        except Exception:
            continue
    
    return sorted(out, key=lambda x: x[1], reverse=True)[:top_n]
