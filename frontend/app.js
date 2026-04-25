const API_BASE = window.location.origin;
const TMDB_IMG = "https://image.tmdb.org/t/p/w500";
const TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original";

let currentUser = null;
let currentMovieId = null;

// Initialization
document.addEventListener("DOMContentLoaded", () => {
    checkSession();
    setupEventListeners();
});

function setupEventListeners() {
    document.getElementById("login-form").addEventListener("submit", handleLogin);
    document.getElementById("signup-form").addEventListener("submit", handleSignup);
    
    const searchInput = document.getElementById("search-input");
    let searchTimeout;
    searchInput.addEventListener("input", (e) => {
        clearTimeout(searchTimeout);
        const query = e.target.value.trim();
        if (query.length > 2) {
            searchTimeout = setTimeout(() => performSearch(query), 500);
        } else {
            document.getElementById("search-results").classList.add("hide");
        }
    });

    document.addEventListener("click", (e) => {
        if(!e.target.closest('.search-container')) {
            document.getElementById("search-results").classList.add("hide");
        }
    });
}

// UI State Management
function switchAuthTab(tab) {
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
    
    document.getElementById(`tab-${tab}`).classList.add('active');
    document.getElementById(`${tab}-form`).classList.add('active');
    
    document.getElementById('login-error').textContent = "";
    document.getElementById('signup-error').textContent = "";
}

function showLoader() { document.getElementById("global-loader").classList.remove("hide"); }
function hideLoader() { document.getElementById("global-loader").classList.add("hide"); }

function checkSession() {
    const savedUser = localStorage.getItem("streamflix_user");
    if (savedUser) {
        currentUser = savedUser;
        document.getElementById("user-greeting").textContent = `Welcome back, ${currentUser}`;
        switchToMain();
    } else {
        document.getElementById("main-view").classList.remove("active");
        document.getElementById("auth-view").classList.add("active");
        hideLoader();
    }
}

function logout() {
    localStorage.removeItem("streamflix_user");
    currentUser = null;
    document.getElementById("main-view").classList.remove("active");
    document.getElementById("auth-view").classList.add("active");
}

async function switchToMain() {
    document.getElementById("auth-view").classList.remove("active");
    document.getElementById("main-view").classList.add("active");
    showLoader();
    await loadInitialData();
    hideLoader();
}

// API Calls
async function apiPost(path, data) {
    try {
        const res = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail || "Request failed");
        return { data: json, error: null };
    } catch (e) {
        return { data: null, error: e.message };
    }
}

async function apiGet(path) {
    try {
        const res = await fetch(`${API_BASE}${path}`);
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail || "Request failed");
        return { data: json, error: null };
    } catch (e) {
        return { data: null, error: e.message };
    }
}

// Auth Handlers
async function handleLogin(e) {
    e.preventDefault();
    const user = document.getElementById("l-user").value;
    const pass = document.getElementById("l-pass").value;
    const btn = document.getElementById("login-btn");
    
    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Authenticating...`;
    const { data, error } = await apiPost('/login', { username: user, password: pass });
    btn.innerHTML = `<span>Enter the Realm</span>`;
    
    if (error) {
        document.getElementById("login-error").textContent = error;
    } else {
        localStorage.setItem("streamflix_user", user);
        currentUser = user;
        document.getElementById("user-greeting").textContent = `Welcome back, ${currentUser}`;
        switchToMain();
    }
}

async function handleSignup(e) {
    e.preventDefault();
    const user = document.getElementById("s-user").value;
    const name = document.getElementById("s-name").value;
    const pass = document.getElementById("s-pass").value;
    const btn = document.getElementById("signup-btn");
    
    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> SECURING PROFILE...`;
    const { data, error } = await apiPost('/signup', { username: user, full_name: name, password: pass });
    btn.innerHTML = `<span>Create Profile</span>`;
    
    if (error) {
        document.getElementById("signup-error").textContent = error;
    } else {
        document.getElementById("signup-error").textContent = "";
        const success = document.getElementById("signup-success");
        success.textContent = "Profile Secure! Transitioning to login...";
        success.style.opacity = "1";
        setTimeout(() => switchAuthTab('login'), 2000);
    }
}

