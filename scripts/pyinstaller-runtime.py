"""Use bundled tokenizer data unless the user selects a different cache."""

import os
from pathlib import Path
import sys

os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(sys._MEIPASS) / "tiktoken_cache"))
