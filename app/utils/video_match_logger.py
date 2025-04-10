import os
import csv
import datetime
import json
from pathlib import Path
from loguru import logger

class VideoMatchLogger:
    """CSV logger for tracking sentence-to-video matches in a single consolidated file."""
    
    def __init__(self, enabled=True, log_dir=None):
        """Initialize the logger with configuration."""
        self.enabled = enabled
        
        if not enabled:
            logger.debug("Video match logger disabled")
            return
            
        # Set up log directory
        if log_dir is None:
            log_dir = os.path.join("tmp", "logs")
        
        # Ensure log directory exists
        os.makedirs(log_dir, exist_ok=True)
        self.log_dir = log_dir
        
        # Set up CSV file
        self.csv_file = None
        self.writer = None
        self._init_csv_file()
    
    def _init_csv_file(self):
        """Initialize or open the CSV file for logging."""
        try:
            csv_path = os.path.join(self.log_dir, "video_matches.csv")
            
            # Check if file exists to determine if we need to write headers
            file_exists = os.path.exists(csv_path)
            
            # Open in append mode
            self.csv_file = open(csv_path, "a", newline="", encoding="utf-8")
            self.writer = csv.writer(self.csv_file)
            
            # Write headers if this is a new file
            if not file_exists:
                self.writer.writerow([
                    "timestamp",
                    "sentence", 
                    "search_query", 
                    "video_url",
                    "human_readable_url",  # New field for descriptive URLs
                    "voice_provider", 
                    "voice_name",
                    "rejected",            # New field to track rejected videos
                    "rejection_keywords",  # New field for rejection reasons
                    "metadata"             # New field for full video metadata
                ])
                self.csv_file.flush()
                
            logger.info(f"Video match logger initialized with log file: {csv_path}")
            
        except Exception as e:
            logger.error(f"Failed to initialize video match log file: {e}")
            self.enabled = False
    
    def extract_descriptive_url(self, video_url, video_id=None):
        """Extract a human-readable descriptive URL from various formats."""
        if not video_url:
            return ""
            
        # For Pexels URLs with descriptive parts
        if "pexels.com/video/" in video_url:
            return video_url
            
        # For Video ID format
        if video_url.startswith("Video ") and "(" in video_url and ")" in video_url:
            try:
                video_id = video_url.split(" ")[1]
                return f"https://www.pexels.com/video/{video_id}"
            except:
                pass
                
        # For Pexels CDN URLs
        if "videos.pexels.com/video-files" in video_url:
            try:
                parts = video_url.split("/")
                video_id = parts[-2]
                return f"https://www.pexels.com/video/{video_id}"
            except:
                pass
                
        # For downloaded local files with ID in filename
        if "/tmp/narrator/" in video_url or "\\tmp\\narrator\\" in video_url:
            try:
                filename = os.path.basename(video_url)
                parts = filename.split("-")
                if len(parts) > 0:
                    video_id = parts[0]
                    return f"https://www.pexels.com/video/{video_id}"
            except:
                pass
                
        # Use provided video_id if available
        if video_id:
            return f"https://www.pexels.com/video/{video_id}"
            
        # Return original if we can't parse it
        return video_url
    
    def log_match(self, sentence, search_query, video_url, 
                 voice_provider="", voice_name="", 
                 rejected=False, rejection_keywords="", 
                 video_id=None, metadata=None):
        """Log a sentence-to-video match with enhanced details."""
        if not self.enabled or not self.writer:
            return
            
        try:
            # Add timestamp
            timestamp = datetime.datetime.now().strftime("%m/%d/%Y %H:%M")
            
            # Get human-readable URL
            human_readable_url = self.extract_descriptive_url(video_url, video_id)
            
            # Format metadata as JSON string if provided
            metadata_str = ""
            if metadata:
                try:
                    # Convert metadata to JSON string, handle circular references
                    metadata_str = json.dumps(metadata, default=str)
                except Exception as e:
                    logger.error(f"Failed to serialize metadata: {e}")
                    metadata_str = str(metadata)
            
            # Write to CSV
            self.writer.writerow([
                timestamp,
                sentence,
                search_query,
                video_url,
                human_readable_url,
                voice_provider,
                voice_name,
                "Yes" if rejected else "No",
                rejection_keywords,
                metadata_str
            ])
            self.csv_file.flush()  # Ensure data is written immediately
            
            log_msg = f"Video match: '{search_query}' -> {human_readable_url}"
            if rejected:
                log_msg = f"REJECTED {log_msg} (reason: {rejection_keywords})"
            logger.debug(log_msg)
            
        except Exception as e:
            logger.error(f"Failed to log video match: {e}")
    
    def close(self):
        """Close the CSV file."""
        if self.csv_file:
            self.csv_file.close()
            logger.info(f"Closed video match log file: {self.csv_file.name}")