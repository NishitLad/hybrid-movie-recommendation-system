import pandas as pd
import numpy as np
import pickle
import ast
from sklearn.feature_extraction.text import TfidfVectorizer

import os

print("Checking for latest processed data...")
if os.path.exists('df_science_ready.pkl'):
    print("Loading df_science_ready.pkl...")
    with open('df_science_ready.pkl', 'rb') as f:
        df = pickle.load(f)
elif os.path.exists('df_processed.pkl'):
    print("Loading df_processed.pkl...")
    with open('df_processed.pkl', 'rb') as f:
        df = pickle.load(f)
else:
    print("Loading df.pkl...")
    with open('df.pkl', 'rb') as f:
        df = pickle.load(f)

print(f"Data shape: {df.shape}")

# Safely extract genres
def safe_extract_genres(x):
    if isinstance(x, list):
        return " ".join(x)
    if pd.isna(x) or not isinstance(x, str):
        return ""
    try:
        if x.startswith('['):
            genres = ast.literal_eval(x)
            if isinstance(genres, list):
                return " ".join([g['name'] for g in genres if 'name' in g])
        return x
    except (ValueError, SyntaxError):
        return ""

print("Processing features...")
if 'soup' in df.columns:
    print("Using existing 'soup' column...")
    df['soup'] = df['soup'].fillna("").astype(str)
else:
    print("Extracting genres and building 'soup'...")
    if 'genres' in df.columns:
        df['genre_names'] = df['genres'].apply(safe_extract_genres)
    else:
        df['genre_names'] = ""

    if 'overview' not in df.columns:
        df['overview'] = ""

    # Combine overview, tagline, tags and genres for a rich feature soup
    df['tagline'] = df['tagline'].fillna("").astype(str)
    df['tags'] = df['tags'].fillna("").astype(str)
    df['soup'] = df['overview'] + " " + df['tagline'] + " " + df['tags'] + " " + df['genre_names']

print("Building TF-IDF Matrix...")
# Limit features to 10,000 to keep it incredibly fast and light, drop english stop words
tfidf = TfidfVectorizer(stop_words='english', max_features=10000)
tfidf_matrix = tfidf.fit_transform(df['soup'])

# Build new indices dict
indices = pd.Series(df.index, index=df['title']).drop_duplicates()
indices_dict = indices.to_dict()

print(f"New TF-IDF Matrix shape: {tfidf_matrix.shape}")

print("Saving models...")
with open('tfidf_matrix.pkl', 'wb') as f:
    pickle.dump(tfidf_matrix, f, protocol=4)

with open('indices.pkl', 'wb') as f:
    pickle.dump(indices_dict, f, protocol=4)

with open('df.pkl', 'wb') as f:
    # only keep necessary columns to save space if needed, but for now we keep df as it is
    pickle.dump(df, f, protocol=4)

with open('tfidf.pkl', 'wb') as f:
    pickle.dump(tfidf, f, protocol=4)

print("Done! Models rebuilt successfully.")
