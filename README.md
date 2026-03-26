# Dispatch — AI Vendor Calling Agent

Dispatch is an AI-powered procurement assistant that calls your vendors for you. Give it a call, tell it what you need, and it handles everything — researching vendors, firing parallel outbound calls, collecting pricing, and reporting back to you.

---

## What It Does

1. **You call the agent** — tell it which vendors to contact, what product you need, and the quantity
2. **Agent researches** — checks your existing vendor database first, then uses Firecrawl to search the web for vendor contact details and pricing if needed
3. **Parallel outbound calls** — fires simultaneous AI-powered calls to all vendors via ElevenLabs, each with vendor-specific context
4. **Live Telegram updates** — get notified as each vendor call completes with pricing and lead time
5. **Smart fallback** — if a vendor can't fulfill, Dispatch asks you on Telegram whether to search the internet for alternative vendors and call them too
6. **Callback** — once all vendors have responded, the agent calls you back with a full summary

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Voice AI | [ElevenLabs Conversational AI](https://elevenlabs.io) |
| Web Research | [Firecrawl](https://firecrawl.dev) |
| Notifications | Telegram Bot API |
| Backend | FastAPI + Python |
| Database | SQLite |
| Tunnel (local) | Cloudflare Tunnel |

---

## Project Structure

```
dispatch_agent/
├── main.py              # FastAPI server, orchestration, webhooks
├── caller.py            # ElevenLabs agent creation & batch calling
├── research.py          # Vendor resolution (DB → Firecrawl fallback)
├── db.py                # SQLite database layer
├── telegram_bot.py      # Telegram notifications & bot commands
├── setup_agents.py      # One-time ElevenLabs agent setup
├── manage_vendors.py    # CLI to add/list/remove vendors
├── templates/
│   └── dashboard.html   # Real-time results dashboard
├── requirements.txt
└── .env.example
```

---

## Getting Started (Local)

### 1. Clone & install dependencies
```bash
git clone https://github.com/moghalsaif/dispatch_agent.git
cd dispatch_agent
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
```
Fill in your `.env`:
```
ELEVENLABS_API_KEY=your_key
ELEVENLABS_AGENT_PHONE_NUMBER_ID=phnum_xxxx
FIRECRAWL_API_KEY=your_key
TELEGRAM_BOT_TOKEN=your_token
SERVER_URL=https://your-tunnel-url.trycloudflare.com
```

### 3. Start tunnel & server
```bash
cloudflared tunnel --url http://localhost:8000 &
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. Run one-time setup
```bash
python3 setup_agents.py
```
This creates your ElevenLabs inbound + outbound agents and registers the Telegram webhook.

### 5. Add your vendors
```bash
python3 manage_vendors.py add
```

### 6. Link your Telegram
Message your bot: `/link +1234567890` (use the same number you'll call from)

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message & setup instructions |
| `/link +1234567890` | Link your phone number to receive updates |
| `/vendors` | List all vendors in your database |

---

## Requirements

- ElevenLabs account (Conversational AI + phone number via Twilio)
- Firecrawl API key
- Telegram bot token (via @BotFather)
- Twilio account with a phone number (upgraded, not trial)

---

## Upcoming Updates

This is currently a **local-run prototype**. More updates are coming to the Dispatch platform.

In the coming weeks, I will be building a **full web platform** where:

- Vendors and procurement teams can sign up directly
- Users plug in their own Firecrawl and ElevenLabs API keys
- The entire workflow runs in the cloud — no local setup, no terminal, no code
- A clean dashboard shows all vendor calls, pricing, and history in one place
- Team collaboration, vendor CRM, and order tracking built in

**This is just the beginning. The whole platform is coming soon.**

---

*Built by [@moghalsaif](https://github.com/moghalsaif)*
