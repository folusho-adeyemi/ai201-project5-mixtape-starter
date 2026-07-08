# Project 5 — Mixtape Bug Hunt — Submission

Branch: `bugfix/mixtape`

Bugs fixed (each is its own commit): **#1 streak, #5 playlist, #4 rating notification, #2 feed, #3 search** — all five, with #1/#2/#4/#5 fully reproduced and #3 documented honestly (see its entry).

---

## AI Usage

I used an AI coding assistant (Cursor) primarily for **codebase navigation and code explanation**, and verified every diagnosis by reading the code and running it myself.

- **Orientation / file summaries.** I asked the assistant to summarize what each `services/*.py` module is responsible for and to trace call chains (route → service → model). This produced the first draft of the codebase map below, which I then checked line-by-line against the actual files.
- **Trace a data flow.** I asked it to trace "how does rating a song reach a notification?" — this is what surfaced that `rate_song()` in `notification_service.py` never calls `create_notification()`, unlike `add_to_playlist()`. I confirmed by reading both functions side by side.
- **Edge-case reasoning on dates.** For Issue #1 I asked "what does `datetime.weekday()` return for Sunday, and how does that differ from `isoweekday()`?" (answer: `weekday()` → 6 for Sunday, `isoweekday()` → 7). I then verified the actual failing branch by reading the code and running the existing Sunday test.
- **Where the AI was incomplete / I overrode it.** The assistant initially described Issue #3 as "the `outerjoin` produces duplicate rows, so the fix is `.distinct()`." When I actually ran `search_songs("Anthem")` against the seeded DB, it returned **1** result, not 3 — because SQLAlchemy 2.0's legacy `Query` deduplicates single-entity rows through the identity map. So the AI's *symptom* claim was wrong for this environment; the underlying *code smell* (an unnecessary tag join) is real. I documented this honestly in the Issue #3 entry rather than pretending I reproduced duplicates.
- **Reproduction was done by me, not the AI.** I wrote small throwaway scripts (using `flask`/`db.session`) to trigger each bug against the seeded database before touching any code, and re-ran `pytest` to confirm which tests failed.

---

## Codebase Map

Mixtape is a Flask app using the **application-factory** pattern (`create_app` in `app.py`) with a single SQLAlchemy `db` instance. Every HTTP route is a thin controller that parses input, delegates to a service function, and formats the JSON response. **All business logic lives in `services/`** — the routes contain no logic worth testing on their own.

### Main files and their roles

- **`app.py`** — Flask application factory. Creates the `db = SQLAlchemy()` singleton, configures the SQLite database (`sqlite:///mixtape.db`), registers the four blueprints under URL prefixes (`/songs`, `/playlists`, `/users`, `/feed`), and calls `db.create_all()`. Must be launched with `FLASK_APP=app:create_app flask run` (running `python app.py` triggers a double-import of the models).
- **`models.py`** — SQLAlchemy models and association tables:
  - `User` (has `listening_streak`, `last_listened_at`, and a self-referential many-to-many `friends` relationship via the `friendships` table).
  - `Song` (shared by a user; has `ratings`, `listening_events`, and `tags`).
  - `ListeningEvent` (one row per play, with `listened_at`).
  - `Rating` (unique per `(user_id, song_id)`; the rating is stored directly on this table, there is no rating-on-song column).
  - `Playlist` + the **`playlist_entries`** association table, which is a join table carrying an explicit **`position`** integer, `added_by`, and `added_at`. Songs in a playlist have an explicit order, not just insertion order.
  - `Notification` (`user_id` recipient, `notification_type`, `body`, `read`).
  - `song_tags` and `friendships` association tables.
- **`routes/`** — one blueprint per area:
  - `songs.py` — `GET /songs/search`, `GET /songs/<id>`, `POST /songs/<id>/rate`, `POST /songs/<id>/listen`.
  - `playlists.py` — `POST /playlists/`, `GET /playlists/<id>`, `GET /playlists/<id>/songs`, `POST /playlists/<id>/songs`.
  - `users.py` — `GET /users/<id>`, `GET /users/<id>/streak`, `GET /users/<id>/notifications`, `POST /users/notifications/<id>/read`.
  - `feed.py` — `GET /feed/<id>/listening-now`, `GET /feed/<id>/activity`.
- **`services/`** — the logic layer (and where all five bugs live):
  - `streak_service.py` — records listening events and updates `listening_streak` based on consecutive calendar days.
  - `feed_service.py` — "Friends Listening Now" (recent, deduped to one song per friend) and a general activity feed.
  - `search_service.py` — case-insensitive title/artist search.
  - `notification_service.py` — creates notifications, adds songs to playlists, and saves ratings.
  - `playlist_service.py` — playlist creation and ordered song retrieval.
- **`seed_data.py`** — drops and recreates the DB, then seeds 5 users, 13 songs (with 0/1/3+ tags to expose the search bug), friendships, listening events (recent + 1–14 days old), playlists (7 songs each), and one example "song added to playlist" notification (so the correct notification pattern is visible when investigating Issue #4).
- **`tests/`** — `pytest` suites for streaks, search, and playlists. Several tests are written to *fail* against the buggy code (e.g. `test_streak_increments_on_sunday`, `test_playlist_returns_all_songs`) and act as regression tests once fixed.

### Data flow — user rates a song (Issue #4's feature)

1. Client sends `POST /songs/<song_id>/rate` with JSON `{user_id, score}`.
2. `routes/songs.py::rate()` parses the body and calls `notification_service.rate_song(user_id, song_id, score)`.
3. `rate_song()` validates the score (1–5), looks up the song and rater, then either updates an existing `Rating` (unique per user+song) or inserts a new one, and commits.
4. **The rating is persisted, but no `Notification` is created** — this is exactly the gap Issue #4 reports. Compare with `add_to_playlist()` in the same file, which *does* call `create_notification(...)` for `song.shared_by`.

### Data flow — viewing a playlist (Issue #5's feature)

1. `GET /playlists/<id>/songs` → `routes/playlists.py::get_songs()` → `playlist_service.get_playlist_songs(playlist_id)`.
2. `get_playlist_songs()` joins `Song` to the `playlist_entries` table, filters by playlist, and orders by `playlist_entries.position` ascending.
3. It returns `[song.to_dict() for song in songs[:-1]]` — the `[:-1]` slice silently drops the last (highest-position, most-recently-added) song.

### Patterns I noticed

- **Thin routes, fat services.** Every route immediately delegates to a service; input parsing/response formatting is in routes, logic is in services. To find any bug, start at the route and follow the single service call.
- **Timezone-aware UTC everywhere.** Timestamps default to `datetime.now(timezone.utc)`; the streak code even re-attaches `tzinfo` to naive values read back from SQLite.
- **Association tables carry data.** `playlist_entries` isn't a plain join — it stores `position`/`added_by`/`added_at`, so playlist reads must order by `position`.
- **Two similar notification paths.** `add_to_playlist` and `rate_song` are structurally parallel, which makes the missing notification in `rate_song` easy to spot by comparison (the approach the brief's hint for #4 recommends).

---

## Root Cause Analysis

_(entries added per fix, below)_
