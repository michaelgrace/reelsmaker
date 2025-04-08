import os
import sys
import subprocess
import asyncio

import ffmpeg
from loguru import logger

# Make PyTorch optional
TORCH_AVAILABLE = False
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    # PyTorch not available, but we can still proceed
    pass

from app.base import (
    BaseEngine,
    BaseGeneratorConfig,
    FileClip,
    StartResponse,
    TempData,
)
from app.utils.strings import split_by_dot_or_newline
from app.utils.path_util import download_resource
from app.utils.metrics_logger import MetricsLogger
from app.utils.metrics_logger import VideoMatchLogger
# from app.utils.video_match_logger import VideoMatchLogger


class ReelsMakerConfig(BaseGeneratorConfig):
    max_videos: int = 3  # Add this field with default value


def create_concat_file(clips):
    concat_filename = "concat_list.txt"
    with open(concat_filename, "w") as f:
        for clip in clips:
            f.write(f"file '{clip}'\n")
    return concat_filename


def concatenate_with_filelist(clips, output_path):
    concat_filename = create_concat_file(clips)

    # Run FFmpeg with the concat demuxer
    ffmpeg.input(concat_filename, format="concat", safe=0).output(
        output_path, c="copy"
    ).run(overwrite_output=True)

    return output_path


def concatenate_clips(clips, output_path):
    """
    Concatenates a list of video clips.
    Args:
    - clips (list of str): List of file paths to each video clip to concatenate.
    - output_path (str): Path to save the final concatenated video.
    """
    # Prepare input streams for each clip
    streams = [ffmpeg.input(clip) for clip in clips]

    # Use concat filter
    concatenated_stream = ffmpeg.concat(*streams, v=1, a=1).output(output_path)

    # Run FFmpeg
    concatenated_stream.run(overwrite_output=True)
    return output_path


