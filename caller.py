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
                        "You are a vendor research coordinator. When the user calls:\n"
                        "1. Greet them warmly.\n"
                        "2. Ask which vendors they want to contact (get a list of names).\n"
                        "3. Ask what product/item they need pricing for.\n"
                        "4. Ask the quantity they need (e.g., 50 units).\n"
                        "5. Once you have all three pieces of info, call the start_research tool.\n"
                        "6. Tell the user: 'Got it! I'm contacting your vendors now. I'll call you back "
                        "once all results are in. Have a great day!'\n"
                        "7. End the call."
                    ),
                    "tools": [
                        {
                            "type": "webhook",
                            "name": "start_research",
                            "description": "Trigger vendor research and outbound calls",
                            "api_schema": {
                                "url": f"{server_url}/orchestrate",
                                "method": "POST",
                                "request_body_schema": {
                                    "type": "object",
                                    "properties": {
                                        "vendors": {"type": "array", "items": {"type": "string", "description": "A single vendor name"}, "description": "List of vendor names to contact"},
                                        "product": {"type": "string", "description": "The product or item the client needs pricing for"},
                                        "quantity": {"type": "string", "description": "The quantity of units needed"},
                                        "user_phone": {"type": "string", "description": "The caller phone number for callback"},
                                    },
                                    "required": ["vendors", "product", "quantity"],
                                },
                            },
                        }
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
