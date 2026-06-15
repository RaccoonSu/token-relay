import os
from dotenv import load_dotenv

load_dotenv()

RELAY_PORT = int(os.getenv("RELAY_PORT", "5020"))
RELAY_API_KEY = os.getenv("RELAY_API_KEY", "relay-secret-key-2026")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/token_relay.db")
