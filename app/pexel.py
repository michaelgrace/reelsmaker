import os
import json
from loguru import logger
import requests
from app.utils.metrics_logger import MetricsLogger

# Add this at the module level to cache keywords
_negative_keywords_cache = None

# Initialize the logger at the module level
metrics_logger = MetricsLogger()

def get_negative_keywords():
    """Load negative keywords from JSON file with caching."""
    global _negative_keywords_cache
    
    # Return cached keywords if available
    if (_negative_keywords_cache is not None):
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
    """Filter out content that contains negative keywords in description, tags, or URL."""
    negative_keywords = get_negative_keywords()
    
    if not negative_keywords:
        logger.warning("No negative keywords loaded, skipping content filtering")
        return items, [], []
        
    filtered_items = []
    rejected_items = []  # Store rejected items for logging
    rejected_keywords = set()  # Track which keywords caused rejection
    
    for item in items:
        # Fields to check for negative content
        description = item.get("description", "").lower()
        tags = [tag.lower() for tag in item.get("tags", [])]
        url = item.get("url", "").lower()
        
        # Extract descriptive terms from URL for better logging
        url_descriptive_terms = []
        if url and "pexels.com/video/" in url:
            try:
                # Extract the descriptive part of the URL (e.g., "two-women-arguing")
                path_parts = url.split("/")
                if len(path_parts) > 5:  # Make sure URL has enough parts
                    description_part = path_parts[-2]  # Get the part before the ID
                    # Split by hyphens and filter out empty parts
                    terms = [term for term in description_part.split("-") if term and len(term) > 2]
                    url_descriptive_terms = terms
                    
                    # Store these terms with the item for later use
                    item["url_descriptive_terms"] = terms
                    
                    # Log extracted terms for debugging
                    if terms:
                        logger.debug(f"Extracted URL terms: {terms}")
            except Exception as e:
                logger.debug(f"Error extracting URL descriptive terms: {e}")
        
        # Default to including the item
        include = True
        matched_keyword = None
        
        # Check each negative keyword
        for keyword in negative_keywords:
            keyword_lower = keyword.lower()
            
            # Check description
            if keyword_lower in description:
                include = False
                rejected_keywords.add(keyword)
                matched_keyword = keyword
                logger.debug(f"Rejected due to keyword '{keyword}' in description")
                break
                
            # Check URL
            if keyword_lower in url:
                include = False
                rejected_keywords.add(keyword)
                matched_keyword = keyword
                logger.debug(f"Rejected due to keyword '{keyword}' in URL")
                break
                
            # Check URL descriptive terms specifically
            for term in url_descriptive_terms:
                if keyword_lower in term:
                    include = False
                    rejected_keywords.add(f"{keyword} (in URL: {term})")
                    matched_keyword = f"{keyword} (in URL: {term})"
                    logger.debug(f"Rejected due to keyword '{keyword}' in URL term '{term}'")
                    break
                    
            # Check tags
            if include:  # Only check if still including
                for tag in tags:
                    if keyword_lower in tag:
                        include = False
                        rejected_keywords.add(keyword)
                        matched_keyword = keyword
                        logger.debug(f"Rejected due to keyword '{keyword}' in tag '{tag}'")
                        break
                        
            if not include:
                break
        
        if include:
            filtered_items.append(item)
        else:
            # Store rejection reason with the item
            item["rejection_reason"] = matched_keyword
            item["rejected"] = True
            rejected_items.append(item)
    
    # Log the rejected keywords if metrics_logger is provided
    if metrics_logger and rejected_keywords:
        rejection_string = ', '.join(rejected_keywords)
        metrics_logger.add_metric('rejection_keywords', rejection_string)
    
    logger.info(f"Content filtering: {len(items)} items -> {len(filtered_items)} items remained after filtering")
    
    return filtered_items, rejected_items, list(rejected_keywords)  # Return filtered items, rejected items and keywords

