import os
from pathlib import Path
from dotenv import load_dotenv

# Always load .env from project root, regardless of current working directory
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

RELAY_PORT = int(os.getenv("RELAY_PORT", "5020"))
RELAY_API_KEY = os.getenv("RELAY_API_KEY", "relay-secret-key-2026")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/token_relay.db")

# A fixed virtual model id exposed as Claude Code's 4th slot via
# ANTHROPIC_CUSTOM_MODEL_OPTION. Claude Code always sends this id; the relay
# rewrites it to whatever real model the user picks in the UI, so switching
# models is instant and requires no Claude Code restart.
DEFAULT_MODEL_ALIAS = "token-relay-default"
DEFAULT_TARGET_KEY = "default_target_model_id"

# 日志详情存储（调用参数 request_body / 响应参数 response_body）
LOG_DETAIL_ENABLED_KEY = "log_detail_enabled"        # AppSetting 中开关的 key
LOG_DETAIL_RETENTION_HOURS = 24                       # 详情保留时长（写死）
LOG_DETAIL_CLEANUP_INTERVAL = 6 * 60 * 60             # 后台清理间隔（秒），每 6 小时一次
