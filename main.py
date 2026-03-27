"""FastAPI server: orchestration, ElevenLabs + Telegram webhooks, dashboard API."""
from __future__ import annotations
import os
import time
import asyncio
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import db
import research
import caller
import telegram_bot as tg

app = FastAPI(title="Vendor Caller Agent")
templates = Jinja2Templates(directory="templates")
db.init_db()


# ── Models ───────────────────────────────────────────────────────────────────

class OrchestrationRequest(BaseModel):
    vendors: List[str]
    product: str
    quantity: str
    user_phone: Optional[str] = None
    confirmed_vendors: Optional[List[str]] = None  # vendor names confirmed from DB — skip web search


# ── Orchestration (called by inbound ElevenLabs agent tool) ─────────────────

@app.post("/orchestrate")
async def orchestrate(req: OrchestrationRequest, background_tasks: BackgroundTasks):
    user_phone = req.user_phone or "unknown"

    # Auth: phone must be linked to a Telegram account
    chat_id = db.get_telegram_chat_id(user_phone)
    if not chat_id and user_phone != "unknown":
        return {"status": "auth_failed", "message": "Phone not linked to Telegram. Send /link to the bot first."}

    session_id = db.create_session(user_phone=user_phone, product=req.product, quantity=req.quantity)

    if chat_id:
        tg.notify_research_started(chat_id, req.vendors, req.product, req.quantity)

    background_tasks.add_task(
        _run_research_and_call, session_id, req.vendors, req.product, req.quantity,
        user_phone, chat_id, req.confirmed_vendors or []
    )
    return {"status": "started", "session_id": session_id}


async def _run_research_and_call(session_id: str, vendors: list[str], product: str,
                                  quantity: str, user_phone: str, chat_id: Optional[str],
                                  confirmed_vendors: list = None):
    loop = asyncio.get_event_loop()
    try:
        # 1. Resolve vendors — skip Firecrawl for confirmed DB vendors
        confirmed_set = {v.lower() for v in (confirmed_vendors or [])}
        vendor_data = await loop.run_in_executor(
            None, research.resolve_all_vendors, vendors, product, quantity, confirmed_set
        )

        outbound_agent_id = os.environ["OUTBOUND_AGENT_ID"]
        phone_number_id = os.environ["ELEVENLABS_AGENT_PHONE_NUMBER_ID"]

        batch_recipients = []
        for vd in vendor_data:
            vid = db.create_vendor_call(
                session_id=session_id,
                vendor_name=vd["vendor_name"],
                vendor_phone=vd.get("phone") or "",
                vendor_website=vd.get("website") or "",
                listed_price=vd.get("listed_price") or "",
                source=vd.get("source", "db"),
            )

            if not vd.get("can_handle_quantity", True):
                # Known vendor but can't handle quantity
                db.update_vendor_call_by_id(vid, status="no_fulfill", can_fulfill=0,
                                            notes="Quantity outside vendor's range per DB")
                if chat_id:
                    tg.notify_vendor_no_fulfill(chat_id, vd["vendor_name"], session_id)
                continue

            if vd.get("phone"):
                batch_recipients.append({
                    "vendor_name": vd["vendor_name"],
                    "vendor_phone": vd["phone"],
                    "product": product,
                    "quantity": quantity,
                    "session_id": session_id,
                    "vendor_call_id": vid,
                })
            else:
                db.update_vendor_call_by_id(vid, status="failed", notes="No phone number found")

        # 2. Submit batch calls
        if batch_recipients:
            batch_id = await loop.run_in_executor(
                None,
                lambda: caller.submit_batch_calls(outbound_agent_id, phone_number_id, batch_recipients,
                                                   os.environ["SERVER_URL"]),
            )
            db.set_batch_call_id(session_id, batch_id)
        else:
            with db.get_conn() as conn:
                conn.execute("UPDATE sessions SET status='done', updated_at=? WHERE id=?",
                             (time.time(), session_id))
            if chat_id:
                tg.notify_all_done(chat_id, session_id, db.get_vendor_calls(session_id),
                                   os.environ["SERVER_URL"])

    except Exception as e:
        print(f"[{session_id}] Orchestration error: {e}")
        with db.get_conn() as conn:
            conn.execute("UPDATE sessions SET status='error', updated_at=? WHERE id=?",
                         (time.time(), session_id))


# ── ElevenLabs post-call webhook ─────────────────────────────────────────────

@app.post("/webhook/elevenlabs")
async def elevenlabs_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    if body.get("type") == "post_call_transcription":
        background_tasks.add_task(_handle_post_call, body)
    return {"received": True}


