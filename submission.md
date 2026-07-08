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

### Issue #1 — My listening streak keeps resetting (Sunday)

**How I reproduced it.** The existing test `tests/test_streaks.py::test_streak_increments_on_sunday` sets a Saturday listen (streak → 1) then a Sunday listen and asserts the streak becomes 2. Running `pytest tests/test_streaks.py` showed it failing with `assert 1 == 2` — the streak reset to 1 on Sunday instead of incrementing. That matches kenji's report exactly (streak thrown away, both times on a Sunday).

**How I found the root cause.** Route path: `GET /users/<id>/streak` → `users.py::streak()` → `streak_service.get_streak()` just reads the stored value, so the corruption had to happen at *write* time in `record_listening_event()` → `update_listening_streak()`. Reading `update_listening_streak()`, the consecutive-day branch was `elif days_since_last == 1 and today.weekday() != 6:`. The `weekday() != 6` guard jumped out — 6 is Sunday. I confirmed by checking `datetime(2024, 6, 16).weekday()` → `6`, so on any Sunday the "listened yesterday" branch is skipped and execution falls through to the `else`, which resets the streak to 1.

**The root cause.** Python's `datetime.weekday()` returns `6` for Sunday. The consecutive-day branch had an extra, unjustified condition `today.weekday() != 6`, so when a user listened on consecutive days but the *second* day was a Sunday, `days_since_last == 1` was `True` but `today.weekday() != 6` was `False`. The `and` short-circuited the whole branch to `False`, execution fell into the `else`, and the streak was reset to 1 — even though no day had actually been skipped. Every week boundary that landed on Sunday wiped the streak.

