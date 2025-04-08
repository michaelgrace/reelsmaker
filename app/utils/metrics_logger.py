import csv
import os
import time
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from loguru import logger

class VideoMatchLogger:
    """Simple CSV logger for tracking sentence-to-video matches."""
    
    def __init__(self, cwd: str = None, enabled: bool = True):
        self.enabled = enabled
        self.log_file = None
        self.csv_file = None
        self.writer = None  # Add explicit writer initialization
        
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
        if not self.enabled or not self.csv_file:
            return
            
        try:
            self.writer.writerow([
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                sentence[:100],  # Limit sentence length
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
                logger.error(f"Error closing log file: {e}")

class MetricsLogger:
    """CSV logger for tracking video generation metrics and performance."""
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.log_file = None
        self.writer = None
        self.csv_file = None
        self.start_time = time.time()
        self.metrics = {}
        
        # Technical metrics - timestamps for calculating durations
        self._timestamps = {}
        
    def initialize(self, cwd: str = None):
        """Set up the CSV log file."""
        if not self.enabled:
            logger.debug("Metrics logging is disabled")
            return
        
        try:
            # If no cwd provided, default to tmp directory
            if cwd is None:
                # Use the tmp directory by default
                cwd = os.path.join(os.getcwd(), "tmp")
            
            # Create logs directory if it doesn't exist
            log_dir = Path(cwd) / "logs"
            log_dir.mkdir(exist_ok=True)
            
            logger.debug(f"Using log directory: {log_dir.absolute()}")
            
            # Create timestamped CSV file
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_file = log_dir / f"video_metrics_{timestamp}.csv"
            
            # Define all possible columns for the CSV
            columns = [
                # Generation context
                'timestamp', 'prompt', 'script_length', 'sentence_count',
                
                # Voice metrics
                'voice_provider', 'voice_name', 'sentence_pause_ms',
                
                # Video search
                'search_query', 'videos_requested', 'videos_found', 'videos_rejected',
                'rejection_keywords',
                
                # Video details
                'video_url_path', 'video_duration', 'video_id', 'video_width', 
                'video_height', 'orientation', 'final_resolution', 'final_filesize_mb',
                
                # Performance metrics
                'script_generation_time_s', 'video_search_time_s', 
                'audio_generation_time_s', 'total_generation_time_s',
                'errors_encountered', 'error_types', 'retries_needed',
                'sentence_word_count', 'fallback_used'
            ]
            
            # Create CSV file and write header
            self.csv_file = open(self.log_file, 'w', newline='', encoding='utf-8')
            self.writer = csv.DictWriter(self.csv_file, fieldnames=columns)
            self.writer.writeheader()
            
            logger.info(f"CSV metrics logging enabled: {self.log_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize CSV logging: {e}", exc_info=True)
            self.enabled = False
            return False
    
    def mark_start(self, name: str):
        """Mark the start time of an operation for duration tracking."""
        if not self.enabled:
            return
        self._timestamps[f"{name}_start"] = time.time()
    
    def mark_end(self, name: str):
        """Mark the end time of an operation and calculate duration."""
        if not self.enabled:
            return
            
        if f"{name}_start" in self._timestamps:
            duration = time.time() - self._timestamps[f"{name}_start"]
            self.metrics[f"{name}_time_s"] = round(duration, 2)
            return duration
        return None
    
    def add_metric(self, name: str, value: Any):
        """Add a metric to be logged."""
        if not self.enabled:
            return
        self.metrics[name] = value
    
    def add_video_info(self, video_data: Dict[str, Any]):
        """Add video-specific information."""
        if not self.enabled:
            return
            
        # Extract URL path
        video_url = video_data.get('url', '')
        if '/video/' in video_url:
            self.metrics['video_url_path'] = video_url.split('/video/')[1]
        
        # Add other video data
        for key in ['duration', 'id', 'width', 'height']:
            if key in video_data:
                self.metrics[f'video_{key}'] = video_data[key]
        
        # Calculate orientation
        if 'width' in video_data and 'height' in video_data:
            width = video_data['width']
            height = video_data['height']
            if width and height:
                ratio = width / height
                if ratio > 1.2:
                    self.metrics['orientation'] = 'landscape'
                elif ratio < 0.8:
                    self.metrics['orientation'] = 'portrait'
                else:
                    self.metrics['orientation'] = 'square'
    
    def add_error(self, error_type: str):
        """Track an error that occurred during processing."""
        if not self.enabled:
            return
            
        if 'errors_encountered' not in self.metrics:
            self.metrics['errors_encountered'] = 1
        else:
            self.metrics['errors_encountered'] += 1
            
        if 'error_types' not in self.metrics:
            self.metrics['error_types'] = error_type
        else:
            self.metrics['error_types'] += f", {error_type}"
    
    def add_retry(self):
        """Increment the retry counter."""
        if not self.enabled:
            return
            
        if 'retries_needed' not in self.metrics:
            self.metrics['retries_needed'] = 1
        else:
            self.metrics['retries_needed'] += 1
    
    def log_entry(self):
        """Write the current metrics to the CSV file."""
        if not self.enabled:
            logger.debug("Metrics logging disabled, not writing entry")
            return
        
        if not self.writer:
            logger.warning("CSV writer not initialized, cannot log metrics")
            return
            
        try:
            # Add timestamp
            self.metrics['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Calculate total generation time if not already set
            if 'total_generation_time_s' not in self.metrics:
                self.metrics['total_generation_time_s'] = round(time.time() - self.start_time, 2)
            
            # Clone the metrics to avoid modifying the dictionary during iteration
            metrics_to_write = self.metrics.copy()
            
            # Write row
            self.writer.writerow(metrics_to_write)
            self.csv_file.flush()  # Ensure data is written immediately
        except Exception as e:
            logger.error(f"Failed to write metrics to CSV: {e}", exc_info=True)
    
    def close(self):
        """Close the CSV file."""
        if self.csv_file and not self.csv_file.closed:
            try:
                self.csv_file.close()
                logger.info(f"Closed metrics log file: {self.log_file}")
            except Exception as e:
                logger.error(f"Error closing metrics file: {e}")



