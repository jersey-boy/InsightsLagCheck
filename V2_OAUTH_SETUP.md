# NationBuilder V2 API + OAuth 2.0 — Setup & Reference

This document explains how the NationBuilder side of the comparison talks to the
**NationBuilder V2 API** using **OAuth 2.0**: how OAuth works in general, how
NationBuilder implements it, exactly what was set up for this project (including
how it shares an app with the sibling *ZipStateFed* project), the confirmed V2
request/response shapes for the fields we compare, and the token-refresh flow a
scheduled job needs.

It is written to be standalone — a future reader (or a fresh build session)
should be able to start here without re-discovering any of it.

> Scope note: in this project V2 is the source for **Pull B** (`pull_nationbuilder.py`),
> the authoritative NationBuilder data that Insights is compared against. The
> Insights pull (Pull A) still uses the Tableau REST API with its own token.

---

## 1. OAuth 2.0 in general (the concepts)

OAuth 2.0 is a standard for letting an application access an API **on behalf of
an account without the app ever handling that account's password**. Instead of a
password, the app ends up holding a short-lived **access token** that it sends
with each API request.

### The actors

- **Resource owner** — the account that owns the data (here: the
  `democratsabroad` NationBuilder nation).
- **Client** — the application requesting access (here: our script / Lambda; the
  app registered under "Your apps").
- **Authorization server** — issues tokens after verifying identity and consent
  (here: `https://democratsabroad.nationbuilder.com/oauth/...`).
- **Resource server** — the actual API serving data (here:
  `https://democratsabroad.nationbuilder.com/api/v2/...`).

### The credentials

- **Client ID** — public identifier for the app. Low-sensitivity; it even
  appears in the authorize URL.
- **Client Secret** — the app's password. **Sensitive** — must stay out of
  source control (in `.env` locally, Secrets Manager in AWS).
- **Redirect URI (OAuth callback)** — a URL registered with the app. After the
  user authorizes, the authorization server sends the browser back here with a
  one-time code. It must match the registered value **exactly** (scheme, host,
  port, path), or the flow is rejected.

### The tokens

- **Authorization code** — a short-lived (~10 min), **single-use** value handed
  back via the redirect after the user clicks "Authorize". It is not a token; it
  is exchanged for tokens. Using it twice fails.
- **Access token** — the bearer credential sent on each API call
  (`Authorization: Bearer <token>`). Short-lived — **24 hours in NationBuilder
  V2**.
- **Refresh token** — a durable credential used to obtain a *new* access token
  when the old one expires, **without any human/browser step**. This is what
  makes unattended/scheduled jobs possible.

### The "Authorization Code" flow (what we use)

This is the standard flow when a human can authorize once in a browser:

```
1. App builds an authorize URL (client_id + redirect_uri + response_type=code)
   and the user opens it in a browser.
2. User logs in (already logged in here) and clicks "Authorize".
3. Auth server redirects the browser to the redirect URI with ?code=XXXX
   (single-use, short-lived).
4. App exchanges that code (+ client_id + client_secret + redirect_uri) at the
   token endpoint for an access_token AND a refresh_token.
5. App calls the API with Authorization: Bearer <access_token>.
6. When the access token expires (24h), the app POSTs the refresh_token to the
   token endpoint to get a fresh access_token (+ usually a new refresh_token),
   with no browser step. Repeat forever.
```

The one-time human step is #1–#3. Everything after is automatable.

### PKCE (optional, not used here)

PKCE ("pixie") adds a one-time `code_verifier` / `code_challenge` pair so an
intercepted authorization code can't be exchanged by an attacker. NationBuilder
supports it optionally. We did **not** use it — for a server-side client that
keeps its client secret private, the plain authorization-code flow is sufficient.
If we later want it, add `code_challenge` / `code_challenge_method=S256` to the
authorize request and `code_verifier` to the token exchange.

---

## 2. NationBuilder specifics

- **Access requires** a NationBuilder **Certified Developer** account or a nation
  on an **Enterprise/Network plan**. Developer tools live under
  **Settings → Developer**.
- **V1 vs V2:** V1 uses a static access token passed as an `access_token` query
  param and is being phased out. **V2 uses OAuth 2.0 bearer tokens**, a different
  base path (`/api/v2`), and a JSON:API-style response shape. V2 access tokens
  expire after 24h and need the refresh flow.
