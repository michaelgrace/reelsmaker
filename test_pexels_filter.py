# run from root while reelsmaker is running
# docker exec -it reelsmaker python test_pexels_filter.py

import asyncio
import os
import datetime
from dotenv import load_dotenv
from loguru import logger
from app.pexel import search_for_stock_videos, get_negative_keywords, filter_negative_content, inspect_video_metadata

# Configure logger
logger.remove()
logger.add(lambda msg: print(msg), level="INFO")
log_file = f"filter_test_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logger.add(log_file, level="INFO")

# Load environment variables
load_dotenv()

async def test_filtering():
    """Test the negative content filtering functionality."""
    # 1. Test keyword loading
    keywords = get_negative_keywords()
    logger.info(f"Loaded {len(keywords)} negative keywords: {keywords[:5]}...")
    
    # 2. Test with neutral query
    logger.info("\n--- Testing neutral query ---")
    neutral_videos = await search_for_stock_videos(
        limit=3, 
        query="happy children playing",
        orientation="landscape"
    )
    logger.info(f"Found {len(neutral_videos)} videos for neutral query")
    
    # 3. Test with potentially negative query
    logger.info("\n--- Testing potentially negative query ---")
    negative_videos = await search_for_stock_videos(
        limit=3, 
        query="argument",  # This should trigger filtering
        orientation="landscape"
    )
    logger.info(f"Found {len(negative_videos)} videos for negative query")
    await inspect_video_metadata("argument")
    
    # 4. Test with mixed query
    logger.info("\n--- Testing mixed query ---")
    mixed_videos = await search_for_stock_videos(
        limit=3, 
        query="serious discussion",  # May contain some negative content
        orientation="landscape"
    )
    logger.info(f"Found {len(mixed_videos)} videos for mixed query")

if __name__ == "__main__":
    asyncio.run(test_filtering())