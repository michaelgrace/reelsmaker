import multiprocessing
import os
import random
from typing import TYPE_CHECKING, Literal
from pathlib import Path
import subprocess

from app.effects import zoom_in_effect, zoom_out_effect
from app.utils.strings import (
    FFMPEG_TYPE,
    FileClip,
    adjust_audio_to_target_dBFS,
    get_video_size,
    web_color_to_ass,
)
from loguru import logger
from app.pexel import search_for_stock_videos
from PIL import Image
from PIL import Image as pil
from pkg_resources import parse_version
from pydantic import BaseModel
import ffmpeg


if parse_version(pil.__version__) >= parse_version("10.0.0"):
    Image.ANTIALIAS = Image.LANCZOS  # type: ignore

if TYPE_CHECKING:
    from app.base import BaseEngine

# TODO: implement me
positions = {
    "center": ["center", "center"],
    "left": ["left", "center"],
    "right": ["right", "center"],
    "top": ["center", "top"],
    "bottom": ["center", "bottom"],
}


class VideoGeneratorConfig(BaseModel):
    fontsize: int = 80
    stroke_color: str = "#ffffff"
    text_color: str = "#ffffff"
    stroke_width: int | None = 5
    font_name: str = "Luckiest Guy"
    bg_color: str | None = None
    subtitles_position: str = "center,center"
    threads: int = multiprocessing.cpu_count()

    watermark_path_or_text: str | None = "Now Here Nowhere"
    watermark_opacity: float = 0.5
    watermark_type: Literal["image", "text", "none"] = "text"
    background_music_path: str | None = None

    #aspect_ratio: str = "9:16"
    aspect_ratio: str = "16:9"  # Default landspape or vertical/portrait
    """ aspect ratio of the video """

    color_effect: str = "gray"
    cpu_preset: str = "ultrafast"  # Options: ultrafast, superfast, veryfast, faster, fast, medium


