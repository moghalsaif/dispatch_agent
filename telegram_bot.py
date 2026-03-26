from __future__ import annotations
"""Telegram bot: send live updates, handle /link command, YES/NO vendor search callbacks."""
import os
import httpx

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _url(method: str) -> str:
    return TELEGRAM_API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method)


def send_message(chat_id: str, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = httpx.post(_url("sendMessage"), json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Telegram send error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Telegram response body: {e.response.text}")
        return None


def set_webhook(server_url: str):
    r = httpx.post(_url("setWebhook"), json={"url": f"{server_url}/telegram/webhook"}, timeout=10)
    r.raise_for_status()
    print(f"Telegram webhook set: {r.json()}")


# ── Notification helpers ─────────────────────────────────────────────────────

def notify_research_started(chat_id: str, vendors: list[str], product: str, quantity: str):
    names = ", ".join(vendors)
    send_message(chat_id, (
        f"🔍 <b>Research started</b>\n"
        f"Product: <b>{product}</b> × {quantity} units\n"
        f"Contacting: {names}\n\n"
        f"I'll update you as each call completes."
    ))


def notify_call_completed(chat_id: str, vendor_name: str, price: str | None,
                          lead_time: str | None, can_fulfill: bool):
    if can_fulfill:
        send_message(chat_id, (
            f"✅ <b>{vendor_name}</b> responded\n"
            f"Price: <b>{price or 'not specified'}</b>\n"
            f"Lead time: {lead_time or 'not specified'}"
        ))
    else:
        send_message(chat_id, (
            f"❌ <b>{vendor_name}</b> cannot fulfill this order."
        ))


def notify_vendor_no_fulfill(chat_id: str, vendor_name: str, session_id: str):
    """Ask user if they want to search internet for alternative vendors."""
    send_message(
        chat_id,
        f"⚠️ <b>{vendor_name}</b> can't fulfill this order.\n\nShould I search the internet for alternative vendors?",
        reply_markup={
            "inline_keyboard": [[
                {"text": "✅ Yes, find alternatives", "callback_data": f"search_alt:{session_id}:{vendor_name}"},
                {"text": "❌ No thanks",              "callback_data": f"skip_alt:{session_id}:{vendor_name}"},
            ]]
        },
    )


def notify_all_done(chat_id: str, session_id: str, vendor_calls: list[dict], dashboard_url: str):
    lines = []
    for vc in vendor_calls:
        if vc.get("can_fulfill") == 0 or vc.get("status") == "failed":
            lines.append(f"❌ {vc['vendor_name']}: couldn't fulfill")
        else:
            price = vc.get("price_quoted") or "not obtained"
            lead = vc.get("lead_time") or "—"
            lines.append(f"✅ {vc['vendor_name']}: {price} | lead time: {lead}")

    summary = "\n".join(lines)
    send_message(chat_id, (
        f"🎉 <b>All vendor calls complete!</b>\n\n"
        f"{summary}\n\n"
        f"Full dashboard: {dashboard_url}/session/{session_id}"
    ))


def notify_auth_failed(chat_id: str):
    send_message(chat_id, (
        "🔒 <b>Authentication failed.</b>\n"
        "The phone number that called our agent doesn't match your linked number.\n\n"
        "Make sure you're calling from the same number you linked with /link."
    ))


# ── Webhook update handler (called from FastAPI) ─────────────────────────────

def handle_update(update: dict) -> dict | None:
    """
    Process an incoming Telegram update.
    Returns action dict if orchestration needs to be triggered, else None.
    """
    # Handle callback queries (YES/NO inline buttons)
    if "callback_query" in update:
        cq = update["callback_query"]
        chat_id = str(cq["from"]["id"])
        data = cq.get("data", "")

        # Answer the callback to remove loading state
        httpx.post(_url("answerCallbackQuery"), json={"callback_query_id": cq["id"]}, timeout=5)

        if data.startswith("search_alt:"):
            _, session_id, vendor_name = data.split(":", 2)
            send_message(chat_id, f"🔍 Searching internet for alternatives to <b>{vendor_name}</b>...")
            return {"action": "search_alternatives", "session_id": session_id, "vendor_name": vendor_name, "chat_id": chat_id}

        elif data.startswith("skip_alt:"):
            _, session_id, vendor_name = data.split(":", 2)
            send_message(chat_id, f"OK, skipping alternative search for <b>{vendor_name}</b>.")
            return None

    # Handle regular messages
    if "message" not in update:
        return None

    msg = update["message"]
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "").strip()
    username = msg["from"].get("username", "")

    if text.startswith("/start"):
        send_message(chat_id, (
            "👋 <b>Welcome to Vendor Caller!</b>\n\n"
            "To receive call updates, link your phone number:\n"
            "<code>/link +1234567890</code>\n\n"
            "Use the same number you'll call our agent from."
        ))

    elif text.startswith("/link"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Usage: <code>/link +1234567890</code>")
        else:
            phone = parts[1]
            import db as _db
            _db.link_telegram(phone, chat_id, username)
            send_message(chat_id, (
                f"✅ <b>Linked!</b>\n"
                f"Your phone <code>{phone}</code> is now connected to this Telegram account.\n"
                f"You'll receive live updates whenever you use the vendor calling agent."
            ))

    elif text.startswith("/vendors"):
        import db as _db
        vendors = _db.list_vendors()
        if not vendors:
            send_message(chat_id, "No vendors in your database yet.")
        else:
            lines = [f"📋 <b>Your vendors ({len(vendors)}):</b>"]
            for v in vendors:
                lines.append(f"\n• <b>{v['name']}</b>\n  📞 {v['phone'] or '—'} | Supplies: {v['supplies'] or '—'} | Orders: {v['min_order']}–{v['max_order']} units")
            send_message(chat_id, "\n".join(lines))

    return None