// Data Loading
// Data Loading
async function loadInitialData() {
    try {
        const rows = document.getElementById("content-rows");
        rows.innerHTML = '';
        
        const sections = [
            { id: 'foryou', title: 'Top AI Matches For You', icon: 'fa-brain' },
            { id: 'recent', title: 'Because You Viewed', icon: 'fa-magic' },
            { id: 'trending', title: 'Global Trending', icon: 'fa-fire' },
            { id: 'popular', title: 'Critically Acclaimed', icon: 'fa-award' },
            { id: 'collab', title: 'Inspired by Your Circles', icon: 'fa-users' },
            { id: 'watchlist', title: 'Saved for Later', icon: 'fa-bookmark' },
            { id: 'mood_picks', title: 'Powered by Your Mood', icon: 'fa-bolt' },
            { id: 'history', title: 'Your Journey', icon: 'fa-history' }
        ];

        // Initial Skeleton UI
        sections.forEach(s => {
            rows.innerHTML += `
                <section class="row-section" id="section-${s.id}">
                    <h2 class="row-title"><i class="fas ${s.icon}"></i> ${s.title}</h2>
                    <div class="carousel-container">
                        <button class="carousel-btn prev" onclick="scrollCarousel('carousel-${s.id}', -1)"><i class="fas fa-chevron-left"></i></button>
                        <div class="carousel" id="carousel-${s.id}">
                            ${Array(6).fill('<div class="movie-card skeleton-card skeleton"></div>').join('')}
                        </div>
                        <button class="carousel-btn next" onclick="scrollCarousel('carousel-${s.id}', 1)"><i class="fas fa-chevron-right"></i></button>
                    </div>
                </section>
            `;
        });

        // SINGLE REQUEST for everything!
        const { data, error } = await apiGet(`/dashboard/${currentUser}`);
        if (error) throw new Error(error);

        // Deduplication set to keep feed unique
        const seenIds = new Set();

        // Helper to filter and update
        const processSection = (key, showMatch) => {
            if (data[key] && data[key].length > 0) {
                const filtered = data[key].filter(m => {
                    if (seenIds.has(m.tmdb_id)) return false;
                    seenIds.add(m.tmdb_id);
                    return true;
                });
                renderCarousel(`carousel-${key}`, filtered, showMatch);
                if (filtered.length === 0 && data[key].length > 0) {
                    // All were duplicates, show original anyway for this row if important
                    renderCarousel(`carousel-${key}`, data[key], showMatch);
                }
            } else {
                document.getElementById(`section-${key}`).classList.add('hide');
            }
        };

        // Hero Setup
        if (data.foryou && data.foryou.length > 0 && data.stats && data.stats.total_interactions > 0) {
            setupHero(data.foryou[0], true);
            if (data.stats.favorite_genres?.length > 0) {
                const topGenre = data.stats.favorite_genres[0].genre;
                document.getElementById("hero-overview").innerHTML = `
                    <span style="color: var(--primary); font-weight: 800; display: block; margin-bottom: 0.5rem; letter-spacing: 2px; font-size: 0.9rem;">TAILORED FOR YOUR TASTE</span>
                    Since you're a fan of <b>${topGenre}</b>, we think you'll love our latest pick for you.
                `;
            }
        } else if (data.trending && data.trending.length > 0) {
            setupHero(data.trending[0], false);
        }

        // Process sections with minor delays for "staggered" appearance (optional)
        processSection('foryou', true);
        processSection('recent', true);
        processSection('trending', false);
        processSection('popular', false);
        processSection('collab', true);
        processSection('watchlist', false);
        processSection('mood_picks', true);
        processSection('history', false);

    } catch (e) {
        console.error("Initial data load failed:", e);
    }
}