async def _handle_post_call(body: dict):
    try:
        conversation_id = body.get("conversation_id", "")
        transcript = body.get("transcript", [])
        analysis = body.get("analysis", {})
        metadata = body.get("metadata", {})

        agent_meta = metadata.get("agent", {}).get("metadata", {})
        session_id = agent_meta.get("session_id", "")
        vendor_call_id = agent_meta.get("vendor_call_id", "")

        transcript_text = "\n".join(
            f"{t.get('role','?')}: {t.get('message','')}"
            for t in (transcript if isinstance(transcript, list) else [])
        )

        price, lead_time, contact_name, notes, can_fulfill = _parse_call_data(analysis, transcript_text)

        # Update DB
        if vendor_call_id:
            db.update_vendor_call_by_id(
                vendor_call_id,
                conversation_id=conversation_id,
                status="completed" if can_fulfill else "no_fulfill",
                price_quoted=price,
                lead_time=lead_time,
                contact_name=contact_name,
                can_fulfill=1 if can_fulfill else 0,
                notes=notes,
            )

        # Telegram notification
        session = db.get_session(session_id) if session_id else None
        chat_id = db.get_telegram_chat_id(session["user_phone"]) if session else None

        if chat_id:
            # Find vendor name
            vc_row = db.get_vendor_call_by_conv(conversation_id) if conversation_id else None
            vendor_name = vc_row["vendor_name"] if vc_row else "Vendor"
            tg.notify_call_completed(chat_id, vendor_name, price, lead_time, can_fulfill)
            if not can_fulfill:
                tg.notify_vendor_no_fulfill(chat_id, vendor_name, session_id)

        # Check if all calls done
        if session_id and db.check_session_complete(session_id):
            await _finish_session(session_id)

    except Exception as e:
        print(f"Webhook error: {e}")


def _parse_call_data(analysis: dict, transcript_text: str):
    import re
    summary = analysis.get("transcript_summary", "") or ""

    price_m = re.search(r"\$[\d,]+(?:\.\d{2})?(?:\s*(?:per|/)\s*(?:unit|piece|item|ea))?",
                        transcript_text, re.IGNORECASE)
    price = price_m.group(0) if price_m else None

    lead_m = re.search(r"(\d+[\s\-]+(?:to[\s\-]+\d+[\s\-]+)?(?:days?|weeks?|months?))",
                       transcript_text, re.IGNORECASE)
    lead_time = lead_m.group(0) if lead_m else None

    name_m = re.search(r"(?:my name is|this is|speaking with|I'm)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
                       transcript_text)
    contact_name = name_m.group(1) if name_m else None

    # Detect inability to fulfill
    no_fulfill_phrases = ["can't fulfill", "cannot fulfill", "out of stock", "don't carry",
                          "not available", "unable to", "we don't have", "not in stock"]
    can_fulfill = not any(p in transcript_text.lower() for p in no_fulfill_phrases)

    notes = (summary or transcript_text)[:400]
    return price, lead_time, contact_name, notes, can_fulfill


async def _finish_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        return
    vendor_calls = db.get_vendor_calls(session_id)
    chat_id = db.get_telegram_chat_id(session["user_phone"]) if session.get("user_phone") else None
    server_url = os.environ.get("SERVER_URL", "")

    if chat_id:
        tg.notify_all_done(chat_id, session_id, vendor_calls, server_url)

    # Callback call to user
    if session.get("user_phone") and session["user_phone"] != "unknown":
        try:
            lines = []
            for vc in vendor_calls:
                price = vc.get("price_quoted") or "not obtained"
                lead = vc.get("lead_time") or "unknown"
                lines.append(f"{vc['vendor_name']}: {price}, lead time {lead}")
            summary = ". ".join(lines)
            caller.call_user_back(
                os.environ.get("INBOUND_AGENT_ID", ""),
                os.environ["ELEVENLABS_AGENT_PHONE_NUMBER_ID"],
                session["user_phone"],
                summary,
            )
        except Exception as e:
            print(f"Callback error: {e}")


# ── Telegram webhook ──────────────────────────────────────────────────────────

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    update = await request.json()
    action = tg.handle_update(update)
    if action and action.get("action") == "search_alternatives":
        background_tasks.add_task(
            _search_and_call_alternatives,
            action["session_id"],
            action["vendor_name"],
            action["chat_id"],
        )
    return {"ok": True}


