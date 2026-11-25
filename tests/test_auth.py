def _register(client, email="boss@example.com", password="password123", role="viewer", headers=None):
    return client.post("/auth/register", json={"email": email, "password": password, "role": role}, headers=headers)


def _login(client, email, password="password123"):
    return client.post("/auth/login", data={"username": email, "password": password})


def test_first_user_becomes_admin_whatever_role_requested(client):
    r = _register(client, role="viewer")
    assert r.status_code == 201 and r.json()["role"] == "admin"


def test_after_bootstrap_registration_needs_admin(client):
    _register(client)
    assert _register(client, "a@example.com").status_code == 401
    token = _login(client, "boss@example.com").json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = _register(client, "acc@example.com", role="accountant", headers=h)
    assert r.status_code == 201 and r.json()["role"] == "accountant"
    # an accountant cannot create users
    acc = {"Authorization": f"Bearer {_login(client, 'acc@example.com').json()['access_token']}"}
    assert _register(client, "x@example.com", headers=acc).status_code == 403


def test_login_and_me(client):
    _register(client)
    assert _login(client, "boss@example.com", "wrong-password").status_code == 401
    token = _login(client, "BOSS@example.com").json()["access_token"]  # email is case-insensitive
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "boss@example.com"


def test_protected_routes_reject_missing_or_garbage_token(client):
    assert client.get("/parties").status_code == 401
    assert client.get("/parties", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_duplicate_email_conflict(client):
    _register(client)
    token = _login(client, "boss@example.com").json()["access_token"]
    r = _register(client, "boss@example.com", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409


def test_short_password_rejected(client):
    assert _register(client, password="short").status_code == 422
