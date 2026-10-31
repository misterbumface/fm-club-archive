import pytest
from flask import Flask

from app.extensions import db

@pytest.fixture()
def app():
    flask_app = Flask(__name__)
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    flask_app.config["SQLALCHEMY_BINDS"] = {"world": "sqlite:///:memory:"}
    flask_app.config["TESTING"] = True
    db.init_app(flask_app)

    with flask_app.app_context():
        import app.models  # noqa: F401  ensure models are registered

        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def session(app):
    return db.session


@pytest.fixture(autouse=True)
def followed_in_the_test_world(monkeypatch):
    """The test world's managed club, Chelsea (630), and Boreham Wood are followed, as in the first archive."""
    from app.services import story_leads

    monkeypatch.setattr(story_leads, "DEFAULT_FOLLOWED", (630, 109212))