async def _search_and_call_alternatives(session_id: str, failed_vendor: str, chat_id: str):
    session = db.get_session(session_id)
    if not session:
        return
    loop = asyncio.get_event_loop()
    existing = [vc["vendor_name"] for vc in db.get_vendor_calls(session_id)]
    try:
        alts = await loop.run_in_executor(
            None,
            lambda: research.find_alternative_vendors(session["product"], session["quantity"], existing),
        )
        if not alts:
            tg.send_message(chat_id, "😔 Couldn't find alternative vendors online.")
            return

        outbound_agent_id = os.environ["OUTBOUND_AGENT_ID"]
        phone_number_id = os.environ["ELEVENLABS_AGENT_PHONE_NUMBER_ID"]
        batch_recipients = []
        for vd in alts:
            if not vd.get("phone"):
                continue
            vid = db.create_vendor_call(
                session_id=session_id,
                vendor_name=vd["vendor_name"],
                vendor_phone=vd["phone"],
                vendor_website=vd.get("website") or "",
                listed_price=vd.get("listed_price") or "",
                source="web_alt",
            )
            batch_recipients.append({
                "vendor_name": vd["vendor_name"],
                "vendor_phone": vd["phone"],
                "product": session["product"],
                "quantity": session["quantity"],
                "session_id": session_id,
                "vendor_call_id": vid,
            })

        if batch_recipients:
            names = ", ".join(r["vendor_name"] for r in batch_recipients)
            tg.send_message(chat_id, f"📞 Found {len(batch_recipients)} alternatives. Calling: {names}")
            await loop.run_in_executor(
                None,
                lambda: caller.submit_batch_calls(outbound_agent_id, phone_number_id,
                                                   batch_recipients, os.environ["SERVER_URL"]),
            )
        else:
            tg.send_message(chat_id, "😔 Found some vendors but couldn't get their phone numbers.")

    except Exception as e:
        tg.send_message(chat_id, f"Error searching alternatives: {e}")


# ── Dashboard API ─────────────────────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions():
    return db.all_sessions()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404)
    session["vendors"] = db.get_vendor_calls(session_id)
    return session


@app.get("/api/vendors")
def list_vendors():
    return db.list_vendors()


@app.post("/api/vendors")
async def add_vendor(request: Request):
    data = await request.json()
    vid = db.add_vendor(
        name=data["name"],
        phone=data.get("phone", ""),
        website=data.get("website", ""),
        supplies=data.get("supplies", ""),
        min_order=int(data.get("min_order", 0)),
        max_order=int(data.get("max_order", 999999)),
        notes=data.get("notes", ""),
    )
    return {"id": vid, "status": "created"}


@app.put("/api/vendors/{vendor_id}")
async def edit_vendor(vendor_id: str, request: Request):
    data = await request.json()
    allowed = {"name", "phone", "website", "supplies", "min_order", "max_order", "notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "min_order" in updates:
        updates["min_order"] = int(updates["min_order"])
    if "max_order" in updates:
        updates["max_order"] = int(updates["max_order"])
    db.update_vendor(vendor_id, **updates)
    return {"status": "updated"}


@app.delete("/api/vendors/{vendor_id}")
def remove_vendor(vendor_id: str):
    db.delete_vendor(vendor_id)
    return {"status": "deleted"}


@app.get("/api/vendors/lookup")
def lookup_vendor(name: str):
    """Called by inbound agent mid-call to check if vendor is in DB."""
    matches = db.fuzzy_search_vendors(name)
    if not matches:
        return {"found": False, "matches": []}
    return {
        "found": True,
        "matches": [
            {
                "id": v["id"],
                "name": v["name"],
                "phone": v["phone"] or "not on file",
                "contact": v["notes"] or "",
                "supplies": v["supplies"] or "",
                "min_order": v["min_order"],
                "max_order": v["max_order"],
            }
            for v in matches[:3]  # return top 3 matches max
        ],
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/session/{session_id}", response_class=HTMLResponse)
def session_detail(request: Request, session_id: str):
    return templates.TemplateResponse("dashboard.html", {"request": request, "session_id": session_id})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/api/settings")
def get_settings():
    s = db.get_settings()
    # Mask keys for display — never return raw values
    masked = {}
    for k, v in s.items():
        if v and len(v) > 8:
            masked[k] = v[:4] + "••••••••" + v[-4:]
        else:
            masked[k] = "••••••••" if v else ""
    return {"saved": bool(s), "keys": list(s.keys()), "masked": masked}


@app.post("/api/settings")
async def save_settings(request: Request):
    data = await request.json()
    allowed = {"ELEVENLABS_API_KEY", "FIRECRAWL_API_KEY", "TELEGRAM_BOT_TOKEN",
               "ELEVENLABS_AGENT_PHONE_NUMBER_ID", "SERVER_URL"}
    filtered = {k: v for k, v in data.items() if k in allowed and v and v.strip()}
    db.save_settings(filtered)
    # Also update os.environ so the running server uses new keys immediately
    for k, v in filtered.items():
        os.environ[k] = v
    return {"status": "saved", "count": len(filtered)}


@app.get("/vendors", response_class=HTMLResponse)
def vendors_page(request: Request):
    return templates.TemplateResponse("vendors.html", {"request": request})


@app.get("/faq", response_class=HTMLResponse)
def faq_page(request: Request):
    return templates.TemplateResponse("faq.html", {"request": request})
