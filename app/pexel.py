import os
import json
from loguru import logger
import requests

# Add this at the module level to cache keywords
_negative_keywords_cache = None

def get_negative_keywords():
    """Load negative keywords from JSON file with caching."""
    global _negative_keywords_cache
    
    # Return cached keywords if available
    if _negative_keywords_cache is not None:
        return _negative_keywords_cache
    
    try:
        # Build path to negative_keywords.json
        keywords_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                    "data", "negative_keywords.json")
        
        if os.path.exists(keywords_path):
            logger.info(f"Loading negative keywords from {keywords_path}")
            with open(keywords_path, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "negative_keywords" in data:
                    _negative_keywords_cache = data["negative_keywords"]
                    return _negative_keywords_cache
                logger.warning("Invalid format in negative_keywords.json")
        else:
            logger.warning(f"Negative keywords file not found at {keywords_path}")
            
    except Exception as e:
        logger.error(f"Error loading negative keywords: {e}")
    
    # Fallback to default keywords if something goes wrong
    _negative_keywords_cache = [
        "argument", "fight", "prison", "jail", "depression", 
        "darkness", "occult", "violence", "conflict", "suffering"
    ]
    return _negative_keywords_cache

def filter_negative_content(items, query=None, metrics_logger=None):
    """Filter out content that contains negative keywords."""
    keywords = get_negative_keywords()
    filtered_results = []
    rejected_count = 0
    rejected_details = []
    
    for item in items:
        # Extract metadata as before
        description = item.get("description", "").lower()
        
        # Handle tags
        if "tags" in item and isinstance(item["tags"], list):
            tags = " ".join(item["tags"]).lower()
        else:
            tags = ""
            
        # Also check the video title/alt
        title = item.get("alt", item.get("title", "")).lower()
        
        # Add URL checking - critical for Pexels content
        url = item.get("url", "").lower()
        # Extract the descriptive part after /video/
        if "/video/" in url:
            url_path = url.split("/video/")[1].rsplit("/", 1)[0]
        else:
            url_path = ""
        
        # Combine all text for analysis
        combined_text = f"{description} {tags} {title} {url_path}"
        
        # Check if any negative keyword appears
        matching_keywords = [kw for kw in keywords if kw in combined_text]
        
        if not matching_keywords:
            filtered_results.append(item)
        else:
            rejected_count += 1
            # Store details about the rejected item
            rejected_details.append({
                "title": title or "Unknown",
                "url": url,
                "matching_keywords": matching_keywords,
                "description": description[:100] + "..." if len(description) > 100 else description,
                "id": item.get("id", "unknown")
            })
    
    # Log summary and details
    logger.info(f"Content filter: {rejected_count} items rejected, {len(filtered_results)} items passed")
    
    if rejected_details:
        logger.info("Rejected content details:")
        for i, details in enumerate(rejected_details, 1):
            logger.info(f"  {i}. ID: {details['id']} - URL: {details['url']}")
            logger.info(f"     Matched keywords: {', '.join(details['matching_keywords'])}")
    
    if metrics_logger:
        metrics_logger.add_metric('videos_rejected', rejected_count)
        if rejected_details:
            # Record which keywords caused rejections
            rejection_keywords = set()
            for details in rejected_details:
                rejection_keywords.update(details['matching_keywords'])
            metrics_logger.add_metric('rejection_keywords', ', '.join(rejection_keywords))
    
    return filtered_results

async def search_for_stock_videos(
    limit: int = 5, 
    min_dur: int = 10, 
    query: str = "nature",
    orientation: str = None,
    metrics_logger=None) -> list[str]:
    """
    Search for stock videos on Pexels with orientation and content filtering.
    """
    # Get API key
    PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
    
    if not PEXELS_API_KEY:
        logger.error("PEXELS_API_KEY not found in environment variables")
        return []
    
    headers = {"Authorization": PEXELS_API_KEY}
    qurl = f"https://api.pexels.com/videos/search"
    
    # Add orientation to query parameters
    params = {
        "query": query,
        "per_page": limit * 2,  # Request more to ensure we have enough after filtering
        "min_duration": min_dur
    }
    
    # Add orientation parameter if specified
    if orientation:
        params["orientation"] = orientation
        logger.info(f"Searching for {orientation} videos matching '{query}'")
    
    r = requests.get(qurl, headers=headers, params=params)
    response = r.json()
    
    if not response.get("videos"):
        logger.warning(f"No videos found for query '{query}' with orientation '{orientation}'")
        return []
    
    # Additional filtering to ensure correct aspect ratio
    video_urls = []
    
    try:
        videos_data = response.get("videos", [])
        filtered_videos = filter_negative_content(videos_data, query=query, metrics_logger=metrics_logger)
        
        for video in filtered_videos:
            if video["duration"] < min_dur:
                continue
                
            # Get the highest quality video URL
            raw_urls = video["video_files"]
            best_video = None
            max_resolution = 0
            
            for v in raw_urls:
                if ".com/video-files" not in v["link"]:
                    continue
                    
                # Get dimensions and verify orientation
                width, height = v["width"], v["height"]
                video_orientation = get_orientation(width, height)
                
                # Skip if orientation doesn't match requested orientation
                if orientation and video_orientation != orientation:
                    continue
                    
                resolution = width * height
                if resolution > max_resolution:
                    max_resolution = resolution
                    best_video = v["link"]
            
            if best_video:
                video_urls.append(best_video)
                
            # Break if we have enough videos
            if len(video_urls) >= limit:
                break
                
    except Exception as e:
        logger.error(f"Error processing videos: {e}")
    
    return video_urls

def get_orientation(width, height):
    """Determine video orientation based on dimensions"""
    ratio = width / height
    if ratio > 1.2:  # Wider than tall
        return "landscape"
    elif ratio < 0.8:  # Taller than wide
        return "portrait"
    else:
        return "square"  # Close to square

async def inspect_video_metadata(query="argument", orientation="landscape"):
    """Debug function to inspect raw metadata from Pexels videos."""
    PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
    
    if not PEXELS_API_KEY:
        logger.error("PEXELS_API_KEY not found in environment variables")
        return
    
    headers = {"Authorization": PEXELS_API_KEY}
    qurl = "https://api.pexels.com/videos/search"
    
    params = {
        "query": query,
        "per_page": 3,
        "orientation": orientation
    }
    
    logger.info(f"Inspecting metadata for query: '{query}'")
    r = requests.get(qurl, headers=headers, params=params)
    videos = r.json().get("videos", [])
    
    if not videos:
        logger.info(f"No videos found for query '{query}'")
        return
    
    for i, video in enumerate(videos):
        logger.info(f"\nVideo {i+1}: ID={video.get('id')}")
        logger.info(f"  Title: {video.get('user', {}).get('name', '')} - {video.get('url', '').split('/')[-1]}")
        logger.info(f"  Description: {video.get('description', 'No description')}")
        logger.info(f"  Tags/Keywords: {video.get('tags', [])}")
        logger.info(f"  URL: {video.get('url', 'No URL')}")
