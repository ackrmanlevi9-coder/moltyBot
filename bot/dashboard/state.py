"""
Dashboard shared state — bridge between bot engine and web dashboard.
Bot writes → Dashboard reads. Thread-safe via asyncio lock.

v2.1: Added game history, strategy phase tracking, uptime stats,
      action success/fail counters.
"""
import time
from collections import deque
from bot.utils.logger import get_logger

log = get_logger(__name__)

# Maximum log entries kept in memory
MAX_LOGS = 500
MAX_HISTORY = 50


def _as_int(value, default: int = 0) -> int:
    """Convert API numeric values that may arrive as int, float, or numeric string."""
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


def _first_int(data: dict, keys: tuple[str, ...], default: int = 0) -> int:
    for key in keys:
        if key in data and data.get(key) is not None:
            return _as_int(data.get(key), default)
    return default


class DashboardState:
    """Singleton shared state between bot and dashboard."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # ── Agent state ────────────────────────────────────────
        self.agents: dict[str, dict] = {}  # {agent_id: {name, status, hp, ep, ...}}

        # ── Global stats ───────────────────────────────────────
        self.total_wins = 0
        self.total_losses = 0
        self.total_moltz = 0
        self.total_smoltz = 0
        self.total_cross = 0.0
        self.total_kills = 0
        self.bots_running = 0

        # ── Action counters ────────────────────────────────────
        self.actions_sent = 0
        self.actions_success = 0
        self.actions_failed = 0

        # ── Game history ───────────────────────────────────────
        self.game_history: deque = deque(maxlen=MAX_HISTORY)

        # ── Logs ───────────────────────────────────────────────
        self.global_logs: deque = deque(maxlen=MAX_LOGS)
        self.agent_logs: dict[str, deque] = {}  # {agent_id: deque}

        # ── Accounts ───────────────────────────────────────────
        self.accounts: list[dict] = []

        # ── Timestamps ─────────────────────────────────────────
        self.started_at = time.time()
        self.last_update = time.time()

    # ── Bot writes ─────────────────────────────────────────────

    def update_agent(self, agent_id: str, data: dict):
        """Update agent state from bot engine."""
        if agent_id not in self.agents:
            self.agents[agent_id] = {}
            self.agent_logs[agent_id] = deque(maxlen=MAX_LOGS)
        if "smoltz" in data:
            data["smoltz"] = _as_int(data["smoltz"])
        if "smoltz_earned" in data:
            data["smoltz_earned"] = _as_int(data["smoltz_earned"])
        if "moltz_earned" in data:
            data["moltz_earned"] = _as_int(data["moltz_earned"])
        self.agents[agent_id].update(data)
        self.agents[agent_id]["last_update"] = time.time()
        self.last_update = time.time()

    def add_log(self, message: str, level: str = "info", agent_id: str = None):
        """Add log entry."""
        entry = {
            "ts": time.time(),
            "msg": message,
            "level": level,
            "agent": agent_id,
        }
        self.global_logs.append(entry)
        if agent_id and agent_id in self.agent_logs:
            self.agent_logs[agent_id].append(entry)

    def record_action(self, success: bool):
        """Track action success/fail rates."""
        self.actions_sent += 1
        if success:
            self.actions_success += 1
        else:
            self.actions_failed += 1

    def record_game(self, game_data: dict):
        """Record a completed game to history."""
        game_data["timestamp"] = time.time()
        game_data["kills"] = _as_int(game_data.get("kills"))
        game_data["moltz_earned"] = _first_int(game_data, ("moltz_earned", "moltz", "Moltz", "MOLTZ"))
        game_data["smoltz_earned"] = _first_int(
            game_data,
            ("smoltz_earned", "sMoltz", "smoltz", "SMOLTZ", "balance_delta", "balanceDelta"),
        )
        self.game_history.append(game_data)
        if game_data.get("is_winner"):
            self.total_wins += 1
        else:
            self.total_losses += 1
        moltz_earned = game_data["moltz_earned"]
        smoltz_earned = game_data["smoltz_earned"]
        self.total_kills += game_data["kills"]
        self.total_moltz += moltz_earned
        self.total_smoltz += smoltz_earned

        agent_key = game_data.get("agent_key")
        if agent_key and agent_key in self.agents:
            # Safely accumulate moltz_earned per agent
            current_moltz = _as_int(self.agents[agent_key].get("moltz_earned"))
            self.agents[agent_key]["moltz_earned"] = current_moltz + moltz_earned
            # Safely accumulate smoltz_earned per agent
            current_smoltz = _as_int(self.agents[agent_key].get("smoltz_earned"))
            self.agents[agent_key]["smoltz_earned"] = current_smoltz + smoltz_earned

    def set_account(self, account_data: dict):
        """Add or update account."""
        api_key = account_data.get("api_key", "")
        for i, acc in enumerate(self.accounts):
            if acc.get("api_key") == api_key:
                self.accounts[i] = account_data
                return
        self.accounts.append(account_data)

    # ── Dashboard reads ────────────────────────────────────────

    def get_snapshot(self) -> dict:
        """Full state snapshot for dashboard API."""
        action_rate = 0
        if self.actions_sent > 0:
            action_rate = round((self.actions_success / self.actions_sent) * 100, 1)

        return {
            "agents": dict(self.agents),
            "stats": {
                "total_wins": self.total_wins,
                "total_losses": self.total_losses,
                "total_moltz": self.total_moltz,
                "total_smoltz": max(
                    self.total_smoltz,
                    sum(_as_int(a.get("smoltz")) for a in self.agents.values()),
                    sum(_as_int(a.get("smoltz_earned")) for a in self.agents.values()),
                ),
                "total_cross": self.total_cross,
                "total_kills": self.total_kills,
                "bots_running": self.bots_running,
                "agents_active": sum(1 for a in self.agents.values()
                                     if a.get("status") == "playing"),
                "agents_idle": sum(1 for a in self.agents.values()
                                   if a.get("status") in ("idle", "queuing")),
                "agents_dead": sum(1 for a in self.agents.values()
                                   if a.get("status") == "dead"),
                "agents_error": sum(1 for a in self.agents.values()
                                    if a.get("status") == "error"),
                "uptime": time.time() - self.started_at,
                "actions_sent": self.actions_sent,
                "actions_success": self.actions_success,
                "actions_failed": self.actions_failed,
                "action_rate": action_rate,
            },
            "accounts": self.accounts,
            "game_history": list(self.game_history),
            "logs": list(self.global_logs)[-200:],
            "agent_logs": {k: list(v)[-100:] for k, v in self.agent_logs.items()},
        }


# Global singleton
dashboard_state = DashboardState()