- **Where things are in the control panel:**
  - `Settings → Developer → Register App` — create an OAuth app. Set the OAuth
    callback (redirect URI); set status **private**; leave all **syndication**
    options off (those publish/share the app to other nations — not wanted).
  - `Settings → Developer → Your apps` — lists apps; open one to see its
    **Client ID**, **Client Secret**, and callback URL.
  - `Settings → Developer → API Testing` — V1-oriented interactive docs / test
    token. Not used for V2 OAuth.
- **OAuth endpoints** (nation-scoped):
  - Authorize: `https://democratsabroad.nationbuilder.com/oauth/authorize`
  - Token: `https://democratsabroad.nationbuilder.com/oauth/token`
- **API base:** `https://democratsabroad.nationbuilder.com/api/v2`

---

## 3. What was set up for this project (done)

This project **reuses the OAuth app registered by the sibling *ZipStateFed*
project** rather than registering a new one. One registered app can issue many
independent token chains, so sharing the app is fine — see the important caveat
below.

1. **Reused the existing OAuth app** in `democratsabroad` ("Register App"):
   - Status: private; syndication: all off.
   - OAuth callback URL: `https://localhost:8080/callback`
   - Its `CLIENT_ID` / `CLIENT_SECRET` are shared with ZipStateFed.
2. **Credentials** stored in `.env` (gitignored):
   - `NATIONBUILDER_SLUG`
   - `NATIONBUILDER_CLIENT_ID`
   - `NATIONBUILDER_CLIENT_SECRET`
   - `NATIONBUILDER_REDIRECT_URI=https://localhost:8080/callback`
3. **A one-time authorization handshake was completed for *this* project** (a
   second, independent handshake against the same app), producing a **dedicated
   refresh token** that only this project uses:
   - Opened the authorize URL in a browser, clicked Authorize, copied the
     `?code=...` from the (non-loading, expected) `https://localhost:8080/...`
     redirect.
   - Exchanged the code at the token endpoint for an access + refresh token.
4. **Tokens persisted** to `.env`:
   - `NATIONBUILDER_ACCESS_TOKEN_V2` — 24-hour access token (usually not needed;
     the client mints one from the refresh token at startup).
   - `NATIONBUILDER_REFRESH_TOKEN` — durable; used to mint new access tokens and
     **rotated on each run**.

> The `localhost` redirect never needs a running server. The browser failing to
> load `https://localhost:8080/callback` (and showing a cert warning first) is
> expected — the only thing we need is the `code` in the address bar.

### Why a *separate* refresh token (two apps sharing a token system)

NationBuilder **rotates the refresh token on every refresh** — each refresh
returns a new refresh token and invalidates the old one. The client refreshes at
the start of every run and persists the new token.

Two independent processes therefore **cannot safely share one refresh token**:
the first run would rotate it and break the second. So this project and
ZipStateFed share the **app** (`CLIENT_ID` / `CLIENT_SECRET`) but each holds its
**own** refresh token, obtained from its own one-time handshake. Same app, two
independent token chains, no collisions.

### Re-running the handshake (if ever needed)

The authorize URL (fill in / confirm the client id):

```
https://democratsabroad.nationbuilder.com/oauth/authorize?client_id=<CLIENT_ID>&redirect_uri=https%3A%2F%2Flocalhost%3A8080%2Fcallback&response_type=code
```

Because the code is single-use, a fresh code is required each time you redo the
exchange. The token exchange is a POST to `/oauth/token` with
`grant_type=authorization_code`, the `code`, `client_id`, `client_secret`, and
`redirect_uri`. The response's `refresh_token` goes into
`NATIONBUILDER_REFRESH_TOKEN`.

---

## 4. Token refresh flow (needed for the scheduled job)

V2 access tokens die after 24h, so an unattended job must refresh. POST to the
token endpoint with the refresh token:

```
POST https://democratsabroad.nationbuilder.com/oauth/token
{
  "grant_type":    "refresh_token",
  "refresh_token": "<current refresh token>",
  "client_id":     "<client id>",
  "client_secret": "<client secret>"
}
```

The response contains a **new** `access_token` and (typically) a **new**
`refresh_token`, both with `expires_in` = 86400. Implementation notes:

- **Rotate storage:** persist the newly returned refresh token, since
  NationBuilder rotates it on each refresh. Locally the client rewrites
  `NATIONBUILDER_REFRESH_TOKEN` in `.env`; in AWS, store it in Secrets Manager
  and write the rotated value back after each refresh.
