from __future__ import annotations

import threading

from app.config import load_config
from app.garmin_client import GarminClient
from app.sheets_db import SheetsDatabase

_state: dict = {}
_lock = threading.Lock()


def get_state() -> dict:
    """Lazily built singleton shared by the Telegram webhook and the webapp.

    Both talk to the same spreadsheet and the same Garmin session, so there
    should only ever be one SheetsDatabase/GarminClient per running instance.
    """
    if not _state:
        with _lock:
            if not _state:
                # Build everything into locals first, and only publish to the
                # shared dict once it all succeeds -- otherwise a failure
                # partway through (e.g. Sheets briefly unreachable) would
                # leave _state half-populated and permanently stuck that way,
                # since `if not _state` would then be true forever.
                config = load_config()
                db = SheetsDatabase(spreadsheet_id=config.spreadsheet_id)
                garmin = GarminClient(
                    tokenstore_path=config.garmin_tokenstore,
                    tokens_b64=config.garmin_tokens_b64,
                )
                _state["config"] = config
                _state["db"] = db
                _state["garmin"] = garmin
    return _state
