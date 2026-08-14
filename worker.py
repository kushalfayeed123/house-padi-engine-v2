import asyncio
import logging
import sys

from app.services.property_ai_service import PropertyAIService

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("background_worker")

async def main():
    logger.info("Starting HousePadi Background AI Enrichment Worker...")
    ai_service = PropertyAIService()
    await ai_service.run_enrichment_sweep()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Background worker stopped by user.")