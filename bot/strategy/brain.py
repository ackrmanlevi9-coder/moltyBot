"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v1.5.2 changes:
- Guardians now ATTACK player agents directly (hostile combatants)
- Curse is TEMPORARILY DISABLED (no whisper Q&A flow)
- Free room: 5 guardians (reduced from 30), each drops 120 sMoltz
- connectedRegions: either full Region objects OR bare string IDs — type-check!
- pendingDeathzones: entries are {id, name} objects

v2.1.0 optimizations:
- Fixed weapon stats to match DOCS.md combat-items (Knife +5, Sword +8, etc.)
- Fixed _is_in_range: strict regionId check, no more "assume same region" fallback
- Fixed move EP cost: base=3 per DOCS.md (was incorrectly 2)
- Added explore action for item discovery
- Added failed-action tracking to prevent infinite retry loops
- Added phase-based strategy (early/mid/late game)
- Added kill-potential combat scoring
- Improved target selection with multi-factor scoring

Uses ALL view fields from api-summary.md:
- self: agent stats, inventory, equipped weapon
- currentRegion: terrain, weather, connections, facilities
- connectedRegions: adjacent regions (full Region object when visible, bare string ID when out-of-vision)
- visibleRegions: all regions in vision range
- visibleAgents: other agents (players + guardians — guardians are HOSTILE)
- visibleMonsters: monsters
- visibleNPCs: NPCs (flavor — safe to ignore per game-systems.md)
- visibleItems: ground items in visible regions
- pendingDeathzones: regions becoming death zones next ({id, name} entries)
- recentLogs: recent gameplay events
- recentMessages: regional/private/broadcast messages
- aliveCount: remaining alive agents
"""
from bot.utils.logger import get_logger

log = get_logger(__name__)

# ── Weapon stats from DOCS.md combat-items ────────────────────────────
# FIXED: Values now match DOCS.md exactly (were previously double/wrong)
WEAPONS = {
    "fist":   {"bonus": 0,  "range": 0},
    "knife":  {"bonus": 5,  "range": 0},  # DOCS says Knife +5
    "dagger": {"bonus": 5,  "range": 0},  # Alias for knife
    "sword":  {"bonus": 8,  "range": 0},  # DOCS says Sword +8
    "katana": {"bonus": 21, "range": 0},  # DOCS says Katana +21
    "bow":    {"bonus": 3,  "range": 1},  # DOCS says Bow +3
    "pistol": {"bonus": 6,  "range": 1},  # DOCS says Pistol +6
    "sniper": {"bonus": 17, "range": 2},  # DOCS says Sniper +17
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "knife", "dagger", "bow", "fist"]

# ── Item priority for pickup ──────────────────────────────────────────
# Moltz = ALWAYS pickup (highest). Weapons > healing > utility.
# Binoculars = passive (vision+1 just by holding), always pickup.
ITEM_PRIORITY = {
    "rewards": 300,  # Moltz/sMoltz — ALWAYS pickup first
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "knife": 80, "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55,  # Passive: vision +1 permanent, always pickup
    "map": 52,          # Use immediately to reveal entire map
    "megaphone": 40,
}

# ── Recovery items for healing (combat-items.md) ──────────────────────
# For normal healing (HP<70): prefer Emergency Food (save Bandage/Medkit)
# For critical healing (HP<30): prefer Bandage then Medkit
RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,  # EP restore, not HP
}

# Weather combat penalty per game-systems.md
WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,   # -5%
    "fog": 0.10,    # -10%
    "storm": 0.15,  # -15%
}

# ── Failed action tracking (prevents retry loops) ─────────────────────
_failed_targets: set = set()  # Target IDs that failed this turn
_last_failed_action: str = ""  # Last action type that failed
_explored_regions: set = set()  # Regions already explored this game


def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    """Damage formula per combat-items.md + game-systems.md weather penalty.
    Base: ATK + bonus - (DEF * 0.5), min 1.
    Weather: clear=0%, rain=-5%, fog=-10%, storm=-15%.
    """
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped_weapon) -> int:
    """Get ATK bonus from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_range(equipped_weapon) -> int:
    """Get range from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)

_known_agents: dict = {}
# Map knowledge: track all revealed DZ/pending DZ/safe regions after using Map
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}


def _resolve_region(entry, view: dict):
    """Resolve a connectedRegions entry to a full region object.
    Per v1.5.2 gotchas.md §3: entries are EITHER full Region objects
    (when adjacent region is within vision) OR bare string IDs (when out-of-vision).
    Returns the full object, or None if out-of-vision.
    """
    if isinstance(entry, dict):
        return entry  # Full object
    if isinstance(entry, str):
        # Look up in visibleRegions
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None  # Out-of-vision — only ID is known


def _get_region_id(entry) -> str:
    """Extract region ID from either a string or dict entry."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def reset_game_state():
    """Reset per-game tracking state. Call when game ends."""
    global _known_agents, _map_knowledge, _failed_targets, _last_failed_action, _explored_regions
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _failed_targets = set()
    _last_failed_action = ""
    _explored_regions = set()
    log.info("Strategy brain reset for new game")