- **Just-in-time refresh:** the client ignores any stored access token and
  **always refreshes at the start of a run** (one extra call, always-valid
  token). This is the simplest robust pattern and is what
  `NationBuilderV2Client.__init__` does when a refresh token is present.
- **Where this lives in code:** `nationbuilder_v2/client.py`
  — `refresh_access_token()` performs the POST and calls the `on_refresh`
  callback with the rotated token. `from_env()` wires in persistence (the local
  `.env` writer by default; pass a Secrets-Manager writer in Lambda).

> AWS note: this project's Lambda deployment (ToDoList item #2) is not built yet.
> When it is, the credentials will move to a Secrets Manager secret and the
> `on_refresh` callback will write the rotated refresh token back to it, mirroring
> ZipStateFed's `nb/v2/prod` setup.

---

## 5. Confirmed V2 request/response shapes

All verified against live `democratsabroad` data.

### Auth header

```
Authorization: Bearer <access_token>
Accept: application/json
```

### Pull signups with the registered address

```
GET /api/v2/signups?extra_fields[signups]=registered_address&page[size]=100
```

- `extra_fields[signups]=registered_address` sideloads the registered address
  into each signup's `attributes`. Confirmed working.
- `/api/v2/signups/{id}?extra_fields[signups]=registered_address` fetches one.

### Response shape (JSON:API)

```json
{
  "data": [
    {
      "type": "signups",
      "id": "676",
      "attributes": {
        "federal_district": "NY13",
        "registered_address": {
          "address1": "535 W 110th St",
          "city": "New York",
          "zip": "10025-2086",
          "state": "NY",
          "country_code": "US"
        }
      }
    }
  ],
  "links": { "self": "...", "next": "..." },
  "meta": {}
}
```

### The exact fields the comparison needs

| Needed value | Path in V2 response |
|--------------|---------------------|
| signup id | `data[].id` |
| registered state | `data[].attributes.registered_address.state` |
| registered zip | `data[].attributes.registered_address.zip` (may be ZIP+4; take first 5 digits) |
| federal district | `data[].attributes.federal_district` (e.g. `"NY13"`; prefix `NY` is the state) |

Projected by `nationbuilder_v2/extract.py:project_signup()` into
`signup_id, registered_state, registered_zip, federal_district`.

Notes:
- `registered_address` can be `null` (e.g. the system/admin record id=1, and
  members with no registered US address). Handled — those become blank fields.
- `zip` may be ZIP+4 (`10025-2086`) or 5-digit (`48433`) — normalized to the
  first five digits.
- `federal_district` is the authoritative value we compare the (less reliable)
  Insights `federal_district` against.

### Pagination

- Page via `page[size]=N` (100 is the max). V2 uses **page-number** pagination
  (`page[number]=N`), so any page can be requested directly.
- `links.next` gives the next page for serial walking. For the full ~189k-signup
  pull, `fetch_all_signups()` fetches pages in parallel batches instead and stops
  when a whole batch comes back empty (the stats total is unreliable here).

---

## 6. Implementation status (this project)

1. **V2 client** — `nationbuilder_v2/client.py`: Bearer auth, `/api/v2` base,
   `refresh_access_token()` (section 4), and the parallel paged pull
   (`fetch_all_signups`). Vendored from ZipStateFed.
2. **Pull + project** — `nationbuilder_v2/extract.py` pulls all signups with
   `extra_fields[signups]=registered_address` and projects `signup_id`,
   `registered_state`, `registered_zip` (5-digit), `federal_district`. No
   inclusion filter (this project compares the whole shared population).
3. **Pull B script** — `pull_nationbuilder.py` writes `nationbuilder_data.csv`.
4. **Comparison** — `compare_pipelines.py` inner-joins Pull A (Insights) and
   Pull B (V2) on `signup_id` and flags per-field matches/mismatches.
5. **Not yet done** — AWS Lambda / S3 / email for *this* project (ToDoList #2),
   and the GitHub Actions + Serverless deployment (ToDoList #3).

---

## 7. Security notes

- `.env` holds the client secret and tokens; it is gitignored — never commit it.
- `.env.example` must contain only placeholders for the `NATIONBUILDER_*` keys.
- The client secret and refresh token are the sensitive values; the client ID and
  access token are less so (the access token self-expires in 24h).
- The client secret can be regenerated from "Your apps" if it is ever exposed;
  doing so invalidates the old secret **and the sibling project that shares the
  app**, and requires re-authorizing both.