class ReelsMaker(BaseEngine):
    def __init__(self, config: ReelsMakerConfig):
        super().__init__(config)
        self.config = config
        
        # Initialize both loggers
        self.metrics_logger = MetricsLogger(enabled=True)  # Always enable for debugging
        self.metrics_logger.initialize()
        
        # Add the VideoMatchLogger initialization
        self.match_logger = VideoMatchLogger(enabled=True)  # Always enable for debugging
        
        logger.debug(f"Loggers initialized - metrics: {self.metrics_logger.enabled}, match: {self.match_logger.enabled}")
        
        # Ensure voice provider is properly set in synth_generator config
        if hasattr(self, 'synth_generator') and hasattr(self.synth_generator, 'config'):
            # Make sure voice_provider is explicitly set from config
            if hasattr(self.config, 'voice_provider') and self.config.voice_provider:
                self.synth_generator.config.voice_provider = self.config.voice_provider
                logger.info(f"Setting voice provider to: {self.config.voice_provider}")

        # Just log that we're using CPU
        logger.info("Using CPU for processing (no CUDA)")

    async def _generate_script_internal(self, prompt: str) -> str:
        """Internal method to generate a script from the prompt."""
        try:
            # Use the correct method from prompt_generator
            # This looks at how the original code was generating scripts
            if hasattr(self.prompt_generator, 'generate_content'):
                response = await self.prompt_generator.generate_content(prompt)
                return response.content
            elif hasattr(self.prompt_generator, 'generate'):
                response = await self.prompt_generator.generate(prompt)
                return response
            elif hasattr(self.prompt_generator, 'generate_sentences'):
                response = await self.prompt_generator.generate_sentences(prompt, max_sentences=5)
                return " ".join(response)
            else:
                # Fall back to a simple approach if none of the expected methods exist
                logger.warning("Could not find appropriate method in prompt_generator")
                
                # this is the source of the duplication of prompt as entered in motivational quote
                # return f"Let's explore {prompt}. {prompt} is something that affects us all."
                return f"{prompt}"
        except Exception as e:
            logger.exception(f"Internal script generation failed: {e}")
            return ""

    async def generate_script(self, prompt: str) -> str:
        """Generate a script from a prompt."""
        try:
            # Log the prompt and API request
            logger.info(f"Generating script from prompt: {prompt}")
            
            # Add timeout to prevent hanging
            script = await asyncio.wait_for(
                self._generate_script_internal(prompt),
                timeout=30  # 30 second timeout
            )
            
            # Validate script content
            if not script or script.strip() == "":
                logger.error(f"Generated empty script from prompt: {prompt}")
                # Fallback without repeating the prompt
                return f"Here's an interesting thought to consider. Every journey begins with a single step."
                
            logger.info(f"Successfully generated script: {script[:50]}...")
            return script
            
        except asyncio.TimeoutError:
            logger.error(f"Script generation timed out for prompt: {prompt}")
            return f"Life is full of wonderful opportunities. Make the most of each moment."
        except Exception as e:
            logger.exception(f"Script generation failed: {e}")
            return f"The path to success is through persistence and determination."

    async def generate_search_terms(self, script, max_hashtags: int = 5):
        logger.debug("Generating search terms for script...")
        response = await self.prompt_generator.generate_stock_image_keywords(script)
        tags = [tag.replace("#", "") for tag in response.sentences]
        if len(tags) > max_hashtags:
            logger.warning(f"Truncated search terms to {max_hashtags} tags")
            tags = tags[:max_hashtags]

        logger.info(f"Generated search terms: {tags}")
        return tags

    async def start(self, st_state=None) -> StartResponse:
        # At the beginning of the method
        self.st_state = st_state  # Store the session state
        
        # START LOGGING - Add this line
        self.metrics_logger.mark_start('total_generation')
        
        # Add this check before major processing steps
        if self.st_state and self.st_state.get("cancel_requested", False):
            logger.info("Cancellation requested, aborting video generation")
            await self.cleanup_temp_files()
            return None  # Return None to indicate cancellation

        try:
            await super().start()
            
            # Add periodic cancellation checks
            if st_state and self.check_cancellation(st_state):
                raise Exception("Processing cancelled by user")

            # Initialize background_music_path to None
            self.background_music_path = None

            # Priority order: config.video_gen_config.background_music_path first, then background_audio_url
            if self.config.video_gen_config and self.config.video_gen_config.background_music_path:
                self.background_music_path = self.config.video_gen_config.background_music_path
                logger.info(f"Using background music from video_gen_config: {self.background_music_path}")
            elif self.config.background_audio_url:
                try:
                    self.background_music_path = await download_resource(
                        self.cwd, self.config.background_audio_url
                    )
                    logger.info(f"Downloaded background music from URL: {self.background_music_path}")
                except Exception as e:
                    logger.error(f"Failed to download background music: {e}")
                    self.background_music_path = None

            # Set the path in video_generator config
            if hasattr(self, 'video_generator') and hasattr(self.video_generator, 'config'):
                self.video_generator.config.background_music_path = self.background_music_path
            
            # Log the actual background music path (for debugging)
            logger.debug(f"Using background music path: {self.background_music_path}")
            
            # Before script generation - start timing
            self.metrics_logger.mark_start('script_generation')
            
            # generate script from prompt
            try:
                if self.config.prompt:
                    logger.debug(f"Generating script from prompt: {self.config.prompt}")
                    script = await self.generate_script(self.config.prompt)
                    # ADD THIS LINE - log script metrics
                    self.metrics_logger.mark_end('script_generation')
                    self.metrics_logger.add_metric('prompt', self.config.prompt)
                    self.metrics_logger.add_metric('script_length', len(script))
                    
                    logger.debug(f"Generated script: {script}")
                elif self.config.script:
                    script = self.config.script
                else:
                    raise ValueError("No prompt or sentence provided")

                # split script into sentences
                if script is None:
                    raise ValueError("Script generation failed - returned None")
                    
                sentences = split_by_dot_or_newline(script, 100)
                sentences = list(filter(lambda x: x != "", sentences))
                
                # ADD THIS LINE - log sentence count
                self.metrics_logger.add_metric('sentence_count', len(sentences))
                
            except Exception as e:
                logger.exception(f"Script generation or processing failed: {e}")
                raise

            # At the start of video search - mark time
            self.metrics_logger.mark_start('video_search')
            
            video_paths = []
            if self.config.video_paths:
                logger.info("Using video paths from client...")
                video_paths = self.config.video_paths
            else:
                logger.debug("Generating search terms for script...")
                search_terms = await self.generate_search_terms(
                    script=script, max_hashtags=10
                )

                # holds all remote urls
                remote_urls = []

                max_videos = self.config.max_videos if hasattr(self.config, 'max_videos') else int(os.getenv("MAX_BG_VIDEOS", 3))

                for search_term in search_terms[:max_videos]:
                    # search for a related background video
                    video_path = await self.video_generator.get_video_url(
                        search_term=search_term
                    )
                    if not video_path:
                        continue

                    remote_urls.append(video_path)

                # download all remote videos at once
                tasks = []
                for url in remote_urls:
                    # Add timeout to prevent hanging on slow downloads
                    task = asyncio.create_task(
                        asyncio.wait_for(
                            download_resource(self.cwd, url),
                            timeout=60  # 60 second timeout for downloads
                        )
                    )
                    tasks.append(task)

                try:
                    local_paths = await asyncio.gather(*tasks, return_exceptions=True)
                    # Filter out exceptions and keep only successful downloads
                    local_paths = [path for path in local_paths if not isinstance(path, Exception)]
                    video_paths.extend(set(local_paths))
                except Exception as e:
                    logger.error(f"Error downloading videos: {e}")

            if not video_paths:
                logger.warning("No video paths found, attempting to use default videos")
                
                # Try to load default videos from the assets directory
                default_videos_dir = os.path.join(os.getcwd(), "assets", "default_videos")
                if os.path.exists(default_videos_dir):
                    default_videos = [os.path.join(default_videos_dir, f) for f in os.listdir(default_videos_dir) 
                                      if f.endswith(('.mp4', '.mov', '.avi'))]
                    if default_videos:
                        logger.info(f"Using {len(default_videos)} default videos")
                        video_paths = default_videos
            
                # If no default videos found, create a blank video as last resort
                if not video_paths:
                    logger.warning("No default videos found, creating blank video")
                    blank_video = os.path.join(self.cwd, "blank_video.mp4")
                    try:
                        # Create a 15-second blank video with ffmpeg
                        black_cmd = [
                            self.video_generator.ffmpeg_cmd,
                            "-f", "lavfi", 
                            "-i", "color=c=black:s=1080x1920:d=15", 
                            "-c:v", "libx264",
                            "-pix_fmt", "yuv420p",
                            "-t", "15",
                            blank_video
                        ]
                        subprocess.run(black_cmd, check=True)
                        video_paths = [blank_video]
                    except Exception as e:
                        logger.exception(f"Failed to create blank video: {e}")
                        raise ValueError("Unable to create video: no source videos available")

            # After video search completes - add these lines
            self.metrics_logger.mark_end('video_search')
            max_videos = self.config.max_videos if hasattr(self.config, 'max_videos') else int(os.getenv("MAX_BG_VIDEOS", 3))
            self.metrics_logger.add_metric('videos_requested', max_videos)
            self.metrics_logger.add_metric('videos_found', len(video_paths))
            
            # Before audio generation - mark time
            self.metrics_logger.mark_start('audio_generation')
            
            data: list[TempData] = []

            # for each sentence, generate audio
            for sentence in sentences:
                audio_path = await self.synth_generator.synth_speech(sentence)
                data.append(
                    TempData(
                        synth_clip=FileClip(audio_path),
                    )
                )

                # After a video is selected for a sentence:
                if hasattr(self, 'match_logger') and self.match_logger.enabled:
                    self.match_logger.log_match(
                        sentence=sentence,
                        search_query=sentence,
                        video_url=video_paths[0] if video_paths else '',  # Use actual video path
                        voice_provider=self.config.synth_config.voice_provider,
                        voice_name=self.config.synth_config.voice
                    )

            # Filter out any None values from audio_clips
            audio_clips = [clip for clip in data if clip.synth_clip is not None]

            if not audio_clips:
                # Handle the case where all audio generation failed
                logger.error("All audio generation failed, cannot proceed with video creation")
                return StartResponse(success=False, error="Failed to generate any audio files")

            # Add before calling video_generator.generate_video
            if not self.background_music_path and hasattr(self, 'video_generator'):
                logger.warning("No background music path set at ReelsMaker level")
                # Let video_generator handle this with its own null check

            final_speech = ffmpeg.concat(
                *[item.synth_clip.ffmpeg_clip for item in audio_clips], v=0, a=1
            )

            try:
                # get subtitles from script
                subtitles_path = await self.subtitle_generator.generate_subtitles(
                    sentences=sentences,
                    durations=[item.synth_clip.real_duration for item in audio_clips],
                )
            except Exception as e:
                logger.error(f"Failed to generate subtitles: {e}")
                # Create a simple empty subtitles file as fallback
                subtitles_path = os.path.join(self.cwd, "fallback_subtitles.srt")
                with open(subtitles_path, "w") as f:
                    f.write("1\n00:00:00,000 --> 00:10:00,000\n\n")

            # Before calling video_generator.generate_video
            if not self.validate_subtitles_file(subtitles_path):
                logger.warning("Creating fallback subtitles file")
                subtitles_path = os.path.join(self.cwd, "fallback_subtitles.srt")
                with open(subtitles_path, "w") as f:
                    f.write("1\n00:00:00,000 --> 00:10:00,000\n\n")

            # the max duration of the final video
            video_duration = sum(item.synth_clip.real_duration for item in audio_clips)
            logger.info(f"Calculated video duration: {video_duration} seconds")

            # Add debug logging for audio files
            logger.debug(f"Calculated video duration: {video_duration}")
            logger.debug(f"Audio clips count: {len(audio_clips)}")
            for i, item in enumerate(audio_clips):
                if hasattr(item, 'synth_clip') and hasattr(item.synth_clip, 'filepath'):
                    audio_path = item.synth_clip.filepath
                    # Check if audio_path is None before using it
                    if audio_path is not None:
                        logger.debug(f"Audio clip {i} path: {audio_path}")
                        logger.debug(f"  - Exists: {os.path.exists(audio_path)}")
                        logger.debug(f"  - Size: {os.path.getsize(audio_path) if os.path.exists(audio_path) else 'N/A'}")
                    else:
                        logger.debug(f"Audio clip {i} path is None")
                    logger.debug(f"  - Duration: {item.synth_clip.real_duration}")

            # Set a minimum video duration
            MIN_VIDEO_DURATION = 15  # seconds
            if video_duration < MIN_VIDEO_DURATION:
                if video_duration <= 0:
                    logger.warning(f"Invalid video duration: {video_duration}, using minimum duration")
                    factor = 1.0  # Use a default factor of 1.0 (no scaling)
                else:
                    factor = MIN_VIDEO_DURATION / video_duration
                logger.info(f"Video too short ({video_duration}s), extending by factor {factor:.2f}")
                # We'll slow down each clip to meet the minimum duration
                video_duration = MIN_VIDEO_DURATION

            # each clip should be 5 seconds long
            max_clip_duration = 5

            tot_dur: float = 0

            temp_videoclip: list[FileClip] = [
                FileClip(video_path, t=max_clip_duration) for video_path in video_paths
            ]

            final_clips: list[FileClip] = []

            if not temp_videoclip or all(clip.real_duration <= 0 for clip in temp_videoclip):
                logger.warning("All video clips have zero duration, creating fallback clip")
                # Create a fallback clip or handle the error appropriately
                blank_video = os.path.join(self.cwd, "blank_video.mp4")
                try:
                    # Create a 15-second blank video with ffmpeg
                    black_cmd = [
                        self.video_generator.ffmpeg_cmd,
                        "-f", "lavfi", 
                        "-i", "color=c=black:s=1080x1920:d=15", 
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-t", "15",
                        blank_video
                    ]
                    subprocess.run(black_cmd, check=True)
                    temp_videoclip = [FileClip(blank_video, t=max_clip_duration)]
                except Exception as e:
                    logger.exception(f"Failed to create blank video: {e}")
                    raise ValueError("Unable to create video: no source videos available")

            # Add max iterations safety for the duration loop
            max_iterations = 1000  # Prevent infinite loop
            iteration_count = 0
            while tot_dur < video_duration and iteration_count < max_iterations:
                iteration_count += 1
                for clip in temp_videoclip:
                    remaining_dur = video_duration - tot_dur
                    subclip_duration = min(
                        max_clip_duration, remaining_dur, clip.real_duration
                    )
                    subclip = FileClip(clip.filepath, t=subclip_duration).duplicate()

                    final_clips.append(subclip)
                    tot_dur += subclip_duration

                    logger.debug(
                        f"Total duration after adding this clip: {tot_dur}, target is {video_duration}, clip duration: {subclip_duration}"
                    )

                    if tot_dur >= video_duration:
                        break

            if iteration_count >= max_iterations:
                logger.warning(f"Hit maximum iterations ({max_iterations}) when building video")

            # Throughout the method, add periodic checks:
            if self.st_state and self.st_state.get("cancel_requested", False):
                logger.info("Cancellation detected during processing")
                await self.cleanup_temp_files()
                return None

            if st_state and self.check_cancellation(st_state):
                raise Exception("Processing cancelled by user")
            
            # Add to video_gen.py:generate_video before running ffmpeg
            # Debug all inputs to identify which one is None
            for i, input_item in enumerate(final_clips):  # Adjust variable name as needed
                logger.debug(f"Input {i}: {input_item} (type: {type(input_item)})")
                if hasattr(input_item, 'filepath'):
                    logger.debug(f"  - filepath: {input_item.filepath}")
                elif isinstance(input_item, str):
                    logger.debug(f"  - filepath exists: {os.path.exists(input_item)}")

            final_video_path = await self.video_generator.generate_video(
                clips=final_clips,
                subtitles_path=subtitles_path,
                speech_filter=final_speech,
                video_duration=video_duration,
            )

            # Verify the output file exists and has content
            if os.path.exists(final_video_path) and os.path.getsize(final_video_path) > 0:
                # Add these lines
                self.metrics_logger.add_metric('final_filesize_mb', round(os.path.getsize(final_video_path) / (1024 * 1024), 2))
                
                # Close out the metrics and log
                self.metrics_logger.mark_end('total_generation')
                self.metrics_logger.log_entry()
                
                logger.info(f"Final video: {final_video_path}")
                logger.info("Video generated successfully!")
                # After generating the final video, clean up large objects:
                # Add this before returning the final response:
                final_clips.clear()  # Release memory
                if hasattr(self, 'cleanup_temp_files'):
                    self.cleanup_temp_files()
                return StartResponse(video_file_path=final_video_path)
            else:
                logger.error(f"Output file missing or empty: {final_video_path}")
                return None
            
        except Exception as e:
            # ADD THESE LINES - log error in metrics
            self.metrics_logger.add_error(str(type(e).__name__))
            self.metrics_logger.log_entry()
            
            # Enhanced error logging
            logger.exception(f"Video generation failed with error: {e}")
            return None
        finally:
            # ADD THIS LINE - ensure metrics file is closed
            self.metrics_logger.close()
            
            # Make sure to reset generation state if passed from UI
            if st_state:
                st_state["is_generating"] = False

    async def run_diagnostics(self):
        """Run system diagnostics to validate environment"""
        diagnostics = {
            "ffmpeg_version": None,
            "python_version": sys.version,
            "temp_dir_writable": None,
            "gpu_info": "Not using GPU acceleration"  # Simplify this
        }
        
        # Check FFmpeg
        try:
            result = subprocess.run([self.video_generator.ffmpeg_cmd, "-version"], 
                                    capture_output=True, text=True)
            if result.returncode == 0:
                diagnostics["ffmpeg_version"] = result.stdout.splitlines()[0]
            else:
                diagnostics["ffmpeg_version"] = f"Error: {result.stderr}"
        except Exception as e:
            diagnostics["ffmpeg_version"] = f"Exception: {str(e)}"
        
        # Test temp directory
        try:
            test_file = os.path.join(self.cwd, "test_write.txt")
            with open(test_file, "w") as f:
                f.write("Test")
            os.remove(test_file)
            diagnostics["temp_dir_writable"] = True
        except Exception as e:
            diagnostics["temp_dir_writable"] = f"Error: {str(e)}"
        
        # Report results
        logger.info(f"System diagnostics: {diagnostics}")
        return diagnostics

    def cleanup_temp_files(self):
        """Remove temporary files after successful video generation"""
        try:
            # Keep the final video but clean up intermediate files
            files_to_delete = [f for f in os.listdir(self.cwd) 
                              if not f.endswith("_final.mp4") and 
                              os.path.isfile(os.path.join(self.cwd, f))]
            for file in files_to_delete:
                os.remove(os.path.join(self.cwd, file))
            logger.info(f"Cleaned up {len(files_to_delete)} temporary files")
        except Exception as e:
            logger.warning(f"Error cleaning up temporary files: {e}")

    # Add this method to check subtitle file before using it
    def validate_subtitles_file(self, subtitles_path):
        """Ensure the subtitles file doesn't contain any potential issues."""
        if not os.path.exists(subtitles_path):
            return False
            
        try:
            # Check if the file has content
            if os.path.getsize(subtitles_path) == 0:
                logger.warning("Empty subtitles file detected")
                return False
                
            # Check content for potential issues
            with open(subtitles_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Basic validation - file should have timing markers
            if '-->' not in content:
                logger.warning("Invalid subtitles format: missing timing markers")
                return False
                
            # Test for potential encoding issues
            try:
                for line in content.splitlines():
                    line.encode('utf-8')  # Just to test if there are any encoding issues
            except UnicodeEncodeError:
                logger.warning("Subtitles file contains invalid UTF-8 characters")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Error validating subtitles file: {e}")
            return False

    def download_videos(self, prompt):
        # Get max videos from environment or config
        max_bg_videos = int(os.getenv("MAX_BG_VIDEOS", 20))
        
        # Use the smaller of the two values (config and env var)
        max_videos_to_download = min(
            self.config.max_videos if hasattr(self.config, 'max_videos') else max_bg_videos,
            max_bg_videos
        )

    def check_cancellation(self, st_state):
        """Check if cancellation was requested in the Streamlit UI"""
        if st_state.get("cancel_requested", False):
            logger.info("Cancellation requested, stopping processing")
            return True
        return False

    async def run_processing(self, st_state):
        if self.check_cancellation(st_state):  # Fixed: use the passed st_state parameter
            raise Exception("Processing cancelled by user")

    # async def search_videos_for_sentence(self, sentence, search_query):
        # # After your existing video selection code
        # selected_video = ...  # Your existing code that gets the selected video
        
        # # Add this logging code after a video is selected:
        # if hasattr(self, 'match_logger') and self.match_logger.enabled:
        #     self.match_logger.log_match(
        #         sentence=sentence.text if hasattr(sentence, 'text') else str(sentence),
        #         search_query=search_query,
        #         video_url=selected_video.get('url', ''),  # Use the actual selected video URL
        #         voice_provider=getattr(self.config, 'voice_provider', ''),
        #         voice_name=getattr(self.config, 'voice_name', '')
        #     )