async def search_for_stock_videos(
    limit: int = 5, 
    min_dur: int = 10, 
    query: str = "nature",
    orientation: str = None,
    metrics_logger=None,
    video_match_logger=None) -> list[str]:
    """
    Search for stock videos on Pexels with orientation and content filtering.
    """
    # Use the metrics logger if provided, otherwise use module-level
    _metrics_logger = metrics_logger if metrics_logger is not None else globals().get('metrics_logger')
    
    # Log the search query being used
    if _metrics_logger:
        _metrics_logger.add_metric('search_query', query)
        _metrics_logger.add_metric('videos_requested', limit)
    
    # Get the API key
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
    
    # Start timing video search
    if _metrics_logger:
        _metrics_logger.mark_start('video_search')
    
    # Request videos
    r = requests.get(qurl, headers=headers, params=params)
    response = r.json()
    
    if not response.get("videos"):
        logger.warning(f"No videos found for query '{query}' with orientation '{orientation}'")
        if _metrics_logger:
            _metrics_logger.add_metric('videos_found', 0)
            _metrics_logger.mark_end('video_search')
        return []
    
    # Get videos data from Pexels API
    videos_data = response.get("videos", [])
    rejected_count = 0
    rejection_keywords = []
    
    try:
        # This now returns filtered videos, rejected items, and rejection keywords
        filtered_videos, rejected_videos, rejected_words = filter_negative_content(
            videos_data, query, _metrics_logger)
        
        # Track how many videos were rejected by content filtering
        rejected_count = (len(videos_data) - len(filtered_videos))
        
        # Make sure all rejection information is logged
        if rejected_words:
            # Store rejection keywords
            rejection_keywords = rejected_words
            
            # Log them to the metrics
            if _metrics_logger:
                _metrics_logger.add_metric('rejection_keywords', ', '.join(rejected_words))
            
            # Log detailed rejection information
            logger.info(f"Rejected {rejected_count} videos for query '{query}': {rejected_words}")
            
            # Log rejected videos in video_match_logger if provided
            if video_match_logger:
                for rejected_video in rejected_videos:
                    # Get video properties
                    video_url = rejected_video.get("url", "")
                    video_id = str(rejected_video.get("id", ""))
                    rejection_reason = rejected_video.get("rejection_reason", "unknown")
                    
                    # Log the rejected video with full metadata
                    video_match_logger.log_match(
                        sentence=f"[REJECTED] Query: {query}",
                        search_query=query,
                        video_url=video_url,
                        video_id=video_id,
                        rejected=True,
                        rejection_keywords=rejection_reason,
                        metadata=rejected_video  # Include full metadata
                    )
    
    except Exception as e:
        logger.error(f"Error in content filtering: {e}")
        filtered_videos = videos_data
    
    # Process filtered videos to get best video URLs
    video_urls = []
    best_videos = []
    
    for video in filtered_videos:
        try:
            if video["duration"] < min_dur:
                rejected_count += 1
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
                # Log video metadata to metrics
                if _metrics_logger:
                    _metrics_logger.add_metric('video_url_path', best_video)
                    _metrics_logger.add_video_info({
                        'url': best_video, 
                        'duration': video.get('duration', 0),
                        'id': str(video.get('id', '')),
                        'width': v.get('width', 0),
                        'height': v.get('height', 0)
                    })
                
                # Also log this video with full metadata in video_match_logger
                if video_match_logger:
                    video_match_logger.log_match(
                        sentence=f"[ACCEPTED] Query: {query}",
                        search_query=query,
                        video_url=best_video,
                        video_id=str(video.get('id', '')),
                        rejected=False,
                        metadata=video  # Include full metadata
                    )
                
                video_urls.append(best_video)
                best_videos.append({
                    'url': best_video,
                    'metadata': video
                })
            
            else:
                rejected_count += 1
                
            # Break if we have enough videos
            if len(video_urls) >= limit:
                break
                
        except Exception as e:
            logger.error(f"Error processing video: {e}")
    
    # Finish timing and log results
    if _metrics_logger:
        _metrics_logger.mark_end('video_search')
        _metrics_logger.add_metric('videos_found', len(video_urls))
        _metrics_logger.add_metric('videos_rejected', rejected_count)
        
    logger.info(f"Found {len(video_urls)} videos for query '{query}'")
    return best_videos if best_videos else []  # Return videos with metadata

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
        url = video.get("url", "No URL")
        logger.info(f"\nVideo {i+1}: ID={video.get('id')}")
        logger.info(f"  Title: {video.get('user', {}).get('name', '')} - {url.split('/')[-1]}")
        logger.info(f"  Description: {video.get('description', 'No description')}")
        logger.info(f"  Tags/Keywords: {video.get('tags', [])}")
        logger.info(f"  URL: {url}")
        
        # Extract descriptive URL parts
        if url and "pexels.com/video/" in url:
            try:
                path_parts = url.split("/")
                if len(path_parts) > 5:
                    description_part = path_parts[-2]
                    terms = [term for term in description_part.split("-") if term]
                    logger.info(f"  URL descriptive terms: {terms}")
            except Exception as e:
                logger.debug(f"Error extracting URL descriptive terms: {e}")