class VideoGenerator:
    def __init__(
        self,
        base_class: "BaseEngine",
        video_match_logger=None
    ):
        self.job_id = base_class.config.job_id
        self.config = base_class.config.video_gen_config
        self.cwd = base_class.cwd
        self.base_engine = base_class
        
        # Store both loggers
        self.metrics_logger = base_class.metrics_logger if hasattr(base_class, 'metrics_logger') else None
        self.video_match_logger = video_match_logger

        # Common FFmpeg locations to check
        ffmpeg_paths = [
            os.path.join(os.getcwd(), "bin/ffmpeg"),  # Local bin directory
            "/app/bin/ffmpeg",                         # Docker container path
            "/usr/bin/ffmpeg",                         # Linux system path 
            "/usr/local/bin/ffmpeg",                   # macOS Homebrew path
            "ffmpeg"                                   # System PATH
        ]

        # Try each location until we find one that exists
        self.ffmpeg_cmd = None
        for path in ffmpeg_paths:
            # For absolute paths, check if file exists
            if os.path.isabs(path) and os.path.exists(path):
                self.ffmpeg_cmd = path
                logger.info(f"Found FFmpeg at: {path}")
                break
            # For non-absolute paths, just use it (relies on system PATH)
            elif not os.path.isabs(path):
                self.ffmpeg_cmd = path
                logger.info(f"Using system FFmpeg: {path}")
                break

        if not self.ffmpeg_cmd:
            logger.warning("FFmpeg not found in any standard location, defaulting to 'ffmpeg'")
            self.ffmpeg_cmd = "ffmpeg"
        
        # Add in __init__ method where you set ffmpeg_cmd
        # After checking all paths, check one more basic option
        if not self.ffmpeg_cmd:
            try:
                # Check if ffmpeg is in the system PATH using 'which' command
                result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    self.ffmpeg_cmd = result.stdout.strip()
                    logger.info(f"Found FFmpeg using 'which': {self.ffmpeg_cmd}")
                else:
                    # Absolute fallback
                    self.ffmpeg_cmd = "ffmpeg"
                    logger.warning("Using 'ffmpeg' command as last resort")
            except Exception:
                self.ffmpeg_cmd = "ffmpeg"
                logger.warning("Using 'ffmpeg' command as last resort")

    async def get_video_url(self, search_term: str) -> str | None:
        """Get a single video URL matching the search term."""
        # Map aspect ratio to orientation
        aspect_to_orientation = {
            "9:16": "portrait",
            "16:9": "landscape", 
            "1:1": "square"
        }
        
        orientation = aspect_to_orientation.get(self.config.aspect_ratio)
        if not orientation:
            logger.warning(f"Unknown aspect ratio: {self.config.aspect_ratio}, defaulting to landscape")
            orientation = "landscape"
            
        logger.info(f"Searching for {orientation} videos matching '{search_term}'")
        
        try:
            # First try with specific orientation
            videos = await search_for_stock_videos(
                limit=3,
                min_dur=10,
                query=search_term,
                orientation=orientation,
                metrics_logger=self.base_engine.metrics_logger,
                video_match_logger=self.video_match_logger
            )
            
            if videos:
                logger.info(f"Found {len(videos)} {orientation} videos for '{search_term}'")
                # Extract the URL from the first video (videos now contain metadata)
                if isinstance(videos[0], dict):
                    return videos[0]['url']
                return videos[0]
                
            # If no results with orientation, try with a more generic search
            logger.warning(f"No {orientation} videos found for '{search_term}'. Trying generic search...")
            urls = await search_for_stock_videos(
                limit=3,
                min_dur=10,
                query=search_term,
                metrics_logger=self.base_engine.metrics_logger,
                video_match_logger=self.video_match_logger  # Add this
            )
            
            if urls:
                logger.warning("Using video with non-matching orientation - final video may require cropping")
                return urls[0]
                
            # No videos found at all - try with a generic term for the orientation
            generic_terms = {
                "portrait": "vertical video",
                "landscape": "landscape scene",
                "square": "square video"
            }
            
            generic_query = generic_terms.get(orientation, "nature")
            logger.warning(f"No videos found. Trying generic '{generic_query}' search...")
            
            urls = await search_for_stock_videos(
                limit=2,
                min_dur=10,
                query=generic_query,
                orientation=orientation,
                metrics_logger=self.base_engine.metrics_logger,
                video_match_logger=self.video_match_logger  # Add this
            )
            
            return urls[0] if urls else None
            
        except Exception as e:
            logger.error(f"Error getting video URL: {e}")
            return None

    def apply_subtitles(self, video_stream, subtitles_path):
        """Apply subtitles to video stream using available fonts"""
        if not subtitles_path or not os.path.exists(subtitles_path):
            logger.warning(f"Subtitle file not found: {subtitles_path}")
            return video_stream
        
        # Use available font helper
        font_name = self.get_available_font()
        
        # Get position settings
        position = self.config.subtitles_position.split(",")[0]
        styles = {
            "bottom": "Alignment=2",
            "center": "Alignment=10",
            "top": "Alignment=6",
        }

        text_color = web_color_to_ass(self.config.text_color)
        stroke_color = web_color_to_ass(self.config.stroke_color)
        font_size = round(self.config.fontsize / 5)

        style = (
            f"FontName={font_name},FontSize={font_size},"
            f"PrimaryColour={text_color},OutlineColour={stroke_color},Outline={self.config.stroke_width},Bold=1,"
            f"{styles.get(position, 'Alignment=10')}"
        )

        logger.info(f"Adding subtitles with style: {style}")
        
        # Apply subtitles to video stream
        return video_stream.filter(
            "subtitles", 
            subtitles_path, 
            force_style=style
        )

    def add_audio_mix(self, video_stream, background_music_filter, tts_audio_filter):
        audio_mix = ffmpeg.filter(
            stream_spec=[background_music_filter, tts_audio_filter],
            filter_name="amix",
            duration="longest",
            dropout_transition=0,
        )
        return ffmpeg.concat(video_stream, audio_mix, v=1, a=1)

    def concatenate_clips(self, inputs: list[FileClip], effects: list = []):
        processed_clips = []
        
        # Set dimensions based on aspect ratio
        width, height = 1920, 1080  # Default 16:9
        if self.config.aspect_ratio == "9:16":
            width, height = 1080, 1920  # Portrait
        elif self.config.aspect_ratio == "1:1":
            width = height = 1080  # Square
    
        for data in inputs:
            clip = data.ffmpeg_clip

            if len(effects) > 0:
                effect = random.choice(effects)
                clip = effect(clip)
                
            # Scale while preserving aspect ratio
            clip = clip.filter("scale", width, height, force_original_aspect_ratio="decrease")
            # Pad to fill the frame
            clip = clip.filter("pad", width, height, "(ow-iw)/2", "(oh-ih)/2", color="black")

            # Apply color effect if needed
            if (
                self.config.color_effect == "gray"
                and self.base_engine.config.video_type == "motivational"
            ):
                clip = clip.filter("format", "gray")

            processed_clips.append(clip)
        final_video = ffmpeg.concat(*processed_clips, v=1, a=0)
        return final_video

    async def generate_video(
        self,
        clips: list[FileClip],
        subtitles_path: str,
        speech_filter,
        video_duration: float,
    ) -> str:
        """Generate video from clips."""
        logger.info("Generating video...")
        
        # Debug the content of speech_filter to identify None values
        logger.debug(f"Speech filter type: {type(speech_filter)}")
        
        # Add null checks for all file paths
        if subtitles_path is None:
            logger.warning("Subtitle path is None, generating video without subtitles")
            # Proceed without subtitles

        # Check each clip to ensure it has a valid path
        valid_clips = []
        for clip in clips:
            if clip and clip.filepath:
                valid_clips.append(clip)
            else:
                logger.warning(f"Skipping invalid clip: {clip}")

        if not valid_clips:
            raise ValueError("No valid video clips available for processing")

        effects = [zoom_out_effect, zoom_in_effect]

        # Define output path
        output_path = (Path(self.cwd) / f"{self.job_id}_final.mp4").as_posix()

        # In video_gen.py, inside generate_video method
        if self.config.background_music_path is None or not os.path.exists(self.config.background_music_path):
            logger.warning(f"Background music path not found: {self.config.background_music_path}")
            
            # Create a silent audio file in the temp directory instead
            silent_audio = os.path.join(os.path.dirname(subtitles_path), "silent_audio.mp3")
            
            try:
                # Create a silent audio file using ffmpeg
                logger.info(f"Generating silent audio file at {silent_audio}")
                (
                    ffmpeg.input('anullsrc', f='lavfi', t=10)
                    .output(silent_audio, ar=44100)
                    .run(overwrite_output=True, cmd=self.ffmpeg_cmd, quiet=True)
                )
                self.config.background_music_path = silent_audio
            except Exception as e:
                logger.exception(f"Failed to create silent audio: {e}")
                # Proceed without background music by creating an input with silence
                # We'll handle this in the ffmpeg pipeline

        # music must end at the end of the speech, add extra 3 seconds to make it look good
        try:
            # First try to use the background music file
            if self.config.background_music_path and os.path.exists(self.config.background_music_path):
                # Check if file is readable and valid
                file_size = os.path.getsize(self.config.background_music_path)
                if file_size > 0:
                    logger.info(f"Using background music: {self.config.background_music_path} ({file_size} bytes)")
                    music_input = ffmpeg.input(
                        adjust_audio_to_target_dBFS(self.config.background_music_path),
                        t=video_duration,
                    )
                else:
                    logger.warning(f"Background music file is empty: {self.config.background_music_path}")
                    raise ValueError("Empty background music file")
            else:
                # If no background music, generate silent audio directly with ffmpeg
                logger.warning("Creating silent audio track directly with ffmpeg")
                music_input = ffmpeg.input('anullsrc', f='lavfi', t=video_duration)
        except Exception as e:
            logger.exception(f"Background music processing failed: {e}")
            # Fallback to silent audio in case of any failure
            logger.warning("Using fallback silent audio")
            music_input = ffmpeg.input('anullsrc', f='lavfi', t=video_duration)

        if self.base_engine.config.video_type == "motivational":
            effects = []

        video_stream = self.concatenate_clips(valid_clips, effects)
        video_stream = self.apply_aspect_ratio(video_stream)
        video_stream = self.apply_subtitles(video_stream, subtitles_path)
        video_stream = self.apply_watermark(video_stream)
        video_stream = self.add_audio_mix(
            video_stream=video_stream,
            tts_audio_filter=speech_filter,
            background_music_filter=music_input,
        )
        try:
            # Use CPU encoding instead of GPU
            logger.info("Using CPU encoding for video processing")
            output = (
                ffmpeg
                .output(
                    video_stream,
                    output_path,
                    vcodec="libx264",  # CPU encoder instead of h264_nvenc
                    acodec="aac",
                    preset="ultrafast",  # CPU-friendly preset
                    crf=28,  # Lower quality but faster (range 18-28)
                    pix_fmt="yuv420p",
                    movflags="+faststart"
                )
                .global_args('-progress', 'pipe:1')
            )
            
            # Run the encoding command
            logger.info(f"Starting video generation with {self.config.threads} threads")
            output.run(overwrite_output=True, cmd=self.ffmpeg_cmd)
            
            return output_path
        except Exception as e:
            logger.error(f"Error during video generation: {e}")
            # Try fallback with even lower quality settings
            try:
                logger.warning("Trying fallback encoding with lower quality")
                output = (
                    ffmpeg
                    .output(
                        video_stream,
                        output_path,
                        vcodec="libx264",
                        acodec="aac",
                        preset="veryfast",
                        crf=30,
                        pix_fmt="yuv420p",
                        movflags="+faststart"
                    )
                    .global_args('-progress', 'pipe:1')
                )
                output.run(overwrite_output=True, cmd=self.ffmpeg_cmd)
                return output_path
            except Exception as fallback_error:
                logger.error(f"Fallback encoding failed: {fallback_error}")
                raise


    # def get_background_audio(self, video_clip: VideoClip, song_path: str) -> AudioClip:
    #     """Takes the original audio and adds the background audio"""
    #     logger.info(f"Getting background music: {song_path}")

    #     def adjust_audio_to_target_dBFS(audio_file_path: str, target_dBFS=-30.0):
    #         audio = AudioSegment.from_file(audio_file_path)
    #         change_in_dBFS = target_dBFS - audio.dBFS
    #         adjusted_audio = audio.apply_gain(change_in_dBFS)
    #         adjusted_audio.export(audio_file_path, format="mp3")
    #         logger.info(f"Adjusted audio to target dBFS: {target_dBFS}")
    #         return audio_file_path

    #     # set the volume of the song to 10% of the original volume
    #     song_path = adjust_audio_to_target_dBFS(song_path)

    #     background_audio = AudioFileClip(song_path)

    #     if background_audio.duration < video_clip.duration:
    #         # calculate how many times the background audio needs to repeat
    #         repeats_needed = int(video_clip.duration // background_audio.duration) + 1

    #         # create a list of the background audio repeated
    #         background_audio_repeated = concatenate_audioclips(
    #             [background_audio] * repeats_needed
    #         )

    #         # trim the repeated audio to match the video duration
    #         background_audio_repeated = background_audio_repeated.subclip(
    #             0, video_clip.duration
    #         )
    #     else:
    #         background_audio_repeated = background_audio.subclip(0, video_clip.duration)

    #     comp_audio = CompositeAudioClip([video_clip.audio, background_audio_repeated])

    #     return comp_audio

    def crop(self, clip: FileClip) -> FFMPEG_TYPE:
        width, height = get_video_size(clip.filepath)
        aspect_ratio = width / height
        ffmpeg_clip = clip.ffmpeg_clip

        if aspect_ratio < 0.5625:
            crop_height = int(width / 0.5625)
            return ffmpeg_clip.filter(
                "crop", w=width, h=crop_height, x=0, y=(height - crop_height) // 2
            )
        else:
            crop_width = int(0.5625 * height)
            return ffmpeg_clip.filter(
                "crop", w=crop_width, h=height, x=(width - crop_width) // 2, y=0
            )

    def apply_watermark(self, video_stream):
        """Adds a watermark to the bottom-right of the video."""

        sysfont = os.path.join(os.getcwd(), "narrator/sys/fonts/luckiestguy.ttf")

        # Check if watermark path/text is set and watermark type is valid
        if (
            not self.config.watermark_path_or_text
            or self.config.watermark_type == "none"
        ):
            return video_stream  # No watermark, return original stream

        # Text-based watermark
        if self.config.watermark_type == "text":
            watermark_text = self.config.watermark_path_or_text
            video_stream = video_stream.filter(
                "drawtext",
                text=watermark_text,
                x="if(lt(mod(t,20),10), (main_w-text_w)-16, if(lt(mod(t,20),10), 16, if(lt(mod(t,20),15), 16, (main_w-text_w)-16)))",
                y="if(lt(mod(t,20),10), (main_h-text_h)-100, if(lt(mod(t,20),10), 50, if(lt(mod(t,20),15), (main_h-text_h)-100, 50)))",
                fontsize=40,
                fontcolor="white",
                fontfile=sysfont,
            )
            logger.warning(f"Using text watermark with font: {sysfont}")

        # Image-based watermark
        elif self.config.watermark_type == "image":
            watermark_path = self.config.watermark_path_or_text
            watermark = ffmpeg.input(watermark_path)

            # Resize watermark to a height of 100 while maintaining aspect ratio
            watermark = watermark.filter("scale", -1, 100)

            # Overlay the watermark in the bottom-right corner with 8px padding
            video_stream = ffmpeg.overlay(
                video_stream,
                watermark,
                x="(main_w-overlay_w)-8",
                y="(main_h-overlay_h)-8",
            )

        logger.debug("Added watermark to video.")
        return video_stream

    async def create_gif(
        self, master_video_path: str, start_time: float = 1.0, end_time: float = 1.5
    ) -> str:
        logger.debug("Creating GIF...")
        gif_path = f"{self.cwd}/{self.job_id}.gif"

        (
            ffmpeg.input(master_video_path, ss=start_time, t=end_time - start_time)
            .filter("fps", fps=6)
            .filter("scale", "iw/2", "ih/2")
            .output(gif_path, format="gif", loop=0, pix_fmt="rgb24")
            .run(overwrite_output=True)
        )

        return gif_path

    def apply_aspect_ratio(self, video_stream):
        """Apply aspect ratio without requiring explicit dimensions."""
        target_ratio = self.config.aspect_ratio
        logger.info(f"Applying target aspect ratio: {target_ratio}")
        
        # Set standard dimensions based on target ratio
        if target_ratio == "16:9":
            return video_stream.filter("scale", "1920", "1080", force_original_aspect_ratio="decrease").filter(
                "pad", "1920", "1080", "(ow-iw)/2", "(oh-ih)/2", color="black"
            )
        elif target_ratio == "9:16":
            return video_stream.filter("scale", "1080", "1920", force_original_aspect_ratio="decrease").filter(
                "pad", "1080", "1920", "(ow-iw)/2", "(oh-ih)/2", color="black"
            )
        elif target_ratio == "1:1":
            return video_stream.filter("scale", "1080", "1080", force_original_aspect_ratio="decrease").filter(
                "pad", "1080", "1080", "(ow-iw)/2", "(oh-ih)/2", color="black"
            )
        else:
            logger.warning(f"Unknown aspect ratio: {target_ratio}, using original dimensions")
            return video_stream

    def get_available_font(self):
        """Find an available font in the container"""
        # Define font search paths
        search_paths = [
            # Custom font path
            f"/app/fonts/{self.config.font_name}.ttf",
            # Standard Linux font paths
            f"/usr/share/fonts/truetype/{self.config.font_name.lower()}/{self.config.font_name}.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Fallback
        ]
        
        for path in search_paths:
            if os.path.exists(path):
                logger.info(f"Using font: {path}")
                return path
    
        logger.warning(f"Font '{self.config.font_name}' not found, using default")
        return "DejaVuSans-Bold"  # Default that's always available