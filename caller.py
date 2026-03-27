from __future__ import annotations
"""ElevenLabs agent management and batch/outbound calling."""
import os
import httpx

ELEVEN_URL = "https://api.elevenlabs.io/v1"


def _headers():
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Agent setup (run once via setup_agents.py)
# ---------------------------------------------------------------------------

def create_inbound_agent(server_url: str) -> str:
    """Create the inbound agent that receives user calls and triggers orchestration."""
    payload = {
        "name": "Vendor Research Coordinator",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": (
                        "You are a vendor research coordinator for a procurement system.\n\n"
                        "When the user calls, follow this exact flow:\n\n"
                        "STEP 1 — Greet warmly and ask which vendors they want to contact.\n\n"
                        "STEP 2 — For EACH vendor name mentioned, immediately call the lookup_vendor tool "
                        "with that vendor name. Based on the result:\n"
                        "  - If found (found=true): Say 'I found [vendor name] in your directory — "
                        "their contact is [contact/notes], phone is [phone], they supply [supplies]. "
                        "Is this the same one you mean?'\n"
                        "    - If YES: note this vendor as confirmed from database.\n"
                        "    - If NO: note this vendor needs a fresh web search.\n"
                        "  - If not found (found=false): Say 'I don't have [vendor name] in your "
                        "directory yet — I'll search for them online.' Note as needs web search.\n\n"
                        "STEP 3 — Ask what product/item they need pricing for.\n\n"
                        "STEP 4 — Ask the quantity they need.\n\n"
                        "STEP 5 — Once you have all info, call start_research with the full vendor list, "
                        "product, quantity, and for each vendor include whether it was confirmed from DB.\n\n"
                        "STEP 6 — Say: 'Got it! I'm contacting your vendors now. "
                        "I'll send you updates on Telegram and call you back when all results are in!'\n\n"
                        "STEP 7 — End the call."
                    ),
                    "tools": [
                        {
                            "type": "webhook",
                            "name": "lookup_vendor",
                            "description": "Look up a vendor by name in the existing supplier database. Call this for every vendor the user mentions before confirming.",
                            "api_schema": {
                                "url": f"{server_url}/api/vendors/lookup",
                                "method": "GET",
                                "query_params_schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "The vendor name to search for"},
                                    },
                                    "required": ["name"],
                                },
                            },
                        },
                        {
                            "type": "webhook",
                            "name": "start_research",
                            "description": "Trigger vendor research and outbound calls once all vendors are confirmed",
                            "api_schema": {
                                "url": f"{server_url}/orchestrate",
                                "method": "POST",
                                "request_body_schema": {
                                    "type": "object",
                                    "properties": {
                                        "vendors": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "List of vendor names to contact",
                                        },
                                        "product": {"type": "string", "description": "Product or item needed"},
                                        "quantity": {"type": "string", "description": "Quantity of units needed"},
                                        "user_phone": {"type": "string", "description": "Caller phone number for callback"},
                                        "confirmed_vendors": {
                                            "type": "array",
                                            "description": "Vendors confirmed to be in the database — skip web search for these",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": ["vendors", "product", "quantity"],
                                },
                            },
                        },
                    ],
                },
                "first_message": "Hey! I'm your vendor research assistant. Who would you like me to contact today?",
                "language": "en",
            }
        },
    }
    r = httpx.post(f"{ELEVEN_URL}/convai/agents/create", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    agent_id = r.json()["agent_id"]
    print(f"Inbound agent created: {agent_id}")
    return agent_id


def create_outbound_agent() -> str:
    """Create the outbound agent used for calling vendors."""
    payload = {
        "name": "Vendor Pricing Caller",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": (
                        "You are calling {{vendor_name}} on behalf of a client to ask about pricing.\n\n"
                        "When someone answers:\n"
                        "1. Introduce yourself: 'Hi, I'm calling on behalf of a client who is interested "
                        "in purchasing {{product}} in quantities of {{quantity}} units.'\n"
                        "2. Ask to speak with someone in sales if needed.\n"
                        "3. Ask: 'What would the pricing be for {{quantity}} units of {{product}}?'\n"
                        "4. Ask about lead time: 'What's the typical lead time for this order?'\n"
                        "5. Ask for the contact's name.\n"
                        "6. Confirm all details back to them.\n"
                        "7. Thank them and end the call professionally.\n\n"
                        "If you reach voicemail: Leave a brief message saying you'll call back and end the call.\n"
                        "If no answer: End the call after 3 rings go unanswered (the system handles this)."
                    ),
                },
                "first_message": "Hello, I'm calling on behalf of a client regarding a pricing inquiry.",
                "language": "en",
            },
            "conversation": {
                "max_duration_seconds": 300,
            },
        },
    }
    r = httpx.post(f"{ELEVEN_URL}/convai/agents/create", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    agent_id = r.json()["agent_id"]
    print(f"Outbound agent created: {agent_id}")
    return agent_id


# ---------------------------------------------------------------------------
# Batch calling
# ---------------------------------------------------------------------------

def submit_batch_calls(
    agent_id: str,
    phone_number_id: str,
    vendor_calls: list[dict],  # [{vendor_name, vendor_phone, product, quantity, session_id, vendor_call_id}]
    webhook_url: str,
) -> str:
    """Submit parallel outbound calls to all vendors. Returns batch_id."""
    recipients = []
    for vc in vendor_calls:
        if not vc.get("vendor_phone"):
            continue
        recipients.append({
            "phone_number": vc["vendor_phone"],
            "conversation_initiation_client_data": {
                "dynamic_variables": {
                    "vendor_name": vc["vendor_name"],
                    "product": vc["product"],
                    "quantity": vc["quantity"],
                },
                "conversation_config_override": {
                    "agent": {
                        "metadata": {
                            "session_id": vc["session_id"],
                            "vendor_call_id": vc["vendor_call_id"],
                        }
                    }
                },
            },
        })

    if not recipients:
        raise ValueError("No recipients with phone numbers found")

    payload = {
        "call_name": f"Vendor Research - {vendor_calls[0]['product']}",
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": recipients,
        "target_concurrency_limit": len(recipients),  # all calls simultaneously
    }

    r = httpx.post(f"{ELEVEN_URL}/convai/batch-calling/submit", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    batch_id = r.json()["id"]
    print(f"Batch call submitted: {batch_id} ({len(recipients)} recipients)")
    return batch_id


def call_user_back(agent_id: str, phone_number_id: str, user_phone: str, summary: str) -> str:
    """Call the user back with a summary of all vendor results."""
    # Use batch calling with single recipient
    payload = {
        "call_name": "Vendor Research Callback",
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": [
            {
                "phone_number": user_phone,
                "conversation_initiation_client_data": {
                    "dynamic_variables": {"summary": summary},
                    "conversation_config_override": {
                        "agent": {
                            "prompt": {
                                "prompt": (
                                    "You are calling back the user to deliver vendor pricing results.\n"
                                    "When they answer, say:\n"
                                    "'Hi! I've finished contacting all your vendors. Here's what I found:\n"
                                    "{{summary}}\n"
                                    "You can also view the full breakdown on your dashboard. Is there anything else you need?'"
                                )
                            },
                            "first_message": "Hi! I have your vendor pricing results ready.",
                        }
                    },
                },
            }
        ],
    }
    r = httpx.post(f"{ELEVEN_URL}/convai/batch-calling/submit", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def get_batch_status(batch_id: str) -> dict:
    r = httpx.get(f"{ELEVEN_URL}/convai/batch-calling/{batch_id}", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()
