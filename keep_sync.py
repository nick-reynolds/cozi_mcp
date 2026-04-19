"""
Syncs unchecked items from Google Keep "Shopping List" to Cozi Groceries.
Intended to run every 5 minutes via systemd timer.

Authenticates using a Google master token stored in KEEP_TOKEN_FILE or
passed via GOOGLE_MASTER_TOKEN environment variable.
"""
import asyncio
import json
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("keep-cozi-sync")

GOOGLE_EMAIL        = os.environ.get("GOOGLE_EMAIL", "")
GOOGLE_MASTER_TOKEN = os.environ.get("GOOGLE_MASTER_TOKEN", "")
KEEP_TOKEN_FILE     = "/home/ubuntu/cozi_mcp/keep_token.json"
COZI_USERNAME       = os.environ.get("COZI_USERNAME", "")
COZI_PASSWORD       = os.environ.get("COZI_PASSWORD", "")
GROCERIES_LIST_ID   = "9b932345-a393-4557-a8da-8489bacd0035"
KEEP_LIST_NAME      = "Shopping List"


def get_keep():
    import gkeepapi

    keep = gkeepapi.Keep()
    token = None

    # Check saved token file first
    if os.path.exists(KEEP_TOKEN_FILE):
        with open(KEEP_TOKEN_FILE) as f:
            token = json.load(f).get("token")

    # Fall back to env var
    if not token:
        token = GOOGLE_MASTER_TOKEN

    if not token:
        raise RuntimeError("No master token found. Set GOOGLE_MASTER_TOKEN env var.")

    # Save token to file if it came from env var
    if not os.path.exists(KEEP_TOKEN_FILE):
        with open(KEEP_TOKEN_FILE, "w") as f:
            json.dump({"token": token}, f)
        os.chmod(KEEP_TOKEN_FILE, 0o600)
        logger.info("Saved master token to file")

    keep.authenticate(GOOGLE_EMAIL, token)
    return keep


async def main():
    keep = get_keep()
    keep.sync()


    shopping_list = next(
        (n for n in keep.all()
         if hasattr(n, "items")
         and n.title.lower() == KEEP_LIST_NAME.lower()
         and not n.trashed),
        None,
    )

    if not shopping_list:
        logger.error("Keep list '%s' not found", KEEP_LIST_NAME)
        return

    new_items = [i for i in shopping_list.items if not i.checked and i.text.strip()]
    if not new_items:
        logger.info("Nothing to sync")
        return

    # Patch Cozi client to include required API key
    import cozi_client as _cc
    _orig = _cc.CoziClient._make_request

    async def _patched(self, method, endpoint, data=None, params=None, require_auth=True):
        if params is None:
            params = {}
        params.setdefault("apikey", "coziwc|v256_production")
        return await _orig(self, method, endpoint, data=data, params=params, require_auth=require_auth)

    _cc.CoziClient._make_request = _patched

    from cozi_mcp.server import get_cozi_client
    client = await get_cozi_client(COZI_USERNAME, COZI_PASSWORD)

    try:
        for item in new_items:
            text = item.text.strip()
            try:
                await client.add_item(GROCERIES_LIST_ID, text)
                item.checked = True
                logger.info("Synced: %s", text)
            except Exception as e:
                logger.error("Failed to add '%s': %s", text, e)
    finally:
        await client.close()

    keep.sync()


if __name__ == "__main__":
    asyncio.run(main())