function renderCarousel(containerId, movies, showMatch = false) {
    const container = document.getElementById(containerId);
    if (!movies || movies.length === 0) return;
    
    container.innerHTML = movies.map((m, index) => {
        // Smart Badge Logic
        let badgeHtml = '';
        const rating = m.vote_average || 0;
        const year = m.release_date ? parseInt(m.release_date.split('-')[0]) : 0;
        
        if (index < 3 && (containerId === 'carousel-trending' || containerId === 'carousel-recent')) {
            badgeHtml = `<div class="smart-badge badge-trending"><i class="fas fa-fire"></i> Trending</div>`;
        } else if (year >= 2024) {
            badgeHtml = `<div class="smart-badge badge-new">New</div>`;
        } else if (rating >= 8.5) {
            badgeHtml = `<div class="smart-badge badge-top"><i class="fas fa-trophy"></i> Masterpiece</div>`;
        }

        const matchPercent = showMatch ? (98 - (index * 1.5)).toFixed(0) : null;
        
        return `
        <div class="movie-card" onclick="openMovieDetails(${m.tmdb_id})">
            ${badgeHtml}
            <img src="${m.poster_url || 'https://via.placeholder.com/300x450?text=No+Poster'}" alt="${m.title}" loading="lazy">
            <div class="card-info">
                <h4 class="card-title">${m.title}</h4>
                <div class="card-meta">
                    <span style="color: #f59e0b;"><i class="fas fa-star"></i> ${rating.toFixed(1)}</span>
                    ${matchPercent ? `<span class="match-tag">${matchPercent}% Match</span>` : `<span>${year || ''}</span>`}
                </div>
            </div>
        </div>
    `}).join('');
}

async function setupHero(movie, isPersonalized = false) {
    const { data } = await apiGet(`/movie/id/${movie.tmdb_id}`);
    if(data) {
        const heroSection = document.getElementById("hero-section");
        const backdrop = data.backdrop_url ? data.backdrop_url.replace('w500', 'original') : data.poster_url;
        heroSection.style.backgroundImage = `url(${backdrop})`;
        
        document.getElementById("hero-title").textContent = data.title;
        
        // Update hero badge
        const badge = document.getElementById("hero-badge");
        if (isPersonalized) {
            badge.innerHTML = `<i class="fas fa-magic"></i> AI RECOMMENDED FOR YOU`;
            badge.style.background = "linear-gradient(90deg, var(--primary), #a855f7)";
            badge.style.boxShadow = "0 0 20px rgba(96, 165, 250, 0.4)";
        } else {
            badge.innerHTML = `<i class="fas fa-fire"></i> GLOBAL TRENDING`;
            badge.style.background = "rgba(255,255,255,0.1)";
            badge.style.boxShadow = "none";
        }

        // Only update overview if not already personalized by loadInitialData
        if (!isPersonalized) {
            document.getElementById("hero-overview").textContent = data.overview ? data.overview : "";
        }
        
        document.getElementById("hero-watch-btn").onclick = () => openMovieDetails(data.tmdb_id);
        document.getElementById("hero-info-btn").onclick = () => openMovieDetails(data.tmdb_id);
    }
}

// Search
// Search (Movies & Actors)
async function performSearch(query) {
    const resultsContainer = document.getElementById("search-results");
    
    // Track search behavior for AI
    if (query.length > 3) {
        apiPost('/user-action', { username: currentUser, tmdb_id: 0, action_type: 'search', query: query });
    }

    const [movieRes, actorRes] = await Promise.all([
        apiGet(`/tmdb/search?query=${encodeURI(query)}`),
        apiGet(`/search/actor?query=${encodeURI(query)}`)
    ]);
    
    resultsContainer.classList.remove("hide");
    let html = "";

    // Movie Results
    if (movieRes.data && movieRes.data.results && movieRes.data.results.length > 0) {
        html += `<div style="padding:0.5rem 1rem; font-size: 0.75rem; color: var(--accent); font-weight: 800; text-transform: uppercase;">Titles</div>`;
        html += movieRes.data.results.slice(0, 4).map(m => {
            const poster = m.poster_path ? `${TMDB_IMG}${m.poster_path}` : 'https://via.placeholder.com/40x60?text=--';
            return `
                <div class="search-item" onclick="openMovieDetails(${m.id})" style="display:flex; gap:1rem; align-items:center; padding:0.5rem 1rem; cursor:pointer; transition: background 0.2s;">
                    <img src="${poster}" style="width:35px; border-radius:4px;">
                    <div>
                        <h4 style="font-size:0.85rem;">${m.title || m.name}</h4>
                        <p style="font-size:0.75rem; color:var(--text-secondary);">${m.release_date ? m.release_date.split('-')[0] : ''} • ★ ${m.vote_average || 'N/A'}</p>
                    </div>
                </div>
            `;
        }).join('');
    }

    // Actor Results
    if (actorRes.data && actorRes.data.results && actorRes.data.results.length > 0) {
        html += `<div style="padding:0.5rem 1rem; font-size: 0.75rem; color: var(--primary); font-weight: 800; text-transform: uppercase; border-top: 1px solid var(--glass-border); margin-top: 0.5rem;">Actors</div>`;
        html += actorRes.data.results.slice(0, 3).map(a => {
            const profile = a.profile_path ? `${TMDB_IMG}${a.profile_path}` : 'https://via.placeholder.com/40x40?text=?';
            return `
                <div class="search-item" onclick="showActorMovies(${a.id}, '${a.name.replace(/'/g, "\\'")}')" style="display:flex; gap:1rem; align-items:center; padding:0.5rem 1rem; cursor:pointer;">
                    <img src="${profile}" style="width:35px; height:35px; border-radius:50%; object-fit: cover;">
                    <div>
                        <h4 style="font-size:0.85rem;">${a.name}</h4>
                        <p style="font-size:0.75rem; color:var(--text-secondary);">Known for ${a.known_for && a.known_for.length > 0 ? a.known_for[0].title || a.known_for[0].name : 'Acting'}</p>
                    </div>
                </div>
            `;
        }).join('');
    }

    if (!html) {
        resultsContainer.innerHTML = `<div style="padding:1rem;color:var(--text-secondary)">No matches found</div>`;
    } else {
        resultsContainer.innerHTML = html;
    }
}

