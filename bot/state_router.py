"""
State router — determines agent state from GET /accounts/me response.
Routes per skill.md State Router logic.
"""
from bot.utils.logger import get_logger
from bot.config import PAID_ENTRY_FEE_SMOLTZ

log = get_logger(__name__)

# States
NO_ACCOUNT = "NO_ACCOUNT"
NO_IDENTITY = "NO_IDENTITY"
IN_GAME = "IN_GAME"
READY_PAID = "READY_PAID"
READY_FREE = "READY_FREE"
ERROR = "ERROR"


def _as_int(value, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return default
        try:
            return int(float(cleaned))
        except ValueError:
            return default
    return default


def _account_smoltz(me_response: dict) -> int:
    for key in ("balance", "sMoltz", "smoltz", "SMOLTZ", "s_moltz"):
        if key in me_response and me_response.get(key) is not None:
            return _as_int(me_response.get(key))
    return 0


def determine_state(me_response: dict) -> tuple[str, dict]:
    """
    Analyze /accounts/me response → return (state, context).
    Context contains relevant data for the next step.
    """
    readiness = me_response.get("readiness", {})
    current_games = me_response.get("currentGames", [])
    balance = _account_smoltz(me_response)

    # Check for active game
    for game in current_games:
        if game.get("gameStatus") in ("waiting", "running"):
            log.info("Active game found: %s (status=%s)",
                     game["gameId"], game["gameStatus"])
            return IN_GAME, {
                "game_id": game["gameId"],
                "agent_id": game["agentId"],
                "game_status": game["gameStatus"],
                "entry_type": game.get("entryType", "free"),
                "is_alive": game.get("isAlive", True),
            }

    # Paid rooms do not require ERC-8004 identity. DOCS.md says to prefer paid
    # rooms and only fall back to free when blocked, so check paid first.
    wallet_ok = readiness.get("walletAddress") is not None
    whitelist_ok = readiness.get("whitelistApproved", False)
    paid_ready = readiness.get("paidReady", False) or (
        wallet_ok and whitelist_ok and balance >= PAID_ENTRY_FEE_SMOLTZ
    )
    if paid_ready and balance >= PAID_ENTRY_FEE_SMOLTZ:
        log.info("Paid ready: balance=%d sMoltz", balance)
        return READY_PAID, {"balance": balance}

    # ERC-8004 identity is required for free room access.
    erc8004_id = readiness.get("erc8004Id")
    if erc8004_id is None:
        log.info("No ERC-8004 identity registered")
        return NO_IDENTITY, {"balance": balance}

    # Default to free
    log.info("Ready for free play")
    return READY_FREE, {
        "balance": balance,
        "wallet_address": readiness.get("walletAddress"),
        "whitelist_approved": readiness.get("whitelistApproved", False),
    }
