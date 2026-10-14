"""Shared, quiet LiteLLM runtime."""

import logging
import os
from typing import Any


def get_litellm() -> Any:
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.suppress_debug_info = True
    litellm.turn_off_message_logging = True
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    return litellm
