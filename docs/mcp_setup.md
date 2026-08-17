# MCP setup — automated invoice ingestion

Phase 5 pulls invoices automatically instead of requiring a manual
`python scripts/run_graph.py <pdf>` call per invoice. Ingestion connects to
an MCP server over stdio via `langchain-mcp-adapters`' `MultiServerMCPClient`,
and both MCP servers here (and `invoice_agent/ingest_mcp.py` that drives
them) live in this repo — nothing is installed from a third-party package.

## Two sources, one contract

`invoice_agent/mcp_servers/` has two servers exposing the identical 3-tool
shape (`list_pending_invoices`, `download_invoice_pdfs`, `mark_processed`),
so `ingest_mcp.py` is source-agnostic:

| Source | Server | Credentials | Use for |
|---|---|---|---|
| `filesystem` (default) | `filesystem_invoices.py` | none | local dev/testing, demos without setting up Gmail |
| `gmail` | `gmail_imap.py` | Gmail App Password | the actual differentiator — real automated ingestion |

## Why IMAP + App Password, not OAuth

The roadmap's default suggestion is a Gmail API OAuth flow. This repo uses
IMAP + an App Password instead, deliberately:

- **No third-party MCP package gets read access to your inbox.** Both
  servers here are ~100 lines you can read in one sitting.
- **No Google Cloud project / OAuth consent screen** to stand up just for a
  portfolio demo.
- The tradeoff: IMAP is a plainer protocol, so `gmail_imap.py` does its own
  MIME parsing to find PDF attachments, and an App Password is scoped to
  your whole account rather than a fine-grained OAuth scope — mitigated by
  keeping this server's own capabilities narrow (see "Scope" below).

If you'd rather use OAuth + the real Gmail API later, `gmail_imap.py` is the
only file that would need to change — `ingest_mcp.py` and the tool contract
stay the same.

## Setup (Gmail source)

1. Enable 2-factor authentication on the Google account you want to pull
   invoices from (required for App Passwords to be available at all).
2. Generate an App Password at
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   — name it something like "invoice-agent".
3. Add to `.env`:
   ```
   GMAIL_ADDRESS=your-address@gmail.com
   GMAIL_APP_PASSWORD=the-16-character-app-password
   ```
4. Test the server standalone before wiring it into ingestion — with the
   [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):
   ```bash
   npx @modelcontextprotocol/inspector uv run python invoice_agent/mcp_servers/gmail_imap.py
   ```
   Call `list_pending_invoices` and confirm it lists unread emails with PDF
   attachments before running real ingestion.

## Scope — what this server can and can't do

- Reads the `INBOX` only, and every fetch uses IMAP's `BODY.PEEK`, so simply
  *listing* or *downloading* never marks a message read.
- The **only** mailbox mutation is `mark_processed`, which sets the `\Seen`
  flag. Nothing here ever deletes, moves, labels, or sends anything.
- An App Password can technically do more than this server exposes (it's an
  account-level credential, not a granular OAuth scope) — the server code
  itself is the actual boundary. Revoke the App Password from your Google
  Account any time to cut off access immediately.

## Running ingestion

```bash
# No credentials needed - drop a PDF in data/inbox/ first
python -m invoice_agent.ingest_mcp --source filesystem

# Real Gmail inbox
python -m invoice_agent.ingest_mcp --source gmail --limit 5
```

Each pending item is downloaded to `attachments/` and run through the full
graph (`Router -> Extractor -> Validator -> human review -> Output`) under a
fresh `thread_id`. An item is only marked processed (email marked `\Seen`,
or filesystem file moved to `data/inbox/processed/`) if **every** PDF from
it reached a terminal, non-interrupted state — a flagged invoice is left
unresolved and gets re-listed on the next run, rather than being silently
marked done while parked mid-review with nothing in Supabase yet.

This script does not do interactive review itself. If an invoice is
flagged, it prints the `thread_id` and flags; resume it the same way
`scripts/run_graph.py` demonstrates, with `Command(resume=...)` against that
`thread_id` (the checkpointer is `SqliteSaver`, so the parked state survives
until you do).

## Known limitations

- **stdio transport is local-machine only.** This is fine for a cron job or
  manual runs on your own machine, but a *deployed* backend (Phase 8+) needs
  either an HTTP-transport MCP server or a separate local/cron ingestion
  process that pushes into the same Supabase project — it can't spawn a
  stdio subprocess from a request handler on a remote host.
- **No automatic resume loop.** Flagged invoices from ingestion sit parked
  until someone resumes them by hand. A real product would want a review
  queue UI (Phase 9's Streamlit frontend is a natural place for this) or an
  API endpoint (Phase 8) rather than manual `Command(resume=...)` calls.
- **Duplicate detection is still your safety net.** If an ingestion run
  reprocesses an email (e.g. after a crash before `mark_processed`), Phase
  4's duplicate check will catch a truly re-sent invoice and flag it rather
  than double-inserting it.