def mark_action_failed(target_id: str = "", action_type: str = ""):
    """Called by WebSocket engine when an action fails.
    Prevents retry loops by tracking failed targets/actions.
    """
    global _failed_targets, _last_failed_action
    if target_id:
        _failed_targets.add(target_id)
        log.debug("Marked target %s as failed (won't retry this turn)", target_id[:8])
    if action_type:
        _last_failed_action = action_type


def clear_turn_failures():
    """Called at start of each new turn to reset failure tracking."""
    global _failed_targets, _last_failed_action
    _failed_targets = set()
    _last_failed_action = ""


def _get_game_phase(alive_count: int) -> str:
    """Determine game phase based on alive count.
    Early: > 40 alive → focus on looting & exploring
    Mid:   15-40 alive → farm monsters/guardians, start positioning
    Late:  < 15 alive → aggressive combat, secure kills for ranking
    """
    if alive_count > 40:
        return "early"
    elif alive_count > 15:
        return "mid"
    else:
        return "late"


def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine. Returns action dict or None (wait).

    Priority chain per game-loop.md §3 (v2.1.0 optimized):
    1. DEATHZONE ESCAPE (overrides everything — 1.34 HP/sec!)
    1b. Pre-escape pending death zone
    2. [DISABLED] Curse resolution — curse temporarily disabled in v1.5.2
    2b. Guardian threat evasion (guardians now attack players!)
    3. Critical healing (HP < 30)
    3b. Use utility items (Map, Energy Drink)
    4. Free actions (pickup, equip) — always do first
    5. Guardian farming (120 sMoltz per kill — only 5 guardians!)
    6. Favorable agent combat (with kill-potential scoring)
    7. Monster farming
    7b. Moderate healing (HP < 70, safe area)
    8. Facility interaction
    8b. EXPLORE current region (NEW — discover items!)
    9. Strategic movement (NEVER into DZ or pending DZ)
    10. Rest

    Uses ALL api-summary.md view fields for decision making.
    """
    self_data = view.get("self", {})
    region = view.get("currentRegion", {})
    hp = self_data.get("hp", 100)
    ep = self_data.get("ep", 10)
    max_ep = self_data.get("maxEp", 10)
    atk = self_data.get("atk", 10)
    defense = self_data.get("def", 5)
    is_alive = self_data.get("isAlive", True)
    inventory = self_data.get("inventory", [])
    equipped = self_data.get("equippedWeapon")

    # View-level fields per api-summary.md
    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])
    visible_npcs = view.get("visibleNPCs", [])
    visible_items_raw = view.get("visibleItems", [])
    # Unwrap: each visibleItem is { regionId, item: { id, name, typeId, ... } }
    visible_items = []
    for entry in visible_items_raw:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("item")
        if isinstance(inner, dict):
            inner["regionId"] = entry.get("regionId", "")
            visible_items.append(inner)
        elif entry.get("id"):
            visible_items.append(entry)  # Legacy flat format
    visible_regions = view.get("visibleRegions", [])
    connected_regions = view.get("connectedRegions", [])
    pending_dz = view.get("pendingDeathzones", [])
    recent_logs = view.get("recentLogs", [])
    messages = view.get("recentMessages", [])
    alive_count = view.get("aliveCount", 100)

    # Fallback connections from currentRegion if connectedRegions empty
    connections = connected_regions or region.get("connections", [])
    interactables = region.get("interactables", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""

    if not is_alive:
        return None  # Dead — wait for game_ended

    # ── Determine game phase ──────────────────────────────────────
    phase = _get_game_phase(alive_count)

    # ── Build FULL danger map (DZ + pending DZ) ───────────────────
    # Used by ALL movement decisions to NEVER move into danger.
    # v1.5.2: pendingDeathzones entries are {id, name} objects
    danger_ids = set()
    for dz in pending_dz:
        if isinstance(dz, dict):
            danger_ids.add(dz.get("id", ""))
        elif isinstance(dz, str):
            danger_ids.add(dz)  # Legacy fallback
    # Also mark currently-active death zones from connected regions
    for conn in connections:
        resolved = _resolve_region(conn, view)
        if resolved and resolved.get("isDeathZone"):
            danger_ids.add(resolved.get("id", ""))

    # Track visible agents for memory
    _track_agents(visible_agents, self_data.get("id", ""), region_id)

    # ── Priority 1: DEATHZONE ESCAPE (overrides everything) ───────
    # Per game-systems.md: 1.34 HP/sec damage — bot dies fast!
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 IN DEATH ZONE! Escaping to %s (HP=%d)", safe, hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"ESCAPE: In death zone! HP={hp} dropping fast (1.34/sec)"}
        elif not safe:
            log.error("🚨 IN DEATH ZONE but NO SAFE REGION! All neighbors are DZ!")

    # ── Priority 1b: Pre-escape pending death zone ────────────────
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ Region %s becoming DZ soon! Escaping to %s", region_id[:8], safe)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Region becoming death zone soon"}

    # ── Priority 2: Curse resolution — DISABLED in v1.5.2 ─────────
    # Curse is temporarily disabled. Guardians no longer curse players.
    # Legacy code kept inert — will re-enable when curse returns.
    # (was: _check_curse → whisper answer to guardian)

    # ── Priority 2b: Guardian threat evasion (v1.5.2) ─────────────
    # Guardians now ATTACK player agents directly! Flee if low HP.
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and _is_same_region(a, region_id)]
    if guardians_here and hp < 40 and ep >= move_ep_cost:
        # Low HP + guardian in same region = flee!
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ Guardian threat! HP=%d, fleeing to safety", hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"GUARDIAN FLEE: HP={hp}, guardian in region, too dangerous"}

    # ── FREE ACTIONS (no cooldown, do before main action) ─────────

    # Auto-pickup Moltz (currency) and valuable items
    pickup_action = _check_pickup(visible_items, inventory, region_id)
    if pickup_action:
        return pickup_action

    # Auto-equip better weapon
    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    # Use utility items: Map (reveal map), Energy Drink (EP recovery)
    util_action = _use_utility_item(inventory, hp, ep, alive_count)
    if util_action:
        return util_action

    # If cooldown active, only free actions allowed
    if not can_act:
        return None

    # (Death zone escape already handled above as Priority 1)

    # ── Priority 3: Healing management ─────────────────────────────
    # HP < 30 = CRITICAL: use Bandage first (30 HP), then Medkit (50 HP)
    # HP < 70 = MODERATE: use Emergency Food first (20 HP), save better items
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}, using {heal.get('typeId', 'heal')}"}
    elif hp < 50 and phase != "early":
        # Mid/late game: heal at HP<50 to stay combat-ready
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}, using {heal.get('typeId', 'heal')} (mid/late game)"}

    # ── Priority 4: EP recovery if EP=0 ──────────────────────────
    if ep == 0:
        # Check for energy drink first
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": "EP RECOVERY: EP=0, using energy drink (+5 EP)"}

    # ── Priority 5: Guardian farming (v1.5.2: 120 sMoltz per kill!) ─
    # Only 5 guardians per free room — each worth 120 sMoltz!
    # Guardians now ATTACK back — only fight if we can win.
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)
                 and a.get("id") not in _failed_targets]
    if guardians and ep >= 2 and hp >= 35:
        target = _select_best_target(guardians, atk, equipped, defense, region_weather,
                                     region_id, connections)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                    target.get("def", 5), region_weather)
                guardian_dmg = calc_damage(target.get("atk", 10),
                                           _estimate_enemy_weapon_bonus(target),
                                           defense, region_weather)
                # Fight if we deal more damage OR target is low HP (finish off)
                if my_dmg >= guardian_dmg or target.get("hp", 100) <= my_dmg * 3:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"GUARDIAN FARM: HP={target.get('hp','?')} "
                                      f"(120 sMoltz! dmg={my_dmg} vs {guardian_dmg})"}

    # ── Priority 6: Favorable agent combat ────────────────────────
    # Phase-adaptive aggression + kill-potential scoring
    # Avoid combat in storm(-15%) or fog(-10%) unless we heavily outgun them
    weather_penalty = WEATHER_COMBAT_PENALTY.get(region_weather, 0.0)
    hp_threshold = _get_combat_hp_threshold(phase)

    enemies = [a for a in visible_agents
               if not a.get("isGuardian", False) and a.get("isAlive", True)
               and a.get("id") != self_data.get("id")
               and a.get("id") not in _failed_targets]

    if enemies and ep >= 2 and hp >= hp_threshold:
        target = _select_best_target(enemies, atk, equipped, defense, region_weather,
                                     region_id, connections)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                    target.get("def", 5), region_weather)
                enemy_dmg = calc_damage(target.get("atk", 10),
                                         _estimate_enemy_weapon_bonus(target),
                                         defense, region_weather)
                target_hp = target.get("hp", 100)
                # Kill potential: how many hits to kill?
                hits_to_kill = max(1, (target_hp + my_dmg - 1) // my_dmg) if my_dmg > 0 else 99
                hits_to_die = max(1, (hp + enemy_dmg - 1) // enemy_dmg) if enemy_dmg > 0 else 99

                # Fight if: we can kill them before they kill us,
                # OR target is very low HP (finish off), OR late game aggressive
                should_fight = (
                    hits_to_kill < hits_to_die  # We kill them first
                    or target_hp <= my_dmg * 2  # Can kill in 1-2 hits
                    or (phase == "late" and my_dmg >= enemy_dmg)  # Late game aggression
                    or (weather_penalty == 0 and my_dmg > enemy_dmg)  # Clear weather advantage
                )

                if should_fight:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"COMBAT: Target HP={target_hp}, "
                                      f"dmg={my_dmg} vs {enemy_dmg}, "
                                      f"kills_in={hits_to_kill} vs dies_in={hits_to_die} "
                                      f"[{phase}]"}

    # ── Priority 7: Monster farming ───────────────────────────────
    monsters_here = [m for m in visible_monsters
                     if m.get("hp", 0) > 0
                     and m.get("id") not in _failed_targets]
    if monsters_here and ep >= 2:
        target = _select_best_target(monsters_here, atk, equipped, defense, region_weather,
                                     region_id, connections)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": f"MONSTER FARM: {target.get('name', 'monster')} HP={target.get('hp', '?')}"}

    # ── Priority 7b: Moderate healing (HP < 70, safe area) ────────
    if hp < 70 and not enemies:
        heal = _find_healing_item(inventory, critical=(hp < 30))
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}, area safe, using {heal.get('typeId', 'heal')}"}

    # ── Priority 8: Facility interaction ──────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact",
                    "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}

    # ── Priority 8b: EXPLORE current region (NEW) ─────────────────
    # Search current region for items if we haven't explored it yet
    # and there's nothing better to do. Costs 2 EP.
    if ep >= 2 and region_id and region_id not in _explored_regions:
        # Don't explore if in death zone or pending DZ
        if not region.get("isDeathZone") and region_id not in danger_ids:
            # Early game: always explore for weapons
            # Mid/late game: explore only if we have EP to spare
            should_explore = (
                phase == "early"
                or (phase == "mid" and ep >= 4)
                or (phase == "late" and ep >= 6)
            )
            if should_explore:
                _explored_regions.add(region_id)
                log.info("🔍 Exploring region %s for items (phase=%s)", region_id[:8], phase)
                return {"action": "explore", "data": {},
                        "reason": f"EXPLORE: Searching region for items [{phase} game]"}

    # ── Priority 9: Strategic movement ────────────────────────────
    # Use connectedRegions — NEVER move into DZ or pending DZ!
    if ep >= move_ep_cost and connections:
        move_target = _choose_move_target(connections, danger_ids,
                                           region, visible_items, alive_count,
                                           phase, view)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": f"MOVE: Strategic positioning [{phase}]"}

    # ── Priority 10: Rest (EP < 4 and safe) ───────────────────────
    if ep < 4 and not enemies and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}, area is safe (+1 bonus EP)"}

    return None  # Wait for next turn


# ── Helper functions ──────────────────────────────────────────────────

def _get_move_ep_cost(terrain: str, weather: str) -> int:
    """Calculate move EP cost per DOCS.md.
    FIXED: Base move cost is 3 EP (was incorrectly 2).
    Storm and water terrain may increase it.
    """
    base = 3  # DOCS.md: move costs 3 EP
    if terrain == "water":
        return base + 1  # Water: extra penalty
    if weather == "storm":
        return base + 1  # Storm: extra penalty
    return base


def _get_combat_hp_threshold(phase: str) -> int:
    """Get minimum HP to engage in combat based on game phase.
    Early game: conservative (need HP for exploring)
    Mid game: moderate
    Late game: aggressive (ranking matters most)
    """
    if phase == "early":
        return 50  # Don't fight unless healthy
    elif phase == "mid":
        return 35
    else:  # late
        return 20  # Fight even when low


def _is_same_region(agent: dict, my_region: str) -> bool:
    """Check if an agent is confirmed to be in the same region.
    STRICT check: only returns True if regionId matches.
    """
    target_region = agent.get("regionId", "")
    if not target_region:
        # No regionId on target — CANNOT confirm same region
        # This is the fix for the "out of range" attack spam bug
        return False
    return target_region == my_region


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimate enemy's weapon bonus from their equipped weapon."""
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


