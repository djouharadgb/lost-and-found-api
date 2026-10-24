import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


def auth(username):
    client.post("/auth/register", json={"username": username, "password": "secret123"})
    token = client.post("/auth/login", json={"username": username, "password": "secret123"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


ITEM = {
    "type": "lost", "title": "Black wallet", "description": "Leather, with student card",
    "category": "wallet", "location": "USTHB Bab Ezzouar", "date_event": "2026-10-20",
    "contact": "0555000000",
}


def test_full_flow():
    alice, bob = auth("alice"), auth("bob")

    assert client.post("/items", json=ITEM).status_code == 401           # needs login
    r = client.post("/items", json=ITEM, headers=alice)
    assert r.status_code == 201
    item_id = r.json()["id"]
    assert r.json()["status"] == "open"

    found = {**ITEM, "type": "found", "title": "Blue keys", "category": "keys", "location": "Tram station"}
    client.post("/items", json=found, headers=bob)

    # validation
    assert client.post("/items", json={**ITEM, "type": "stolen"}, headers=alice).status_code == 422

    # filter + search
    assert client.get("/items").json()["total"] == 2
    assert client.get("/items?type=found").json()["total"] == 1
    assert client.get("/items?q=wallet").json()["total"] == 1
    assert client.get("/items?location=usthb").json()["total"] == 1
    assert client.get("/items?date_from=2026-10-25").json()["total"] == 0
    assert client.get("/items?limit=1&page=2").json()["items"][0]["id"] == item_id

    # details
    assert client.get(f"/items/{item_id}").json()["title"] == "Black wallet"
    assert client.get("/items/9999").status_code == 404

    # update: owner only
    assert client.patch(f"/items/{item_id}", json={"title": "Hacked"}, headers=bob).status_code == 403
    r = client.patch(f"/items/{item_id}", json={"title": "Brown wallet"}, headers=alice)
    assert r.status_code == 200 and r.json()["title"] == "Brown wallet"
    assert r.json()["location"] == ITEM["location"]  # untouched field kept

    # recover: owner only, once only
    assert client.patch(f"/items/{item_id}/recover", headers=bob).status_code == 403
    r = client.patch(f"/items/{item_id}/recover", headers=alice)
    assert r.json()["status"] == "recovered" and r.json()["recovered_at"]
    assert client.patch(f"/items/{item_id}/recover", headers=alice).status_code == 409
    assert client.get("/items?status=open").json()["total"] == 1

    # mine
    assert len(client.get("/items/mine", headers=alice).json()) == 1

    # delete
    assert client.delete(f"/items/{item_id}", headers=bob).status_code == 403
    assert client.delete(f"/items/{item_id}", headers=alice).status_code == 204
