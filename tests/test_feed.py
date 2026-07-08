"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed.

Regression coverage for Issue #2: the feed must only include plays from the
current calendar day, not a rolling 24-hour window (so a play from yesterday
evening drops off at midnight instead of lingering until the next morning).
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def _add_friendship(u1, u2):
    db.session.execute(friendships.insert().values(user_id=u1.id, friend_id=u2.id))
    db.session.execute(friendships.insert().values(user_id=u2.id, friend_id=u1.id))


@pytest.fixture
def seed(app):
    """
    nova has two friends:
      - simone listened earlier today
      - darius listened yesterday evening (previous calendar day, still < 24h ago)
    """
    with app.app_context():
        nova = User(username="nova", email="nova@example.com")
        simone = User(username="simone", email="simone@example.com")
        darius = User(username="darius", email="darius@example.com")
        db.session.add_all([nova, simone, darius])
        db.session.flush()

        _add_friendship(nova, simone)
        _add_friendship(nova, darius)

        song = Song(title="Neon City", artist="Static Era", shared_by=nova.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # simone listened today (a moment ago) — should appear
        db.session.add(ListeningEvent(user_id=simone.id, song_id=song.id, listened_at=now))
        # darius listened one minute before midnight — previous calendar day,
        # always < 24h ago, so the old rolling-window logic would wrongly include it.
        db.session.add(ListeningEvent(
            user_id=darius.id, song_id=song.id,
            listened_at=start_of_today - timedelta(minutes=1),
        ))

        db.session.commit()
        yield {"nova": nova, "simone": simone, "darius": darius}


def test_feed_includes_todays_listeners(app, seed):
    """A friend who listened today appears in Listening Now."""
    with app.app_context():
        feed = get_friends_listening_now(seed["nova"].id)
        names = [entry["friend"]["username"] for entry in feed]
        assert "simone" in names


def test_feed_excludes_yesterday_evening_listeners(app, seed):
    """
    A friend whose last play was yesterday evening (still within 24h) must NOT
    appear. This is the Issue #2 regression: rolling-24h logic showed them.
    """
    with app.app_context():
        feed = get_friends_listening_now(seed["nova"].id)
        names = [entry["friend"]["username"] for entry in feed]
        assert "darius" not in names
