"""
tests/test_notifications.py — Mixtape

Tests for notification creation on song interactions.

Regression coverage for Issue #4: rating a song must notify the song's
original sharer, the same way adding it to a playlist does.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


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
        db.session.commit()

        yield {"sharer": sharer, "friend": friend, "song": song}


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
