# Wumi synthetic fixtures

These are fictional WhatsApp transcripts and ERP seed records for the Wumi PoC. They contain no real customer information, payment details, or media. `media_ref` is deliberately only an opaque identifier.

## Loading contract

The loader assigns immutable message IDs in array order (`MSG-<conversation_id>-001`, etc.). It must retain `media_ref`, text, type, timestamp and sender exactly as supplied. Do not expose `fixtures/keys/` through MCP or package it with the Wumi skill.

`seed.json` expresses operational dates as `day_offset` from the reset date. On `POST /admin/reset`, replace each date with `reset_date + day_offset` while preserving the supplied local time and `Africa/Lagos` offset. This deliberately keeps an overdue balance, an order due today and orders due soon on every reset.

Money is integer kobo. A customer saying they paid, a bank-alert screenshot, or a receipt image is never a confirmed ledger payment. Only seed ledger entries with `status: "confirmed"` count toward `paid_kobo`.

## Fixture groups

- `conversations/conv-001` through `conv-012`: extraction and approval cases, 15 messages each.
- `conversations/conv-013` and `conv-014`: short no-action noise chats.
- `keys/`: evaluator-only expected outcomes and guardrails.

The dataset is intentionally conversational: later messages may replace earlier details; fields without a clear final value must remain unresolved.