async function showActorMovies(actorId, actorName) {
    showLoader();
    document.getElementById("search-results").classList.add("hide");
    document.getElementById("search-input").value = "";
    
    const rows = document.getElementById("content-rows");
    const { data } = await apiGet(`/movies/actor/${actorId}`);
    hideLoader();

    if (data && data.length > 0) {
        rows.innerHTML = `
            <section class="row-section">
                <h2 class="row-title"><i class="fas fa-user-star"></i> Movies Starring ${actorName}</h2>
                <div class="carousel-container">
                    <button class="carousel-btn prev" onclick="scrollCarousel('carousel-actor-movies', -1)"><i class="fas fa-chevron-left"></i></button>
                    <div class="carousel" id="carousel-actor-movies"></div>
                    <button class="carousel-btn next" onclick="scrollCarousel('carousel-actor-movies', 1)"><i class="fas fa-chevron-right"></i></button>
                </div>
            </section>
        `;
        renderCarousel('carousel-actor-movies', data);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function scrollCarousel(containerId, direction) {
    const carousel = document.getElementById(containerId);
    if (!carousel) return;
    
    // Scroll by roughly 80% of the visible width
    const scrollAmount = carousel.offsetWidth * 0.8 * direction;
    carousel.scrollBy({
        left: scrollAmount,
        behavior: 'smooth'
    });
}

// Modals
async function openMovieDetails(tmdbId) {
    showLoader();
    document.getElementById("search-results").classList.add("hide");
    
    apiPost('/user-action', { username: currentUser, tmdb_id: tmdbId, action_type: 'view' });
    currentMovieId = tmdbId;

    // 1. Fetch Details & Rating immediately
    const [details, ratingInfo] = await Promise.all([
        apiGet(`/movie/id/${tmdbId}`),
        apiGet(`/rating/${currentUser}/${tmdbId}`)
    ]);

    // 2. SHOW MODAL IMMEDIATELY with details
    hideLoader();
    if(details.data) {
        renderMovieDetailsModal(details.data, ratingInfo.data);
        document.getElementById("movie-modal").classList.remove("hide");
        
        // 3. LOAD SIMILAR MOVIES IN BACKGROUND
        const similarCarousel = document.getElementById("modal-similar-carousel");
        similarCarousel.innerHTML = `<div class="spinner" style="margin:2rem auto; width:30px; height:30px;"></div>`;
        
        apiGet(`/movie/similar/${tmdbId}`).then(similar => {
            if (similar.data && similar.data.length > 0) {
                similarCarousel.innerHTML = similar.data.map(m => `
                    <div class="movie-card" onclick="openMovieDetails(${m.tmdb_id})">
                        <img src="${m.poster_url || 'https://via.placeholder.com/300x450?text=No+Poster'}" alt="${m.title}">
                        <div class="card-info">
                            <h4 class="card-title">${m.title}</h4>
                        </div>
                    </div>
                `).join('');
            } else {
                similarCarousel.innerHTML = `<p style="color: var(--text-secondary); padding: 1rem;">No similar titles found.</p>`;
            }
        });
    }
}

// Helper to render basic modal content
function renderMovieDetailsModal(d, r) {
    const poster = d.poster_url || 'https://via.placeholder.com/300x450?text=No+Poster';
    document.getElementById("modal-poster").src = poster;
    document.getElementById("modal-title").textContent = d.title || "Unknown Title";
    document.getElementById("modal-rating").textContent = (d.vote_average !== undefined && d.vote_average !== null) ? d.vote_average.toFixed(1) : 'N/A';
    document.getElementById("modal-date").textContent = d.release_date ? d.release_date.split('-')[0] : 'N/A';
    document.getElementById("modal-overview").textContent = d.overview || "No overview available for this cinematic masterpiece.";
    
    const genreContainer = document.getElementById("modal-genres");
    if (d.genres && Array.isArray(d.genres)) {
        genreContainer.innerHTML = d.genres.map(g => `<span class="genre-tag">${g.name}</span>`).join('');
    } else {
        genreContainer.innerHTML = "";
    }
    
    if (r) {
        updateStars(r.rating || 0);
        const wlBtn = document.getElementById("modal-watchlist-btn");
        if (wlBtn) {
            wlBtn.innerHTML = r.in_watchlist ? 
                '<i class="fas fa-check" style="color: #10b981;"></i> In Watchlist' : 
                '<i class="fas fa-bookmark"></i> Watchlist';
        }
    }
}

        // FETCH AI CONTEXTUAL INSIGHT
        const insightEl = document.getElementById("modal-ai-insight");
        if (insightEl) {
            insightEl.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Analyzing cinematic compatibility...`;
            apiGet(`/movie/ai-insight/${tmdbId}?username=${currentUser}`).then(res => {
                if (res.data) {
                    insightEl.innerHTML = `<i class="fas fa-robot" style="color:var(--primary);"></i> <strong>MASTER'S TAKE:</strong> "${res.data.insight}"`;
                }
            });
        }
        
        document.getElementById("movie-modal").classList.remove("hide");
    }
}

