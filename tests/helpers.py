"""Explicit authentication fixtures for the pre-existing local scraper tests."""
from app import create_app


def create_test_app(directory):
    app = create_app(directory, {"TESTING": True, "START_REFRESH": False})
    database = app.extensions["accounts"]
    token = database.login({"sub": "fixture-user", "email": "fixture@school.example",
                            "email_verified": True, "name": "Fixture"})
    store = app.extensions["account_store"]("fixture-user")
    app.extensions["aspen_store"] = store
    original_client = app.test_client
    def authenticated_client(*args, **kwargs):
        client = original_client(*args, **kwargs)
        with client.session_transaction() as state:
            state["login"] = token
        return client
    app.test_client = authenticated_client
    return app