**My fix and side-effect check.** I removed the spurious `and today.weekday() != 6` guard so the branch is simply `elif days_since_last == 1:`. The streak now increments whenever the previous listen was exactly one calendar day ago, on any weekday. Side-effect check: I re-ran all of `test_streaks.py` (5/5 pass), which covers **both sides of the boundary** — `test_streak_resets_after_skipped_day` still passes (a genuinely skipped day still resets to 1) and `test_streak_does_not_double_count_same_day` still passes (same-day replays don't increment). So the fix restores Sunday increments without weakening the real reset behavior.

**Regression test.** `tests/test_streaks.py::test_streak_increments_on_sunday` (already in the repo) fails before this fix and passes after — it is the regression guard for this bug.

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it.** `tests/test_playlists.py::test_playlist_returns_all_songs` seeds a 5-song playlist and asserts `len(get_playlist_songs()) == 5`; it failed returning 4. `test_playlist_returns_songs_in_order` failed showing `['Track 1'..'Track 4']` (Track 5 missing). This is exactly darius's report: a 7-song playlist shows 6, and it's always the most-recently-added one that's missing. Because playlist entries are ordered by `position` ascending, "most recently added" = highest position = the *last* element of the ordered list.

**How I found the root cause.** Path: `GET /playlists/<id>/songs` → `playlists.py::get_songs()` → `playlist_service.get_playlist_songs()`. The query itself was correct — join `Song` to `playlist_entries`, filter by playlist, order by `position` ascending. The problem was the return line: `return [song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice is what made me certain — it deterministically discards the final element of an already-correct, position-ordered list.

**The root cause.** The retrieval built the full, correctly-ordered list of songs and then sliced off the last element with `songs[:-1]`. Since the list is ordered by ascending `position`, the last element is always the highest-position (most recently added) song. That is why "adding another song frees the previous one and hides the new one": each new insert becomes the new highest position, so the previously-hidden song moves to position N-1 (now returned) and the brand-new song at position N is the one dropped. The docstring even claimed "returns all songs," so the slice was clearly unintended.

**My fix and side-effect check.** I changed the return to iterate the full list: `return [song.to_dict() for song in songs]`. Side-effect check: `test_playlists.py` now passes 3/3, including `test_empty_playlist_returns_empty_list` — the empty-playlist boundary still returns `[]` without error (previously `[][:-1]` was also `[]`, so no behavior change there), and ordering is preserved because I didn't touch the `order_by(position)` clause.

**Regression test.** `tests/test_playlists.py::test_playlist_returns_all_songs` and `test_playlist_returns_songs_in_order` (already in the repo) both fail before this fix and pass after.

### Issue #4 — Notified on playlist-add but not on rating

**How I reproduced it.** Against the seeded DB I fetched a song shared by `nova`, recorded `get_notifications(nova)` (1 existing playlist notification), then called `rate_song(kenji, song, 5)` and re-fetched. The count stayed at **1** — no notification was created for the rating, while the report says playlist-adds do notify. This matches aaliya's report: the rating is saved (visible on the song) but nothing lands in the sharer's notification list.

**How I found the root cause.** Path: `POST /songs/<id>/rate` → `songs.py::rate()` → `notification_service.rate_song()`. The hint said the cause is architectural, not a typo, so I compared `rate_song()` line-by-line against its sibling `add_to_playlist()` in the same file. `add_to_playlist()` ends with `if song.shared_by != added_by_user_id: create_notification(...)`. `rate_song()` had **no such block at all** — it persisted the `Rating` and returned. That structural asymmetry (one interaction path notifies, the parallel one doesn't) was the moment it was clearly the root cause and not just a suspicious area.

**The root cause.** The notification behavior was never implemented in `rate_song()`. Both "someone interacted with your shared song" events are supposed to notify the original sharer, but only the playlist-add path called `create_notification()`. Rating a song created/updated the `Rating` row and committed, then returned without ever creating a `Notification`, so the sharer got nothing.

**My fix and side-effect check.** After the rating commit, I added the same guarded notification the playlist path uses: if `song.shared_by != user_id`, call `create_notification(user_id=song.shared_by, notification_type="song_rated", body=f"{rater.username} rated your song '{song.title}' {score} stars.")`. I placed it after the commit so a failed rating never emits a notification, and guarded on `song.shared_by != user_id` so users don't get notified for rating their own songs — mirroring the playlist path's self-check. Side-effect check: I added `tests/test_notifications.py` (3 tests) covering the notify-on-rate, no-self-notify, and existing playlist path, and re-ran the whole suite (15/15 pass), confirming rating updates (the existing-rating branch) and the score-validation path still work.

**Note (out of scope).** While writing the regression test I found that `add_to_playlist()` inserts via the `playlist.songs.append(...)` relationship, which does not populate the `NOT NULL` `position`/`added_by` columns on `playlist_entries` and raises an `IntegrityError`. That is a separate defect from the five assigned issues, so I left it untouched and did not couple my regression test to that path.

**Regression test (new).** `tests/test_notifications.py::test_rating_a_song_notifies_the_sharer` fails before this fix (0 notifications) and passes after; `test_rating_your_own_song_does_not_notify` guards the self-rating edge case.

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it.** Against the seeded DB I cleared darius's listening events and inserted a single event at yesterday 23:00 UTC (a previous calendar day, but only ~2.8h before the current time). Calling `get_friends_listening_now(nova)` returned `['simone', 'kenji', 'darius']` — darius appeared even though his only play was the night before. That is nova's exact report: a friend whose last listen was yesterday evening still shows as "listening now" the next morning.

**How I found the root cause.** Path: `GET /feed/<id>/listening-now` → `feed.py::listening_now()` → `feed_service.get_friends_listening_now()`. The function computed `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` with `RECENT_THRESHOLD = timedelta(hours=24)` and filtered `ListeningEvent.listened_at >= cutoff`. Seeing the fixed 24-hour subtraction was the tell: it's a rolling window, not a calendar-day boundary. I confirmed by noting that an 11pm play is only ~10 hours old at 9am, so it stays inside a `now - 24h` window until 11pm the following day — precisely "stuff from yesterday evening hangs around until the same time the next day."

**The root cause.** "Listening now" was defined as "within the last 24 hours" (`now - timedelta(hours=24)`) instead of "today." A rolling 24-hour window has no notion of a day boundary, so a play from yesterday evening remains eligible until 24 hours after it happened. Users expect the feed to reset at midnight (only today's plays), which a rolling window never does.

**My fix and side-effect check.** I replaced the rolling threshold with the start of the current UTC calendar day: `cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)`, and removed the now-unused `RECENT_THRESHOLD`/`timedelta` import. Events are now included only if `listened_at >= midnight today`. Side-effect check — I verified **both sides of the boundary** with `tests/test_feed.py`: a friend who listened earlier today still appears (`test_feed_includes_todays_listeners`), and a friend who listened one minute before midnight (previous day, still < 24h ago) no longer appears (`test_feed_excludes_yesterday_evening_listeners`). The per-friend dedup and ordering logic were untouched, and `get_activity_feed` (which is intentionally not recency-filtered) is unaffected.

**Regression test (new).** `tests/test_feed.py::test_feed_excludes_yesterday_evening_listeners` fails under the old 24-hour logic (the yesterday-evening play is < 24h old, so it's included) and passes after the fix; `test_feed_includes_todays_listeners` confirms today's listeners still show.

### Issue #3 — The same song shows up multiple times in search

**Honesty note up front:** the *symptom* (duplicate rows returned to the user) does **not** reproduce through the public `search_songs()` API in this environment, but the underlying *defect* that would cause it is real and I found and removed it. Details below.

**How I reproduced it (and where it didn't).** I first called `search_songs("Anthem")` against the seeded DB (which intentionally gives "Crown Heights Anthem" three tags) expecting three copies, per simone's report — but it returned the song exactly **once**. Reproducing at the SQL layer, however, showed the defect clearly: running the service's own query shape as a raw column `select` (`select(Song.id, Song.title).outerjoin(song_tags, ...).filter(title ilike '%Anthem%')`) returned **3 identical rows** — one per tag. So the query genuinely multiplies rows; the duplication is just masked before it reaches the caller (see root cause).

**How I found the root cause.** Path: `GET /songs/search` → `songs.py::search()` → `search_service.search_songs()`. The query was `db.session.query(Song).outerjoin(song_tags, Song.id == song_tags.c.song_id).filter(title/artist ilike ...)`. The `outerjoin(song_tags)` jumped out because the search neither filters nor selects on tags — the join has no purpose. Joining a one-to-many association table produces one result row per matching child row (per tag), so a 3-tag song yields 3 rows and a 0-tag song yields 1. That "conditional on tag count" behavior is exactly the hint for this issue ("some songs appear once, others two or three times").

**The root cause.** `search_songs()` joined `Song` to the `song_tags` association table for no reason. That join fans out to one row per tag, so a song with N tags appears N times in the result set. Why the app doesn't visibly duplicate today: SQLAlchemy's legacy ORM `Query` for a **single mapped entity** deduplicates results through the identity map before `.all()` returns, collapsing the three `Song` rows back into one object. So the bug is latent behind ORM behavior — but it's a real defect: any change to the query (selecting extra columns, adding a tag column to the output, switching to the 2.0-style `select()` execution, or a different DB/driver) would surface the duplicates simone described, and the SQL genuinely returns 3 rows (verified above).

**My fix and side-effect check.** I removed the pointless `outerjoin(song_tags)` (and the now-unused `Tag`/`song_tags` imports) so the query filters directly on `Song.title`/`Song.artist`. Tags still appear in each result because `to_dict()` reads them via the `Song.tags` relationship (`lazy="subquery"`), independent of the search join. This addresses the root cause rather than papering over it with `.distinct()` (which would also work but leaves the meaningless join in place). Side-effect check: `tests/test_search.py` passes 5/5 — including `test_search_no_duplicates_multi_tag_song` (multi-tag song appears once), the no-tag and one-tag cases (still returned exactly once), the basic match test, and the empty-result case. My SQL-level check now returns 1 row instead of 3.

**Regression test.** `tests/test_search.py::test_search_no_duplicates_multi_tag_song` (already in the repo) asserts a 3-tag song appears exactly once — it is the intended guard. It passes both before and after in this SQLAlchemy version because of the identity-map dedup described above, so it does not fully protect against the latent defect; the SQL-level check in this entry is what actually demonstrates the fix.

---

## Summary of commits

| Commit | Issue | Change |
|---|---|---|
| `docs:` | — | Codebase map, AI usage, orientation |
| `fix:` streak | #1 | Remove Sunday guard from consecutive-day branch |
| `fix:` playlist | #5 | Return all songs (drop the `[:-1]` slice) |
| `fix:` rating notification | #4 | Notify sharer on rating, mirroring playlist-add |
| `fix:` feed | #2 | Scope Listening Now to today, not rolling 24h |
| `fix:` search | #3 | Remove unnecessary `song_tags` join |