# Track observed agents for memory (threat assessment)
_known_agents: dict = {}


# ── CURSE HANDLING — DISABLED in v1.5.2 ───────────────────────────────
# Curse is temporarily disabled per strategy.md v1.5.2.
# Guardians no longer set victim EP to 0 and no whisper-question/answer flow.
# Legacy code kept below for reference — will re-enable when curse returns.
#
# def _check_curse(messages, my_id) -> dict | None:
#     """DISABLED: Guardian curse is temporarily disabled in v1.5.2."""
#     return None
#
# def _solve_curse_question(question) -> str:
#     """DISABLED: Guardian curse is temporarily disabled in v1.5.2."""
#     return ""


def _check_pickup(items: list, inventory: list, region_id: str) -> dict | None:
    """Smart pickup: weapons > healing stockpile > utility > Moltz (always).
    Max inventory = 10 per limits.md.
    Strategy:
    - Moltz ($rewards): ALWAYS pickup, highest priority
    - Weapons: pickup if better than current OR no weapon equipped
    - Healing: stockpile for endgame (keep at least 2-3 healing items)
    - Binoculars: passive vision+1, always pickup
    - Map: pickup and use immediately

    FIXED v2.1.1: No more fallback to all visible items.
    Only pick items CONFIRMED in current region. Items without regionId
    or in other regions are skipped to prevent "Item not found" spam.
    """
    if len(inventory) >= 10:
        return None

    # STRICT: Only items confirmed in current region + not already failed
    local_items = [i for i in items
                   if isinstance(i, dict)
                   and i.get("id")
                   and i.get("regionId") == region_id
                   and i.get("id") not in _failed_targets]

    # NO FALLBACK — if no items match current region, don't try to pick anything
    if not local_items:
        return None

    # Count current healing items for stockpile management
    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)

    # Sort by priority — Moltz always first
    local_items.sort(
        key=lambda i: _pickup_score(i, inventory, heal_count), reverse=True)
    best = local_items[0]
    score = _pickup_score(best, inventory, heal_count)
    if score > 0:
        type_id = best.get('typeId', 'item')
        log.info("PICKUP: %s (score=%d, heal_stock=%d)", type_id, score, heal_count)
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": f"PICKUP: {type_id}"}
    return None


