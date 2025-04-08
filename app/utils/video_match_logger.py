import csv
import os
import datetime
from pathlib import Path
from loguru import logger

class VideoMatchLogger:
    """Simple CSV logger for tracking sentence-to-video matches."""
    
    def __init__(self, cwd: str = None, enabled: bool = True):
        self.enabled = enabled
        self.log_file = None
        self.csv_file = None
        self.writer = None
        
        if not enabled:
            logger.debug("VideoMatchLogger disabled during initialization")
            return
        
        # Default to tmp directory if none provided
        if cwd is None:
            cwd = os.path.join(os.getcwd(), "tmp")
        
        try:
            # Create logs directory
            log_dir = Path(cwd) / "logs"
            log_dir.mkdir(exist_ok=True)
            
            logger.debug(f"Using VideoMatchLogger directory: {log_dir.absolute()}")
            
            # Create timestamped CSV file
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_file = log_dir / f"video_matches_{timestamp}.csv"
            
            # Create CSV file and write header
            self.csv_file = open(self.log_file, 'w', newline='', encoding='utf-8')
            self.writer = csv.writer(self.csv_file)
            self.writer.writerow([
                'timestamp', 
                'sentence', 
                'search_query',
                'video_url',
                'voice_provider',
                'voice_name'
            ])
            
            logger.info(f"Video match logging enabled: {self.log_file}")
        except Exception as e:
            logger.error(f"Failed to initialize VideoMatchLogger: {e}", exc_info=True)
            self.enabled = False
    
    def log_match(self, sentence: str, search_query: str, video_url: str, 
                  voice_provider: str = "", voice_name: str = ""):
        """Log a sentence-to-video match."""
        if not self.enabled or not self.writer:
            return
            
        try:
            # Add timestamp
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Write to CSV
            self.writer.writerow([
                timestamp,
                sentence,
                search_query,
                video_url,
                voice_provider,
                voice_name
            ])
            self.csv_file.flush()  # Ensure data is written immediately
        except Exception as e:
            logger.error(f"Failed to log video match: {e}")
    
    def close(self):
        """Close the CSV file."""
        if self.csv_file and not self.csv_file.closed:
            try:
                self.csv_file.close()
                logger.info(f"Closed video match log file: {self.log_file}")
            except Exception as e:
                logger.error(f"Error closing video match file: {e}")