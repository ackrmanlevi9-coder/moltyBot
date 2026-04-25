"""
Molty Royale AI Agent — Entry Point v2.0.
Run: python -m bot.main
Dashboard + Bot run concurrently.
"""
import asyncio
import os
import sys
from bot.heartbeat import Heartbeat
from bot.dashboard.server import start_dashboard
from bot.utils.logger import get_logger

log = get_logger(__name__)

# Railway injects PORT env var; fallback to DASHBOARD_PORT or 8080
DASHBOARD_PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")))


def main():
    """Entry point for the bot."""
    log.info("Molty Royale AI Agent v2.0.0")
    log.info("Press Ctrl+C to stop")

    async def run_all():
        # Start dashboard server (non-blocking)
        await start_dashboard(port=DASHBOARD_PORT)
        
        num_agents = int(os.getenv("NUM_AGENTS", "10"))
        log.info(f"Starting {num_agents} bots side-by-side...")
        
        tasks = []
        for i in range(1, num_agents + 1):
            bot_id = f"bot{i}"
            hb = Heartbeat(bot_id=bot_id)
            tasks.append(asyncio.create_task(hb.run()))
            
        # Run heartbeat (main bot loop — runs forever)
        await asyncio.gather(*tasks)

    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_all())
    except KeyboardInterrupt:
        log.info("Shutdown complete.")


if __name__ == "__main__":
    main()
