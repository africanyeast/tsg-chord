# Wumi

Wumi is an AI operations manager on WhatsApp for SimiTeds, a (fictional) Nigerian
bespoke fashion house, built on [Chord](https://www.chordhq.xyz). Customers message
**Wumi - Front Desk** to place and track orders. The owner, Simi, messages **Wumi - Back Office** to approve
orders, see who owes money and get a daily brief. Simi sets prices and approves orders.
The server, not the model, decides balances, payment state and who may approve.

## Try it (no setup)

Open both chats in the WhatsApp simulator:

| Chat | Link |
| --- | --- |
| Customer, **Wumi - Front Desk** | https://chat.tsglabs.dev/c/lw11C7XgR0DcUNNcyrGN0sIH |
| Simi, **Wumi - Back Office** | https://chat.tsglabs.dev/c/bFyRv4kha9GNNUHJM6X7CavC |

1. As the customer, give the name **Amaka Okafor** and ask for a wedding outfit. When
   asked which garment she last ordered, answer "black corset gown".
2. As Simi, say "Anything waiting?", then approve it with a price and deposit.
3. Back as Amaka, ask for an update. Pay the Paystack link with the test card
   `4084 0840 8408 4081` (any future expiry, CVV `408`), then say "paid".
4. As Simi, ask for "the brief".

Other customers to try are in [Test data](#test-data). All data is synthetic and Paystack
runs in test mode.

## How it fits together

```text
WhatsApp simulator
  ├─ customer ─> Chord teammate "Wumi - Front Desk"   (skill: skills/front-desk-skill.md)       ─┐
  └─ Simi     ─> Chord teammate "Wumi - Back Office" (skill: skills/back-office-skill.md) ─┤ MCP over HTTPS
                                                                             ▼
                              Caddy (TLS) ─> Starlette API ─> PostgreSQL
                                                  └─> Paystack (test mode)
```

| Path | What it is |
| --- | --- |
| `skills/front-desk-skill.md` | Skill for the customer-facing teammate |
| `skills/back-office-skill.md` | Skill for Simi's teammate |
| `app/` | The server: MCP tools, routes, services, Paystack client |
| `migrations/` | Alembic migrations (run on container start) |
| `fixtures/` | Synthetic customers, orders and WhatsApp transcripts |
| `tests/` | 44 tests (SQLite, no network) |
| `compose.yaml`, `Dockerfile`, `Caddyfile` | The deployment: Caddy, API, PostgreSQL |

## MCP servers and tools

Two servers, each with its own bearer token. A front-desk token cannot reach owner tools.

**Wumi ERP** at `/mcp/` (customer side)

| Tool | Does |
| --- | --- |
| `identify_customer` | Recognises the sender; links a chat to a customer only after they name a recent garment |
| `add_conversation` | Records the customer's messages verbatim |
| `request_order` | Sends an order request to Simi for approval (no price, no promise) |
| `get_my_orders` | Orders and requests with status, price, deposit, balance and open payment link |
| `check_payment` | Asks Paystack whether the latest payment link was paid |
| `add_measurements` | Saves a new dated set of measurements |
| `get_my_measurements` | Current value per body part, plus history |

**Wumi Owner** at `/mcp/owner/` (Simi's side)

| Tool | Does |
| --- | --- |
| `list_pending` | Order requests waiting for approval |
| `approve` | Sets price and deposit, confirms the order, creates the Paystack deposit link |
| `reject` | Declines a request with a reason the customer sees |
| `find_customer` | Profile, measurements, orders and balances |
| `add_customer_measurements` | Saves measurements Simi gives for a customer |
| `get_conversations` | A customer's chat transcripts |
| `get_receivables` | Who owes what, days overdue, totals |
| `create_payment_link` | Paystack link for an existing balance |
| `get_brief` | Today's brief: approvals, overdue, due soon, at-risk events, held payments, measurements missing |

Other endpoints: `GET /health`, `POST /webhooks/paystack`, `GET /paid` (payment return
page), `GET /v/customer/{id}` and `/v/order/{id}` (read-only pages), and
`POST /admin/reset` and `GET /admin/logs` (admin token).

## Run it on your server

You need a Linux host with Docker and Docker Compose, ports 80 and 443 open, a Paystack
**test** key pair, and a hostname pointing at the host. If you have no domain, use
`<ip-with-dashes>.sslip.io` (for `203.0.113.7`, `203-0-113-7.sslip.io`).

```bash
git clone <repo-url> wumi && cd wumi
cp .env.example .env
```

Edit `.env`:

- Make `POSTGRES_PASSWORD` a strong password and use the same one inside `DATABASE_URL`.
- Generate each of `MCP_BEARER_TOKEN`, `MCP_OWNER_BEARER_TOKEN` and `ADMIN_BEARER_TOKEN`
  with `openssl rand -hex 32`. They must all differ.
- Set `PAYSTACK_SECRET_KEY` and `PAYSTACK_PUBLIC_KEY` to your `sk_test_...` and `pk_test_...` keys.
- Set `PUBLIC_DOMAIN` to your hostname and `PUBLIC_BASE_URL` to `https://<that hostname>`.

Start it and load the demo data:

```bash
docker compose up -d --build
curl https://<hostname>/health
curl -X POST -H "Authorization: Bearer <ADMIN_BEARER_TOKEN>" https://<hostname>/admin/reset
```

Caddy gets a Let's Encrypt certificate on first start, which can take a minute. Migrations
run automatically. `/admin/reset` loads `fixtures/seed.json` and the transcripts with
dates shifted to today. Run it again any time to restore the demo state.

Check that an MCP server answers (expect a list of tools):

```bash
curl -s https://<hostname>/mcp/ \
  -H "Authorization: Bearer <MCP_BEARER_TOKEN>" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Do the same for `/mcp/owner/` with `MCP_OWNER_BEARER_TOKEN`.

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
   and run the steps in [Try it](#try-it-no-setup).

## Test data

`/admin/reset` loads 21 customers and 19 orders. Useful ones:

| Customer | Situation | To verify, say the garment |
| --- | --- | --- |
| Amaka Okafor | Has measurements; the demo order is a wedding outfit | black corset gown |
| Tunde Adebayo | Overdue balance | his latest order |
| Mrs Adeyemi | Order due in 2 days | her latest order |
| Mrs Juwon | Open order, no measurements | her latest order |

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

## Develop and test

Needs Python 3.12.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 44 tests, SQLite, no network
```

To run the stack on your machine without Caddy, copy `.env.example` to `.env`, then:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build   # API on http://localhost:8000
```

## Rules the server enforces

- **Customers are verified before they see orders.**
- **Balances are derived, never stored:** price minus confirmed payments.
- **One path to the ledger.** The webhook, `/paid` and `check_payment` share one function. It
  confirms only a successful Paystack payment for the exact amount. A wrong amount is held,
  replays change nothing, and a payment that would overpay the balance is held.
- **Measurements are dated and never overwritten.** Unbelievable values are refused, and a
  change of more than 15% is flagged for confirmation.

## Known limits

- **Open owner access.** Any chat with Wumi - Back Office acts as Simi. Chord attaches every MCP
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
  simulator myself.
- **Synthetic data:** the customers, orders and transcripts in `fixtures/` were generated
  with AI and checked by hand. None of it is real.
