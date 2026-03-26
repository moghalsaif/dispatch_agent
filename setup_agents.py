"""One-time setup: create ElevenLabs agents + register Telegram webhook."""
import os
import sys
from dotenv import load_dotenv, set_key

load_dotenv()

import caller
import telegram_bot as tg


def main():
    server_url = os.environ.get("SERVER_URL")
    if not server_url:
        print("ERROR: Set SERVER_URL in .env first")
        sys.exit(1)

    print("1. Creating ElevenLabs inbound agent...")
    inbound_id = caller.create_inbound_agent(server_url)
    set_key(".env", "INBOUND_AGENT_ID", inbound_id)

    print("2. Creating ElevenLabs outbound agent...")
    outbound_id = caller.create_outbound_agent()
    set_key(".env", "OUTBOUND_AGENT_ID", outbound_id)

    print("3. Registering Telegram webhook...")
    tg.set_webhook(server_url)

    print(f"""
Done!
  INBOUND_AGENT_ID  = {inbound_id}
  OUTBOUND_AGENT_ID = {outbound_id}
  Telegram webhook  = {server_url}/telegram/webhook

Next steps:
  1. ElevenLabs dashboard → Conversational AI → Phone Numbers
     → Assign a number to agent: {inbound_id}
     → Copy the Phone Number ID → set ELEVENLABS_AGENT_PHONE_NUMBER_ID in .env

  2. Add your existing vendors:
     python manage_vendors.py add

  3. Start the server:
     uvicorn main:app --reload --port 8000

  4. Users: message your Telegram bot → /link +1xxxxxxxxxx
     (must match the phone they'll call from)
""")


if __name__ == "__main__":
    main()
