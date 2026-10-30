# Wumi

Wumi is an AI operations manager on WhatsApp for SimiTeds, a Nigerian bespoke fashion
house, built on [Chord](https://www.chordhq.xyz). It is two Chord teammates backed by one
server:

- **Back Office Agent** works for Simi, the owner. She asks for a brief of the business, sees
  who owes money, approves order requests with a price and deposit, and records measurements.
- **Front Desk Agent** works for customers. It verifies who is messaging, takes order
  requests and measurements, and shares payment links once Simi has approved.

Simi sets prices and approvals. The server, not the model, decides balances, payment state
and who may approve. All data in this repo is mocked, and Paystack runs in test mode only.

## Try it in the WhatsApp simulator

| Chat | Link |
| --- | --- |
| Business Owner (Simi) | https://chat.tsglabs.dev/c/bFyRv4kha9GNNUHJM6X7CavC |
| Customer | https://chat.tsglabs.dev/c/lw11C7XgR0DcUNNcyrGN0sIH |

**As the Business Owner** (start here), send these one at a time:

1. `Give me a brief of the business today.`
2. `Who owes me money?`
3. `Tell me about Tunde.`
4. `Anything waiting for me?`

**Then close the loop as a Customer:**

1. Give the name `Amaka Okafor` and ask for a wedding outfit. When asked which garment she
   last ordered, answer `black corset gown`.
2. As the Business Owner, say `Anything waiting?` and approve the request with a price and deposit.
3. As Amaka, ask for an update. Pay the Paystack link with the test card
   `4084 0840 8408 4081` (any future expiry, CVV `408`), then say `paid`.
4. As the Business Owner, ask for the brief again and see the payment in it.

More customers to try are in [Test data](#test-data).

## How it fits together

```text
WhatsApp simulator
  ├─ customer ─> Chord teammate "Front Desk"   (skill: skills/front-desk-skill.md)       ─┐
  └─ Simi     ─> Chord teammate "Back Office" (skill: skills/back-office-skill.md) ─┤ MCP over HTTPS
                                                                             ▼
                              Caddy (TLS) ─> Starlette API ─> PostgreSQL
                                                  └─> Paystack (test mode)
```

| Path | What it is |
| --- | --- |
| `skills/front-desk-skill.md` | Skill for the customer-facing teammate |
| `skills/back-office-skill.md` | Skill for business-owner teammate |
| `app/` | The server: MCP tools, routes, services, Paystack client |
| `migrations/` | Alembic migrations (run on container start) |
| `fixtures/` | Mocked customers, orders and WhatsApp transcripts data |
| `tests/` | Test suites (SQLite, no network, payments, etc.) |
| `compose.yaml`, `Dockerfile`, `Caddyfile` | The deployment: Caddy, API, PostgreSQL |

## MCP servers and tools

Two servers, each with its own bearer token. A front-desk token cannot reach owner tools.

**Wumi Owner**, `/mcp/owner/` (Back Office Agent)

| Tool | Does |
| --- | --- |
| `get_brief` | Today's brief: approvals, overdue balances, orders due soon, at-risk events, held payments, missing measurements |
| `get_receivables` | Who owes what, days overdue, totals |
| `list_pending` | Order requests waiting for approval |
| `approve` | Sets price and deposit, confirms the order, creates the Paystack deposit link |
| `reject` | Declines a request with a reason the customer sees |
| `find_customer` | Profile, measurements, orders and balances |
| `get_conversations` | A customer's chat transcripts |
| `add_customer_measurements` | Saves measurements Simi gives for a customer |
| `create_payment_link` | Paystack link for an existing balance |

**Wumi ERP**, `/mcp/` (Front Desk Agent)

| Tool | Does |
| --- | --- |
| `identify_customer` | Recognises the sender; links a chat to a customer only after they name a recent garment |
| `add_conversation` | Records the customer's messages verbatim |
| `request_order` | Sends an order request to Simi for approval (no price, no promise) |
| `get_my_orders` | Orders and requests with status, price, deposit, balance and open payment link |
| `check_payment` | Asks Paystack whether the latest payment link was paid |
| `add_measurements` | Saves a new dated set of measurements |
| `get_my_measurements` | Current value per body part, plus history |

## Other endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | none | Liveness check |
| `POST /admin/reset` | `ADMIN_BEARER_TOKEN` | Reload the demo data, with dates shifted to today |
| `GET /admin/logs` | `ADMIN_BEARER_TOKEN` | Recent events and derived order state (`?limit=`, max 500) |
| `POST /webhooks/paystack` | Paystack signature | Payment webhook |
| `GET /paid` | none | Page Paystack returns the customer to; confirms the payment |
| `GET /v/customer/{id}` | none | Read-only customer page, linked from Simi's replies |
| `GET /v/order/{id}` | none | Read-only order page with its payments |

## Run it on your machine

This is the default way to run it. You need only **Docker** (Docker Desktop, or Docker
Engine with the Compose plugin). No Python or database install is needed.

**1. Get the code and create `.env`**

```bash
git clone https://github.com/africanyeast/tsg-chord.git
cd tsg-chord
cp .env.example .env
```

**2. Edit `.env`.** For a local run, change one line and leave the rest:

```text
PUBLIC_BASE_URL=http://localhost:8000
```

The placeholder passwords and tokens work locally. To create real Paystack payment links,
also set `PAYSTACK_SECRET_KEY` and `PAYSTACK_PUBLIC_KEY` to your test keys (`sk_test_...`,
`pk_test_...`). Without them everything else works, but `approve` and `create_payment_link`
return a clear Paystack error (`Invalid key`) and the request stays waiting.

**3. Start it**

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

The first build takes a few minutes. Wait until `docker compose ps` shows `api` and `db` as
`healthy`. If port 8000 is taken, free it first.

**4. Load the demo data and check it**

```bash
curl http://localhost:8000/health
curl -X POST -H "Authorization: Bearer replace-with-a-different-long-random-token" \
  http://localhost:8000/admin/reset
```

Use your own `ADMIN_BEARER_TOKEN` if you changed it. The reset answers `{"status":"ok",...}`.
The database starts empty, so do not skip it. Run the reset again any time to restore the demo state.

**5. Check the MCP servers.** Each should return a list of tools:

```bash
curl -s http://localhost:8000/mcp/ \
  -H "Authorization: Bearer replace-with-a-long-random-token" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

For the owner server use `/mcp/owner/` and `MCP_OWNER_BEARER_TOKEN`
(`replace-with-another-long-random-token` by default).

**Stop it:** `docker compose -f compose.yaml -f compose.dev.yaml down`. Add `-v` to also delete the database.

**Connecting Chord to a local run.** Chord can only call a public HTTPS URL. Expose port 8000
with a tunnel (for example `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`),
set `PUBLIC_BASE_URL` in `.env` to the tunnel URL, run the `up` command again, then follow
[Connect it to Chord](#connect-it-to-chord). To skip the tunnel, use the simulator links above.

**Run the tests** (needs Python 3.12):

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # SQLite, no network
```

No Python 3.12? Run them in Docker instead:

```bash
docker run --rm -v "$PWD":/src -w /src python:3.12-slim \
  sh -c 'pip install -q -r requirements.txt && python -m pytest -q'
```

## Run it on a cloud server

The server runs Caddy (automatic HTTPS), the API and PostgreSQL in Docker Compose.

**1. Create the host.** An AWS EC2 instance works well:

- **Type:** `t3.small` (2 vCPU, 2 GB RAM). Anything smaller struggles to build the image.
- **Image:** Ubuntu Server LTS. **Disk:** 20 GB gp3.
- **Security group:** allow inbound TCP 22 (your IP only), 80 and 443 (anywhere).
  Port 80 is required: Caddy needs it to get the certificate.
- Attach an **Elastic IP** so the address survives a restart.

**2. Pick a hostname.** Use your own domain pointing at the IP, or `<ip-with-dashes>.sslip.io`
(for `203.0.113.7`, use `203-0-113-7.sslip.io`).

**3. Install Docker** on the host, then log out and back in:

```bash
ssh ubuntu@<ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

**4. Get the code and create `.env`**

```bash
git clone https://github.com/africanyeast/tsg-chord.git wumi && cd wumi
cp .env.example .env
```

Edit `.env` (`nano .env`):

- `POSTGRES_PASSWORD`: a strong password, repeated inside `DATABASE_URL`.
- `MCP_BEARER_TOKEN`, `MCP_OWNER_BEARER_TOKEN`, `ADMIN_BEARER_TOKEN`: three different values,
  each from `openssl rand -hex 32`.
- `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`: your `sk_test_...` and `pk_test_...` keys.
- `PUBLIC_DOMAIN`: the hostname. `PUBLIC_BASE_URL`: `https://` plus the hostname.

**5. Start it and load the data**

```bash
docker compose up -d --build
docker compose ps                     # api, db and caddy running; api and db healthy
curl https://<hostname>/health
curl -X POST -H "Authorization: Bearer <ADMIN_BEARER_TOKEN>" https://<hostname>/admin/reset
```

Caddy fetches its certificate on first start, which can take a minute. If `/health` fails
with a certificate error, wait and retry, or check `docker compose logs caddy`.

**6. Check the MCP servers** with the `tools/list` command above, using `https://<hostname>`
and the matching token for `/mcp/` and `/mcp/owner/`.

To update later: `git pull && docker compose up -d --build`. Logs: `docker compose logs --since 10m api`.

## Connect it to Chord

1. **Add the MCP servers** in Admin → Channels & Tools → Custom tools:
   - `Wumi ERP`: `https://<hostname>/mcp/`, bearer token `MCP_BEARER_TOKEN`.
   - `Wumi Owner`: `https://<hostname>/mcp/owner/`, bearer token `MCP_OWNER_BEARER_TOKEN`.

   Keep the trailing slash (without it the server answers 307). Leave custom headers empty,
   because Chord shows them to workspace members.
2. **Create two teammates**, each with its skill file as the skill:
   - `Wumi - Front Desk` (Front Desk Agent) with `skills/front-desk-skill.md`
   - `Wumi - Back Office` (Operations Manager) with `skills/back-office-skill.md`
3. **Open the WhatsApp simulator** (Admin → Connected apps), create a chat link per teammate,
   and run the steps in [Try it](#try-it-in-the-whatsapp-simulator).

## Test data

`/admin/reset` loads 21 customers, 19 orders, a held payment and two waiting order requests. Useful ones:

| Customer | Situation | To verify, say the garment |
| --- | --- | --- |
| Amaka Okafor | Has measurements; the demo order is a wedding outfit | black corset gown |
| Tunde Adebayo | Overdue balance | his latest order |
| Mrs Adeyemi | Order due in 2 days | her latest order |
| Mrs Juwon | Open order, no measurements | her latest order |
| Mrs Fashola | Part-paid; a payment of the wrong amount is held | her latest order |
| Amaka Eze, Chinedu Nwosu | Order requests already waiting for Simi's approval | n/a (Simi sees them under `Anything waiting for me?`) |

The full set is in `fixtures/seed.json`. Use **Success** on Paystack's test checkout, or
**Declined** to see the failed-payment path.

## Configuration

| Variable | Purpose |
| --- | --- |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL` | Database |
| `MCP_BEARER_TOKEN` | Front-desk MCP (`/mcp/`) |
| `MCP_OWNER_BEARER_TOKEN` | Owner MCP (`/mcp/owner/`) |
| `ADMIN_BEARER_TOKEN` | `/admin/reset`, `/admin/logs` |
| `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY` | Paystack **test** keys |
| `PUBLIC_BASE_URL`, `PUBLIC_DOMAIN` | Callback URLs and the TLS hostname |

## Rules the server enforces

- **Customers are verified before they see orders.**
- **Balances are derived, never stored:** price minus confirmed payments.
- **One path to the ledger.** The webhook, `/paid` and `check_payment` share one function. It
  confirms only a successful Paystack payment for the exact amount. A wrong amount is held,
  replays change nothing, and a payment that would overpay the balance is held.
- **Measurements are dated and never overwritten.** Unbelievable values are refused, and a
  change of more than 15% is flagged for confirmation.

## Known limits

- **Open owner access.** Any chat with the Back Office Agent acts as Simi. Chord attaches every MCP
  server to every teammate, so `approve` is kept out of the front desk by its skill only.
  Production needs Simi on her own number and a server-side check.
- **No push.** Chord cannot start a conversation in the simulator, so each side sees changes
  on its next message.
- **Read-only pages** (`/v/...`) have no token.
- **Paystack webhook:** the test dashboard would not save the URL, so `/paid` and
  `check_payment` verify with Paystack directly. The signed webhook handler is built and tested.
- No images or voice notes, order edits, or staff access.

## How AI was used

- **Claude Code** helped draft the spec and plan, wrote most of the server code, tests and
  skills from that spec, and ran the deployment. I reviewed each slice, made the design calls
  (two teammates, server-side approval, derived balances) and tested every demo script in the
  simulator directly.
- **Mocked data:** the customers, orders and transcripts in `fixtures/` were generated
  with AI and checked by hand. None of it is real.
