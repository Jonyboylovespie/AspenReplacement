"""Explicit authentication fixtures for the pre-existing local scraper tests."""
from app import create_app
from sign_in import SignInManager


def create_test_app(directory):
    app = create_app(directory, {"TESTING": True, "START_REFRESH": False})
    database = app.extensions["accounts"]
    token = database.login({"sub": "fixture-user", "email": "fixture@school.example",
                            "email_verified": True, "name": "Fixture"})
    store = app.extensions["account_store"]("fixture-user")
    # These tests exercise the original local browser drivers in isolation.
    # Production factories always use encrypted storage and ExtensionSignIn.
    store.cipher = None
    store.claim_student = None
    store.account_email = None
    store.sign_in = SignInManager(store.directory / "browser", store.lock,
                                  store.verify_session, store.connect_session)
    app.extensions["aspen_store"] = store
    original_client = app.test_client
    def authenticated_client(*args, **kwargs):
        client = original_client(*args, **kwargs)
        with client.session_transaction() as state:
            state["login"] = token
        return client
    app.test_client = authenticated_client
    return app