def _pickup_score(item: dict, inventory: list, heal_count: int) -> int:
    """Calculate dynamic pickup score based on current inventory state."""
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()

    # Moltz/sMoltz — ALWAYS pickup
    if type_id == "rewards" or category == "currency":
        return 300

    # Weapons: higher score if no weapon or this is better
    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        # Check current best weapon in inventory
        current_best = 0
        for inv_item in inventory:
            if isinstance(inv_item, dict) and inv_item.get("category") == "weapon":
                cb = WEAPONS.get(inv_item.get("typeId", "").lower(), {}).get("bonus", 0)
                current_best = max(current_best, cb)
        if bonus > current_best:
            return 100 + bonus  # Better weapon = very high priority
        return 0  # Already have equal or better

    # Binoculars: passive vision+1 permanent, always pickup
    if type_id == "binoculars":
        has_binos = any(isinstance(i, dict) and i.get("typeId", "").lower() == "binoculars"
                       for i in inventory)
        return 55 if not has_binos else 0  # Don't stack

    # Map: always pickup (will be used immediately)
    if type_id == "map":
        return 52

    # Healing items: stockpile for endgame (want 3-4 items)
    if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS.get(type_id, 0) > 0:
        if heal_count < 4:  # Need more healing for endgame
            return ITEM_PRIORITY.get(type_id, 0) + 10
        return ITEM_PRIORITY.get(type_id, 0)  # Normal priority

    # Energy drink
    if type_id == "energy_drink":
        return 58

    return ITEM_PRIORITY.get(type_id, 0)


