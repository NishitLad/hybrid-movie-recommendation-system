import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import ast
import os
import logging
from datetime import datetime

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def safe_parse_json(x):
    try:
        if isinstance(x, str) and (x.startswith('[') or x.startswith('{')):
            return ast.literal_eval(x)
        return []
    except (ValueError, SyntaxError):
        return []

def extract_names(x):
    if isinstance(x, list):
        return [i['name'] for i in x]
    return []

def perform_cleaning(df):
    logging.info("--- Phase 1: Cleaning ---")
    
    # 1. Drop highly sparse or irrelevant columns
    cols_to_drop = ['homepage', 'poster_path', 'video', 'imdb_id', 'status', 'spoken_languages', 'original_title']
    df = df.drop(columns=[c for c in cols_to_drop if c in df.columns])
    
    # 2. Handle Numeric Columns
    # Budget and Revenue might have some string values like '0' or bad data
    df['budget'] = pd.to_numeric(df['budget'], errors='coerce').fillna(0)
    df['revenue'] = pd.to_numeric(df['revenue'], errors='coerce').fillna(0)
    df['popularity'] = pd.to_numeric(df['popularity'], errors='coerce').fillna(0)
    df['vote_average'] = pd.to_numeric(df['vote_average'], errors='coerce').fillna(0)
    df['vote_count'] = pd.to_numeric(df['vote_count'], errors='coerce').fillna(0)
    
    # 3. Handle Dates
    df['release_date'] = pd.to_datetime(df['release_date'], errors='coerce')
    df['release_year'] = df['release_date'].dt.year.fillna(0).astype(int)
    
    # 4. Handle Categorical / JSON
    df['genres'] = df['genres'].apply(safe_parse_json).apply(extract_names)
    df['production_companies'] = df['production_companies'].apply(safe_parse_json).apply(extract_names)
    
    # 5. Missing values in Text
    df['title'] = df['title'].fillna("Unknown")
    df['overview'] = df['overview'].fillna("")
    df['tagline'] = df['tagline'].fillna("")
    
    # 6. Remove duplicates
    initial_len = len(df)
    df = df.drop_duplicates(subset=['id']).drop_duplicates(subset=['title', 'release_year'])
    logging.info(f"Removed {initial_len - len(df)} duplicate records.")
    
    # 7. Filter out extremely low-quality data (optional but good for EDA)
    # Keeping only movies with titles
    df = df[df['title'] != 'Unknown']
    
    logging.info(f"Cleaning complete. Remaining rows: {len(df)}")
    return df

def perform_eda(df, output_dir='eda_advanced'):
    logging.info("--- Phase 2: EDA ---")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    sns.set_theme(style="darkgrid")
    plt.rcParams['figure.figsize'] = (12, 8)

    # 1. Distribution of Ratings
    plt.figure()
    sns.histplot(df[df['vote_count'] > 10]['vote_average'], bins=30, kde=True, color='skyblue')
    plt.title('Distribution of Movie Ratings (Movies with >10 votes)')
    plt.savefig(f'{output_dir}/ratings_dist.png')
    plt.close()

    # 2. Revenue vs Budget (Profitability)
    # Only for movies with non-zero budget and revenue
    money_df = df[(df['budget'] > 1000) & (df['revenue'] > 1000)]
    plt.figure()
    sns.scatterplot(data=money_df, x='budget', y='revenue', alpha=0.5, color='green')
    plt.xscale('log')
    plt.yscale('log')
    plt.title('Revenue vs Budget (Log Scale)')
    plt.savefig(f'{output_dir}/revenue_vs_budget.png')
    plt.close()

    # 3. Top Genres
    all_genres = [g for sublist in df['genres'] for g in sublist]
    genre_counts = pd.Series(all_genres).value_counts().head(10)
    plt.figure()
    sns.barplot(x=genre_counts.values, y=genre_counts.index, palette='viridis')
    plt.title('Top 10 Genres')
    plt.savefig(f'{output_dir}/top_genres.png')
    plt.close()

    # 4. Movies released per Year (Post 1950)
    year_counts = df[df['release_year'] > 1950]['release_year'].value_counts().sort_index()
    plt.figure()
    year_counts.plot(kind='line', marker='o', markersize=2)
    plt.title('Number of Movies Released per Year (1950-Present)')
    plt.xlabel('Year')
    plt.ylabel('Count')
    plt.savefig(f'{output_dir}/release_trends.png')
    plt.close()

    # 5. Correlation Heatmap
    plt.figure(figsize=(10, 8))
    numeric_cols = ['budget', 'revenue', 'popularity', 'vote_average', 'vote_count', 'runtime']
    corr = df[numeric_cols].corr()
    sns.heatmap(corr, annot=True, cmap='coolwarm', fmt=".2f")
    plt.title('Correlation Matrix of Numeric Features')
    plt.savefig(f'{output_dir}/correlation.png')
    plt.close()

    logging.info("EDA plots saved to 'eda_advanced/'")

def perform_preprocessing(df):
    logging.info("--- Phase 3: Preprocessing ---")
    
    # 1. Feature Engineering: Content Soup for Recommendation
    # Filter features for soup
    df['genre_str'] = df['genres'].apply(lambda x: " ".join([i.replace(" ", "") for i in x]))
    df['company_str'] = df['production_companies'].apply(lambda x: " ".join([i.replace(" ", "") for i in x[:3]])) # Top 3 companies
    
    # Create the soup
    df['soup'] = (
        df['overview'].astype(str) + " " + 
        df['tagline'].astype(str) + " " + 
        df['genre_str'] + " " + 
        df['company_str']
    )
    
    # Basic text cleaning on soup
    df['soup'] = df['soup'].str.lower().str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)
    
    # 2. ROI calculation
    df['roi'] = (df['revenue'] - df['budget']) / df['budget'].replace(0, np.nan)
    
    logging.info("Preprocessing complete.")
    return df

def main():
    csv_path = 'movies_metadata.csv'
    if not os.path.exists(csv_path):
        logging.error(f"File {csv_path} not found.")
        return
    
    # Load data
    logging.info("Loading dataset...")
    df = pd.read_csv(csv_path, low_memory=False)
    
    # Run Pipeline
    df = perform_cleaning(df)
    perform_eda(df)
    df = perform_preprocessing(df)
    
    # Save final processed data
    df.to_pickle('df_science_ready.pkl')
    logging.info("Final dataset saved to 'df_science_ready.pkl'")

if __name__ == "__main__":
    main()
