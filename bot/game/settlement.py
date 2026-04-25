"""
Game settlement — Phase 3: process game end, update memory, prepare for next game.
Enhanced with detailed lesson extraction for cross-game learning.
"""
from bot.memory.agent_memory import AgentMemory
from bot.dashboard.state import dashboard_state
from bot.utils.logger import get_logger

log = get_logger(__name__)


async def settle_game(game_result: dict, entry_type: str, memory: AgentMemory):
    """
    Process game end:
    1. Extract final stats
    2. Update memory (overall history + lessons)
    3. Clear temp memory
    """
    result = game_result.get("result", game_result)
    is_winner = result.get("isWinner", False)
    final_rank = result.get("finalRank", 0)
    kills = result.get("kills", 0)
    rewards = result.get("rewards", {})
    smoltz_earned = rewards.get("sMoltz", 0)
    moltz_earned = rewards.get("moltz", 0)
    death_cause = result.get("deathCause", "unknown")
    survived_turns = result.get("survivedTurns", 0)

    log.info("═══ GAME SETTLEMENT ═══")
    log.info("  Winner: %s | Rank: %d | Kills: %d", "YES" if is_winner else "No", final_rank, kills)
    log.info("  Rewards: %d sMoltz, %d Moltz", smoltz_earned, moltz_earned)
    log.info("  Survived: %d turns | Death: %s", survived_turns, death_cause)

    # Record to dashboard history
    dashboard_state.record_game({
        "is_winner": is_winner,
        "final_rank": final_rank,
        "kills": kills,
        "smoltz_earned": smoltz_earned,
        "moltz_earned": moltz_earned,
        "entry_type": entry_type,
        "death_cause": death_cause,
        "survived_turns": survived_turns,
    })

    # Update memory
    memory.record_game_end(
        is_winner=is_winner,
        final_rank=final_rank,
        kills=kills,
        smoltz_earned=smoltz_earned,
    )

    # Add detailed lessons based on game outcome
    if is_winner:
        memory.add_lesson(f"Won with {kills} kills at rank {final_rank} ({entry_type} room)")
    elif final_rank <= 3:
        memory.add_lesson(f"Top 3 finish (rank {final_rank}) with {kills} kills — close to win")
    elif final_rank <= 10:
        memory.add_lesson(f"Top 10 (rank {final_rank}, {kills} kills) — need better late-game play")

    # Death cause analysis
    if death_cause == "deathzone" or "death_zone" in str(death_cause).lower():
        memory.add_lesson("Died to death zone — need earlier pre-escape movement")
    elif death_cause == "guardian" or "guardian" in str(death_cause).lower():
        memory.add_lesson("Died to guardian — check HP before engaging guardians")
    elif death_cause == "agent" or "player" in str(death_cause).lower():
        memory.add_lesson("Died to agent combat — avoid fighting when disadvantaged")

    # Performance analysis
    if kills == 0 and survived_turns > 5:
        memory.add_lesson("Zero kills despite surviving — need more aggressive farming/combat")
    elif kills >= 5:
        memory.add_lesson(f"High kill game ({kills}) — aggressive strategy working well")

    if survived_turns < 3 and not is_winner:
        memory.add_lesson("Died very early — avoid combat in first few turns, focus on looting")

    # Clear temp for next game
    memory.clear_temp()
    await memory.save()

    log.info("Settlement complete. Ready for next game.")