def _check_equip(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon from inventory."""
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    best = None
    best_bonus = current_bonus
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "weapon":
            type_id = item.get("typeId", "").lower()
            bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
            if bonus > best_bonus:
                best = item
                best_bonus = bonus
    if best:
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')} (+{best_bonus} ATK)"}
    return None


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone AND NOT pending DZ.
    Per v1.5.2 gotchas.md §3: connectedRegions entries are EITHER full Region objects
    (when visible) OR bare string IDs (when out-of-vision). Use _resolve_region().
    danger_ids = set of all DZ + pending DZ region IDs.
    """
    safe_regions = []
    for conn in connections:
        if isinstance(conn, str):
            if conn not in danger_ids:
                safe_regions.append((conn, 0))
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            is_dz = conn.get("isDeathZone", False)
            if rid and not is_dz and rid not in danger_ids:
                terrain = conn.get("terrain", "").lower()
                score = {"hills": 3, "plains": 2, "ruins": 1, "forest": 0, "water": -2}.get(terrain, 0)
                safe_regions.append((rid, score))

    if safe_regions:
        safe_regions.sort(key=lambda x: x[1], reverse=True)
        chosen = safe_regions[0][0]
        log.debug("Safe region selected: %s (score=%d, %d candidates)",
                  chosen[:8], safe_regions[0][1], len(safe_regions))
        return chosen

    # Last resort: any non-DZ connection (even if pending)
    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz:
            log.warning("No fully safe region! Using fallback: %s", rid[:8])
            return rid
    return None


def _find_healing_item(inventory: list, critical: bool = False) -> dict | None:
    """Find best healing item based on urgency.
    critical=True (HP<30): prefer Bandage(30) then Medkit(50) — big heals first
    critical=False (HP<70): prefer Emergency Food(20) — save big heals for later
    """
    heals = []
    for i in inventory:
        if not isinstance(i, dict):
            continue
        type_id = i.get("typeId", "").lower()
        if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS[type_id] > 0:
            heals.append(i)
    if not heals:
        return None

    if critical:
        # Critical: use biggest heal first (Medkit > Bandage > Emergency Food)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
    else:
        # Normal: use smallest heal first (Emergency Food first, save big heals)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0))
    return heals[0]


def _find_energy_drink(inventory: list) -> dict | None:
    """Find energy drink for EP recovery (+5 EP per combat-items.md)."""
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _select_best_target(targets: list, my_atk: int, my_weapon,
                         my_def: int, weather: str,
                         my_region: str, connections=None) -> dict | None:
    """Select the best target using multi-factor scoring.
    Factors:
    - Kill potential (can we kill in few hits?)
    - Risk (how much damage will we take?)
    - Range (prefer same-region targets for guaranteed hit)
    - HP (prefer low HP for easy kills)

    Returns the best target, or None if no valid target.
    """
    w_range = get_weapon_range(my_weapon)
    w_bonus = get_weapon_bonus(my_weapon)

    scored = []
    for t in targets:
        if not isinstance(t, dict):
            continue

        # Skip targets we've already failed to hit
        if t.get("id", "") in _failed_targets:
            continue

        # Check if in range
        if not _is_in_range(t, my_region, w_range, connections):
            continue

        target_hp = t.get("hp", 100)
        target_def = t.get("def", 5)
        target_atk = t.get("atk", 10)

        my_dmg = calc_damage(my_atk, w_bonus, target_def, weather)
        enemy_dmg = calc_damage(target_atk, _estimate_enemy_weapon_bonus(t), my_def, weather)

        hits_to_kill = max(1, (target_hp + my_dmg - 1) // my_dmg) if my_dmg > 0 else 99

        # Score: lower is better target
        # Prioritize: (1) can kill quickly, (2) take less damage, (3) same region
        score = 0
        score += hits_to_kill * 10  # Fewer hits to kill = much better
        score += enemy_dmg * 2      # Less enemy damage = better
        score -= my_dmg             # More our damage = better

        # Strong bonus for same-region targets (guaranteed range)
        if _is_same_region(t, my_region):
            score -= 20  # Big bonus for same region

        scored.append((t, score))

    if not scored:
        return None

    # Return the target with lowest score (best)
    scored.sort(key=lambda x: x[1])
    return scored[0][0]


def _select_weakest(targets: list) -> dict:
    """Select target with lowest HP. Fallback method."""
    return min(targets, key=lambda t: t.get("hp", 999))


def _is_in_range(target: dict, my_region: str, weapon_range: int,
                  connections=None) -> bool:
    """Check if target is in weapon range.
    Per combat-items.md: melee = same region, ranged = 1-2 regions.

    FIXED: No longer assumes same region when regionId is missing.
    Missing regionId = UNKNOWN position = cannot confirm in range.
    """
    target_region = target.get("regionId", "")

    # If target has no regionId, we CAN'T confirm they're in range
    # This fixes the "Target is out of range" spam bug
    if not target_region:
        log.debug("Target %s has no regionId — skipping (unknown position)",
                  target.get("id", "?")[:8])
        return False

    if target_region == my_region:
        return True  # Same region — melee and ranged both work

    if weapon_range >= 1 and connections:
        # Check if target is in an adjacent region (range 1+)
        adj_ids = set()
        for conn in connections:
            if isinstance(conn, str):
                adj_ids.add(conn)
            elif isinstance(conn, dict):
                adj_ids.add(conn.get("id", ""))
        if target_region in adj_ids:
            return True

    # Target is out of weapon range
    return False


def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
    """Select best facility to interact with per game-systems.md.
    Facilities: supply_cache, medical_facility, watchtower, broadcast_station, cave.
    """
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        if fac.get("isUsed"):
            continue
        ftype = fac.get("type", "").lower()
        # Priority: medical (if HP < 80) > supply_cache > watchtower > broadcast_station
        if ftype == "medical_facility" and hp < 80:
            return fac
        if ftype == "supply_cache":
            return fac
        if ftype == "watchtower":
            return fac
        if ftype == "broadcast_station":
            return fac
    return None


def _track_agents(visible_agents: list, my_id: str, my_region: str):
    """Track observed agents for threat assessment (agent-memory.md temp.knownAgents)."""
    global _known_agents
    for agent in visible_agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid or aid == my_id:
            continue
        _known_agents[aid] = {
            "hp": agent.get("hp", 100),
            "atk": agent.get("atk", 10),
            "isGuardian": agent.get("isGuardian", False),
            "equippedWeapon": agent.get("equippedWeapon"),
            "lastSeen": my_region,
            "isAlive": agent.get("isAlive", True),
        }
    # Limit size
    if len(_known_agents) > 50:
        # Remove dead agents first
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    """Use utility items immediately after pickup.
    Map: reveals entire map → triggers _learn_from_map next view.
    Binoculars: PASSIVE (vision+1 just by holding) — no use_item needed.
    """
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        # Map: use immediately to reveal entire map
        if type_id == "map":
            log.info("🗺️ Using Map! Will reveal entire map for strategic learning.")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Using Map — reveals entire map for DZ tracking"}
    return None


def learn_from_map(view: dict):
    """Called after Map is used — learn entire map layout.
    Track all death zones, pending DZ, and find safe center regions.
    Per game-guide.md: Map reveals entire map (1-time consumable).
    """
    global _map_knowledge
    visible_regions = view.get("visibleRegions", [])
    if not visible_regions:
        return

    _map_knowledge["revealed"] = True
    safe_regions = []

    for region in visible_regions:
        if not isinstance(region, dict):
            continue
        rid = region.get("id", "")
        if not rid:
            continue

        if region.get("isDeathZone"):
            _map_knowledge["death_zones"].add(rid)
        else:
            # Count connections — center regions have more connections
            conns = region.get("connections", [])
            terrain = region.get("terrain", "").lower()
            terrain_value = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -1}.get(terrain, 0)
            score = len(conns) + terrain_value
            safe_regions.append((rid, score))

    # Sort by connectivity+terrain — highest = most likely center
    safe_regions.sort(key=lambda x: x[1], reverse=True)
    _map_knowledge["safe_center"] = [r[0] for r in safe_regions[:5]]

    log.info("🗺️ MAP LEARNED: %d DZ regions, %d safe regions, top center: %s",
             len(_map_knowledge["death_zones"]),
             len(safe_regions),
             _map_knowledge["safe_center"][:3])


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int, phase: str = "mid",
                         view: dict = None) -> str | None:
    """Choose best region to move to.
    CRITICAL: NEVER move into a death zone or pending death zone!
    Phase-adaptive movement:
    - Early: prefer unexplored regions with potential items
    - Mid: prefer center regions, facilities
    - Late: prefer strategic positions with low enemy density
    """
    candidates = []

    # Build set of regions with visible items for attraction
    item_regions = set()
    for item in visible_items:
        if isinstance(item, dict):
            item_regions.add(item.get("regionId", ""))

    for conn in connections:
        if isinstance(conn, str):
            # HARD BLOCK: never move into danger zone
            if conn in danger_ids:
                continue
            score = 1
            if conn in item_regions:
                score += 5
            # Prefer unexplored regions
            if conn not in _explored_regions:
                score += 3
            candidates.append((conn, score))

        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            # HARD BLOCK: never move into DZ or pending DZ
            if not rid or conn.get("isDeathZone") or rid in danger_ids:
                continue

            score = 0
            terrain = conn.get("terrain", "").lower()

            # Terrain scoring per game-systems.md
            terrain_scores = {
                "hills": 4, "plains": 2, "ruins": 2,
                "forest": 1, "water": -3,
            }
            score += terrain_scores.get(terrain, 0)

            if rid in item_regions:
                score += 5

            # Prefer unexplored regions
            if rid not in _explored_regions:
                score += 3

            # Facilities attract
            facs = conn.get("interactables", [])
            if facs:
                unused = [f for f in facs if isinstance(f, dict) and not f.get("isUsed")]
                score += len(unused) * 2

            # Avoid weather penalties
            weather = conn.get("weather", "").lower()
            weather_penalty = {"storm": -2, "fog": -1, "rain": 0, "clear": 1}
            score += weather_penalty.get(weather, 0)

            # Phase-based scoring
            if phase == "early":
                # Early: prefer regions with items or unexplored
                if rid not in _explored_regions:
                    score += 2
            elif phase == "mid":
                # Mid: moderate bonus for safe regions
                score += 2
            else:
                # Late: strong bonus for central, safe positions
                score += 3

            # MAP KNOWLEDGE: prefer center regions learned from Map
            if _map_knowledge.get("revealed") and rid in _map_knowledge.get("safe_center", []):
                score += 5  # Strong pull toward center

            # MAP KNOWLEDGE: avoid known death zones
            if rid in _map_knowledge.get("death_zones", set()):
                continue  # HARD BLOCK

            candidates.append((rid, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]
"""
View fields from api-summary.md (all implemented above — v2.1.0):
✅ self          — hp, ep, atk, def, inventory, equippedWeapon, isAlive
✅ currentRegion — id, name, terrain, weather, connections, interactables, isDeathZone
✅ connectedRegions — full Region objects OR bare string IDs (type-safe via _resolve_region)
✅ visibleRegions  — used for connectedRegions fallback + region ID lookup
✅ visibleAgents   — guardians (HOSTILE!) + enemies + combat targeting (FIXED range check)
✅ visibleMonsters — monster farming targets
✅ visibleNPCs     — acknowledged (NPCs are flavor per game-systems.md)
✅ visibleItems    — pickup + movement attraction scoring
✅ pendingDeathzones — {id, name} entries for death zone escape + movement planning
✅ recentLogs      — available for analysis
✅ recentMessages  — communication (curse disabled in v1.5.2)
✅ aliveCount      — phase-based strategy (early/mid/late game)
"""
