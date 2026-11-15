"""The sign-in page: one card in the middle, Google then email and password,
and an eye button that shows the password. The sign-in itself is tested in
test_auth.py; this is only the page."""
from fastapi.testclient import TestClient

from app import oauth
from app.api import app

client = TestClient(app)


def test_sign_in_is_one_card_with_everything_in_it(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    assert oauth.configured()
    html = client.get("/login").text
    card = html[html.index("class=authcard"):html.index("</section>")]
    for part in ("Welcome back", 'href="/auth/google"', "<form id=f>",
                 'name=email type=email', 'name=password type=password',
                 "data-pw", 'href="/signup"'):
        assert part in card, part
    # Google comes before the email form, with the "or" line between them
    assert card.index("/auth/google") < card.index("class=author") < card.index("<form id=f>")
    # the eye button never submits the form, and says what it does
    assert 'class=pwshow type=button data-pw aria-label="Show password"' in card


def test_no_or_line_without_google(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    html = client.get("/login").text
    assert "/auth/google" not in html and "class=author" not in html
    assert "<form id=f>" in html
