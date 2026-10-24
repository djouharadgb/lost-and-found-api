# Lost & Found API

Report lost or found items, search and filter them, view details, update your own reports, and mark an item as recovered.

Built with Python, FastAPI and SQLite.

## Run it

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs for the interactive documentation (Swagger UI). You can test every endpoint from there.

## Run the tests

```bash
pytest -q
```

## Authentication

Write actions need a token. Reading is public.

1. `POST /auth/register` with `{"username": "...", "password": "..."}`
2. `POST /auth/login` returns `{"token": "..."}`
3. Send the header `Authorization: Bearer <token>` on protected routes.

## Endpoints

| Method | Route | Auth | What it does |
|---|---|---|---|
| POST | `/auth/register` | no | Create an account |
| POST | `/auth/login` | no | Get a token |
| POST | `/items` | yes | Report a lost or found item |
| GET | `/items` | no | Search, filter, sort, paginate |
| GET | `/items/mine` | yes | List my own reports |
| GET | `/items/{id}` | no | Item details |
| PATCH | `/items/{id}` | yes (owner) | Update my report |
| PATCH | `/items/{id}/recover` | yes (owner) | Mark as recovered |
| DELETE | `/items/{id}` | yes (owner) | Delete my report |

### Filters on `GET /items`

`type` (lost/found), `status` (open/recovered), `category`, `location` (partial), `q` (searches title, description, location), `date_from`, `date_to`, `sort` (newest/oldest), `page`, `limit`.

Example: `GET /items?type=found&category=keys&q=blue&page=1&limit=5`

## Status codes

- 201 created, 204 deleted
- 400 nothing to update
- 401 missing or bad token
- 403 not the owner of that report
- 404 item not found
- 409 conflict (username taken, item already recovered)
- 422 invalid data (wrong type, too short, bad date)

## Example item

```json
{
  "type": "lost",
  "title": "Black wallet",
  "description": "Leather, with student card",
  "category": "wallet",
  "location": "USTHB Bab Ezzouar",
  "date_event": "2026-10-20",
  "contact": "0555000000"
}
```
