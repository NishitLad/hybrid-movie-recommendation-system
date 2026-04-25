# 🎬 MovieFlix - Quick Start Guide

## 🚀 Getting Started (2 minutes)

### Services Running:
```
✅ Backend API:  http://localhost:8000
✅ Frontend:     http://localhost:8503
```

---

## 📝 User Journey

### 1️⃣ **Sign Up** (30 seconds)
```
1. Open http://localhost:8503
2. Click "Sign Up"
3. Enter: username, full name, password
4. Click "Sign Up" button
5. Auto-redirected to login
```

### 2️⃣ **Log In** (10 seconds)
```
1. Enter username and password
2. Click "Login"
3. Enter home page
```

### 3️⃣ **Build Your Taste Profile** (5 minutes)
```
1. Search for movies you like
   - Click 🔍 Search bar
   - Type: "avengers", "romance", "thriller", etc.
   - Click on movie from results

2. Interact with movies
   - Click on any movie to view details
   - Click 👍 "Like" to add to favorites
   - Each action tracked automatically!

3. After 3-5 movies:
   - Home page recommendations improve
   - Movie details show personalized suggestions
```

### 4️⃣ **Explore Recommendations**
```
Home Page shows:
├─ Tailored for You (AI-powered hybrid)
├─ Trending in Your Genres (your preferences)
├─ Trending This Week (global)
├─ Popular Picks (by similar users)
└─ All-Time Popular (classics)

Movie Details shows:
├─ Similar Movies (content-based)
├─ Recommended for You (personalized)
└─ With smart fallbacks
```

---

## 🎯 How the System Learns From You

### What Gets Tracked:
- **View**: Every movie you click on
- **Like**: Every 👍 you click
- **Search**: Every search you make

### How It Improves:
1. Each action creates data
2. System analyzes your taste
3. Recommendations get better
4. Recent actions count more (recency weighting)

### Timeline:
| Actions | Recommendations |
|---------|-----------------|
| 0 | Popular movies |
| 1-3 | Genre-based |
| 3-10 | Personalized |
| 10+ | Highly accurate |

---

## 💡 Pro Tips

### Tip 1: Like Movies You Enjoyed
- "Like" has 2x weight vs "View"
- More likes = Better recommendations
- Mix genres to discover variety

### Tip 2: Search Different Genres
- Try: "action", "drama", "animation", "horror"
- System learns your full taste profile
- Recommendations become more diverse

### Tip 3: Check Movie Details
- Click on any movie to see:
  - Similar movies
  - Personalized recommendations
  - Related titles

### Tip 4: Keep Watching
- Fresh recommendations on home page
- Each view/like improves future picks
- Recency matters (recent = weighted higher)

---

## 📊 Your Stats Dashboard

On home page, see:
- **Interactions**: Total movies you've engaged with
- **Movies Liked**: Your favorites count
- **Top Genre**: Your most-watched category

---

## 🔍 Search Tips

### Search Examples:
```
✅ Works: "avengers", "spider-man", "romance"
✅ Works: Genre: "action", "sci-fi", "horror"
✅ Works: Director/Actor: (no exact support, try title)
```

### Not Found?
- Try: Alternative titles
- Try: Release year (e.g., "Avatar 2009")
- Try: Similar movies instead

---

## 🎮 Features You Can Try

### Feature 1: Similar Movies
1. Click on any movie
2. Scroll down to "Similar Movies"
3. See content-based recommendations

### Feature 2: Personalized Recommendations
1. After liking 3+ movies
2. Go to movie details page
3. See "Recommended for You" section
4. Recommendations based on YOUR taste

### Feature 3: Collaborative Recommendations
1. After watching 5+ movies
2. Check home page "Popular Picks"
3. Movies liked by users like you

### Feature 4: Trending Analysis
1. Home page shows global trending
2. And trending IN YOUR GENRES
3. See what's hot in your interests

---

## 🐛 Troubleshooting

### Problem: No recommendations showing
**Solution:**
- Like 3+ movies first
- Wait 30 seconds for system to process
- Refresh page (Ctrl+R)

### Problem: Same movies in all sections
**Solution:**
- This is normal for new users!
- Like more diverse movies
- System needs variety to diversify

### Problem: Movie not found in search
**Solution:**
- Alternative spellings?
- Year might matter (e.g., "Avatar 2009")
- Try different keywords

### Problem: Page stuck loading
**Solution:**
- Check internet connection
- Verify backend is running: http://localhost:8000/health
- Restart Streamlit: Ctrl+C then run command again

---

## 📱 Mobile Access

From your network:
```
http://192.168.0.104:8503
```
(Replace IP with your actual network IP)

---

## ⚙️ System Requirements Met

✅ User Behavior Tracking
- Views tracked automatically
- Likes tracked on button click
- Searches tracked on query

✅ Smart Recommendations
- Similar movies when clicking
- Personalized based on history
- Genre preferences learned
- Recency-weighted scoring

✅ Multiple Strategies
- Content-based (TF-IDF)
- Collaborative (user-based)
- Hybrid (combined approach)
- Trending data integration

✅ Fallback Handling
- Always shows something
- Cold start → Popular movies
- Warm start → Personalized
- Multi-tier fallback system

---

## 🎓 Understanding Your Recommendations

### Why This Movie?
1. **Similar Movies**: Fans of that genre love this
2. **Tailored for You**: Your watch pattern suggests this
3. **Trending in Your Genres**: Hot right now in your interests
4. **Popular Picks**: Users like you loved this
5. **Trending**: Most watched globally

---

## 🎬 Example Session

### Scenario: New User

**Time 0:00** - Sign up, start on Home Page
- See "Popular" movies everywhere

**Time 2:00** - Search and like 3 movies
- Like: Action movie, Drama, Animation

**Time 2:30** - Click Home
- Still showing popular, but:
  - "Your Genres" = Action, Drama
  - Gradually personalizing

**Time 5:00** - Like 5 more movies
- Now see real personalization
- "Tailored for You" shows YOUR taste
- "Trending in Your Genres" shows relevant

**Time 15:00** - Active user with 15+ actions
- Every section personalized
- Recommendations highly accurate
- Collaborative picks appearing

---

## 🏆 Achievement Unlocks

| Achievement | Condition | Reward |
|------------|-----------|--------|
| Movie Watcher | Like 5 movies | Collaborative recommendations unlock |
| Genre Master | Like movies from 5+ genres | Better genre diversity |
| Dedicated Fan | Like 20+ movies | Highly accurate AI recommendations |
| Trend Setter | Follow trending movies | Trending section gets better |

---

## 🚀 Ready to Start?

```
1. Open http://localhost:8503
2. Sign Up (30 seconds)
3. Search & Like Movies (5 minutes)
4. See Personalized Recommendations (instant!)
5. Enjoy discovering movies! 🍿
```

**Happy Watching! 🎬**

*The more you watch, the better the recommendations get.*
