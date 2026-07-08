"""
tests/test_notifications.py — Mixtape

Tests for notification creation on song interactions.

Regression coverage for Issue #4: rating a song must notify the song's
original sharer, the same way adding it to a playlist does.
"""

import pytest
from app import create_app, db
from models import User, Song, Playlist
from services.notification_service import (
    rate_song,
    add_to_playlist,
    get_notifications,
)
from services.playlist_service import get_playlist_songs


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """A sharer who shared a song, plus a separate friend who interacts with it."""
    with app.app_context():
        sharer = User(username="aaliya", email="aaliya@example.com")
        friend = User(username="kenji", email="kenji@example.com")
        db.session.add_all([sharer, friend])
        db.session.flush()

        song = Song(title="Neon City", artist="Static Era", shared_by=sharer.id)
        db.session.add(song)
        db.session.flush()

        playlist = Playlist(name="Friday Energy", created_by=friend.id)
        db.session.add(playlist)
        db.session.commit()

        yield {"sharer": sharer, "friend": friend, "song": song, "playlist": playlist}


def test_rating_a_song_notifies_the_sharer(app, seed):
    """Rating another user's song creates a 'song_rated' notification for the sharer."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        friend_id = seed["friend"].id
        song_id = seed["song"].id

        assert get_notifications(sharer_id) == []

        rate_song(friend_id, song_id, 5)

        notifs = get_notifications(sharer_id)
        assert len(notifs) == 1  # Bug #4 caused this to be 0
        assert notifs[0]["type"] == "song_rated"
        assert "rated your song" in notifs[0]["body"]


def test_rating_your_own_song_does_not_notify(app, seed):
    """A user rating their own shared song should not notify themselves."""
    with app.app_context():
        sharer_id = seed["sharer"].id
        song_id = seed["song"].id

        rate_song(sharer_id, song_id, 4)

        assert get_notifications(sharer_id) == []


def test_add_to_playlist_persists_song_with_position(app, seed):
    """
    Adding a song to a playlist must insert a playlist_entries row with the
    NOT NULL position/added_by columns set (regression: the relationship-append
    path left them null and raised IntegrityError), and notify the sharer.
    """
    with app.app_context():
        sharer_id = seed["sharer"].id
        friend_id = seed["friend"].id
        song_id = seed["song"].id
        playlist_id = seed["playlist"].id

        add_to_playlist(playlist_id, song_id, friend_id)

        songs = get_playlist_songs(playlist_id)
        assert [s["id"] for s in songs] == [song_id]

        notifs = get_notifications(sharer_id)
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_added_to_playlist"


def test_add_to_playlist_is_idempotent(app, seed):
    """Adding the same song twice should not create a duplicate playlist entry."""
    with app.app_context():
        friend_id = seed["friend"].id
        song_id = seed["song"].id
        playlist_id = seed["playlist"].id

        add_to_playlist(playlist_id, song_id, friend_id)
        add_to_playlist(playlist_id, song_id, friend_id)

        songs = get_playlist_songs(playlist_id)
        assert len(songs) == 1