function closeModal() {
    document.getElementById("movie-modal").classList.add("hide");
}

async function likeMovie() {
    if(!currentMovieId) return;
    const btn = document.getElementById("modal-like-btn");
    const icon = btn.querySelector('i');
    
    icon.classList.add('pulse-animation');
    btn.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i>`;
    
    await apiPost('/user-action', { username: currentUser, tmdb_id: currentMovieId, action_type: 'like' });
    
    btn.innerHTML = `<i class="fas fa-heart pulse-animation" style="color:#f43f5e"></i> Liked`;
    setTimeout(() => { 
        btn.innerHTML = `<i class="fas fa-heart"></i> Like`; 
    }, 2000);
}

async function toggleWatchlist() {
    if(!currentMovieId) return;
    const btn = document.getElementById("modal-watchlist-btn");
    const { data } = await apiPost('/watchlist/toggle', { username: currentUser, tmdb_id: currentMovieId });
    if (data) {
        btn.innerHTML = data.action === "added" ? '<i class="fas fa-check" style="color: #10b981;"></i> In Watchlist' : '<i class="fas fa-bookmark"></i> Watchlist';
    }
}

async function submitRating(value) {
    if(!currentMovieId || !value) return;
    await apiPost('/rating', { username: currentUser, tmdb_id: currentMovieId, rating: parseFloat(value) });
    updateStars(value);
}

function setRating(value) {
    submitRating(value);
}

function updateStars(rating) {
    const stars = document.querySelectorAll(".star-btn");
    stars.forEach(s => {
        const val = parseInt(s.dataset.value);
        if (val <= rating) {
            s.classList.remove("far");
            s.classList.add("fas");
        } else {
            s.classList.remove("fas");
            s.classList.add("far");
        }
    });
}

// Profile
// Smart Mood Filtering
async function filterByMood(mood, element) {
    document.querySelectorAll('.mood-chip').forEach(c => c.classList.remove('active'));
    element.classList.add('active');
    
    const rows = document.getElementById("content-rows");
    if (mood === 'all') {
        loadInitialData();
        return;
    }

    showLoader();
    rows.innerHTML = `
        <section class="row-section">
            <h2 class="row-title"><i class="fas fa-magic"></i> Curating Your ${mood.charAt(0).toUpperCase() + mood.slice(1)} Mix...</h2>
            <div class="carousel-container">
                <button class="carousel-btn prev" onclick="scrollCarousel('carousel-mood-results', -1)"><i class="fas fa-chevron-left"></i></button>
                <div class="carousel" id="carousel-mood-results"></div>
                <button class="carousel-btn next" onclick="scrollCarousel('carousel-mood-results', 1)"><i class="fas fa-chevron-right"></i></button>
            </div>
        </section>
    `;

    const res = await apiGet(`/recommend/mood?mood=${mood}&limit=24`);
    hideLoader();

    if (res.data && res.data.length > 0) {
        renderCarousel('carousel-mood-results', res.data, true);
        
        // AUTO-CHAT INTEGRATION (WOW FACTOR)
        const chatWindow = document.getElementById("ai-chat-window");
        if (chatWindow.classList.contains("hide")) {
            toggleChat();
        }
        
        // We delay slightly to feel natural
        setTimeout(() => {
            appendMessage('user', `I'm feeling ${mood}. What fits my style?`);
            // Trigger the AI assistant directly
            apiPost('/chat', { message: `I'm feeling ${mood}. Recommend something based on my taste and this mood.`, username: currentUser }).then(aiRes => {
                if (aiRes.data) {
                    appendMessage('ai', aiRes.data.reply, aiRes.data.recommendations);
                }
            });
        }, 800);

    } else {
        rows.innerHTML = `<div style="text-align:center; padding: 4rem;"><h3>No matches for this mood. Try another!</h3></div>`;
    }
}

