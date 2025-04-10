import csv
import os
import time
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from loguru import logger

class MetricsLogger:
    """CSV logger for tracking video generation metrics and performance in a single consolidated file."""
    
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
            
            # Use fixed filename instead of timestamped one
            self.log_file = log_dir / "video_metrics.csv"
            
            # Define all possible columns for the CSV
            columns = [
                # Generation context
                'timestamp', 'prompt', 'script_length', 'sentence_count',
                
                # Voice metrics                
                'voice_provider', 'voice_name', 
                
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
            
            # Check if file exists to determine if we need headers
            file_exists = os.path.exists(self.log_file)
            
            # Create CSV file and write header if necessary
            self.csv_file = open(self.log_file, 'a', newline='', encoding='utf-8')
            self.writer = csv.DictWriter(self.csv_file, fieldnames=columns)
            if not file_exists:
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
    
    def add_video_info(self, video_data):
        """Add video metadata to the current metrics entry."""
        # Store the primary video info in the metrics dictionary
        if video_data:
            # Add basic video metadata to metrics
            self.add_metric('video_url_path', video_data.get('url', ''))
            self.add_metric('video_duration', video_data.get('duration', 0))
            self.add_metric('video_id', video_data.get('id', ''))
            self.add_metric('video_width', video_data.get('width', 0))
            self.add_metric('video_height', video_data.get('height', 0))
            
            # Add orientation based on dimensions
            if 'width' in video_data and 'height' in video_data:
                width = video_data.get('width', 0)
                height = video_data.get('height', 0)
                if width and height:
                    ratio = width / height
                    if ratio > 1.2:
                        orientation = "landscape"
                    elif ratio < 0.8:
                        orientation = "portrait"
                    else:
                        orientation = "square"
                    self.add_metric('orientation', orientation)
    
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