// AI Chat Assistant
async function toggleChat() {
    const chatWindow = document.getElementById("ai-chat-window");
    chatWindow.classList.toggle("hide");
    if (!chatWindow.classList.contains("hide")) {
        document.getElementById("chat-input").focus();
    }
}

function handleChatKey(e) {
    if (e.key === 'Enter') {
        sendChatMessage();
    }
}

async function sendChatMessage() {
    const input = document.getElementById("chat-input");
    const msg = input.value.trim();
    if (!msg) return;

    appendMessage('user', msg);
    input.value = "";

    // Show Thinking/Typing State
    const messages = document.getElementById("chat-messages");
    const thinkingDiv = document.createElement("div");
    thinkingDiv.className = "message ai typing-indicator";
    thinkingDiv.innerHTML = `<span></span><span></span><span></span>`;
    messages.appendChild(thinkingDiv);
    messages.scrollTop = messages.scrollHeight;

    const { data, error } = await apiPost('/chat', { message: msg, username: currentUser });
    
    // Remove individual thinking indicator
    thinkingDiv.remove();

    if (error) {
        appendMessage('ai', "I'm having a bit of trouble hearing you. Try again later?");
    } else {
        appendMessage('ai', data.reply, data.recommendations);
    }
}

function appendMessage(sender, text, recommendations = []) {
    const messages = document.getElementById("chat-messages");
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${sender}`;
    
    if (sender === 'ai') {
        // Typing effect for AI
        let i = 0;
        const speed = 20;
        const formattedText = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        
        // Instant render for formatted text if short, or character by character 
        // We do a simple version for now
        msgDiv.innerHTML = "";
        messages.appendChild(msgDiv);

        function typeWriter() {
            if (i < text.length) {
                // If we encounter a star/bold, we might want to jump, but let's keep it simple
                msgDiv.innerHTML = text.substring(0, i + 1).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
                i++;
                messages.scrollTop = messages.scrollHeight;
                setTimeout(typeWriter, speed);
            } else {
                // Done typing, add recommendations
                if (recommendations && recommendations.length > 0) {
                    renderChatRecommendations(msgDiv, recommendations);
                }
            }
        }
        typeWriter();
    } else {
        msgDiv.innerHTML = text;
        messages.appendChild(msgDiv);
    }
    
    messages.scrollTop = messages.scrollHeight;
}

function renderChatRecommendations(parentDiv, recommendations) {
    const recsDiv = document.createElement("div");
    recsDiv.className = "chat-recs-container";
    recsDiv.style.cssText = "margin-top: 1rem; display: flex; flex-direction: column; gap: 0.8rem; animation: slideUp 0.4s easeOut;";
    
    recommendations.slice(0, 3).forEach(m => {
        const item = document.createElement("div");
        item.className = "chat-suggestion-card";
        item.style.cssText = "display: flex; gap: 1rem; align-items: center; background: rgba(255,255,255,0.05); padding: 0.6rem; border-radius: 12px; cursor: pointer; transition: all 0.2s; border: 1px solid rgba(255,255,255,0.05);";
        
        item.onmouseenter = () => item.style.background = "rgba(255,255,255,0.1)";
        item.onmouseleave = () => item.style.background = "rgba(255,255,255,0.05)";
        
        item.innerHTML = `
            <img src="${m.poster_url || 'https://via.placeholder.com/40x60?text=--'}" style="width: 45px; height: 65px; border-radius: 6px; object-fit: cover; box-shadow: 0 4px 10px rgba(0,0,0,0.3);">
            <div style="flex: 1;">
                <h5 style="font-size: 0.85rem; margin-bottom: 0.2rem; color: white;">${m.title}</h5>
                <div style="display: flex; gap: 0.5rem; align-items: center; font-size: 0.7rem; color: var(--text-secondary);">
                    <span><i class="fas fa-star" style="color: #f59e0b;"></i> ${m.vote_average?.toFixed(1) || 'N/A'}</span>
                    <span>•</span>
                    <span>${m.release_date?.split('-')[0] || ''}</span>
                </div>
            </div>
            <i class="fas fa-chevron-right" style="font-size: 0.7rem; color: var(--accent); margin-right: 0.5rem;"></i>
        `;
        item.onclick = (e) => {
            e.stopPropagation();
            openMovieDetails(m.tmdb_id);
        };
        recsDiv.appendChild(item);
    });
    parentDiv.appendChild(recsDiv);
}

// Smart Profile
async function showProfile() {
    const prof = document.getElementById("profile-container");
    if(!prof.classList.contains("hide")) { prof.classList.add("hide"); return; }
    
    prof.classList.remove("hide");
    const container = document.getElementById("profile-stats");
    container.innerHTML = `<div class="spinner" style="margin:2rem auto; width:30px; height:30px;"></div>`;
    
    const { data } = await apiGet(`/user/stats/${currentUser}`);
    if (data) {
        const views = data.action_breakdown?.view || 0;
        const likes = data.action_breakdown?.like || 0;
        
        container.innerHTML = `
            <div class="stat-item"><span>Cinematic Rank</span><strong>LVL ${data.level || 1}</strong></div>
            <div class="stat-item"><span>Genre Discovery</span><strong>${data.diversity_score || 0} Topics</strong></div>
            <div class="stat-item"><span>Curated Likes</span><strong>${data.action_breakdown?.like || 0}</strong></div>
            <div style="padding: 1.5rem 2rem; border-top: 1px solid var(--glass-border);">
                <p style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 2rem; letter-spacing: 1px; text-transform: uppercase;">Your Taste DNA</p>
                <div style="display: flex; flex-direction: column; gap: 1.2rem;">
                    ${data.favorite_genres.map(g => `
                        <div style="display:flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.85rem; flex: 0 0 100px;">${g.genre}</span>
                            <div style="flex: 1; height: 4px; background: rgba(255,255,255,0.05); margin: 0 1rem; border-radius: 10px; overflow: hidden;">
                                <div style="width: ${g.percent || 0}%; height: 100%; background: var(--primary); box-shadow: 0 0 10px var(--primary);"></div>
                            </div>
                            <span style="font-size: 0.75rem; color: var(--accent); font-weight: 700; flex: 0 0 30px; text-align: right;">${g.percent || 0}%</span>
                        </div>
                    `).join('')}
                </div>
                ${data.favorite_genres.length === 0 ? '<p style="text-align:center; color:var(--text-secondary); font-size:0.8rem; margin:1.5rem 0;">Likely a newcomer? Like 3+ movies to see your DNA profile.</p>' : ''}
            </div>
            <button class="btn-primary" onclick="logout()" style="margin: 2rem; width: calc(100% - 4rem); font-size: 0.8rem;">Release Session</button>
        `;
    }
}

function closeProfile() {
    document.getElementById("profile-container").classList.add("hide");
}
