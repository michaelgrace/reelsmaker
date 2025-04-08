import asyncio
import multiprocessing
import os
import typing
import time
import threading
from uuid import uuid4
from loguru import logger
import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile
from app.reels_maker import ReelsMaker, ReelsMakerConfig
from app.synth_gen import SynthConfig
from app.video_gen import VideoGeneratorConfig
from streamlit.components.v1 import html

# Add this import if not already present
import os.path

# Add this near the top of your main file, after the imports but before your main() function
def check_log_dirs():
    """Check log directories and their permissions."""
    import os
    from pathlib import Path
    
    tmp_dir = os.path.join(os.getcwd(), "tmp")
    tmp_logs_dir = os.path.join(tmp_dir, "logs")
    
    logger.debug(f"tmp directory: {tmp_dir}")
    logger.debug(f"  exists: {os.path.exists(tmp_dir)}")
    if os.path.exists(tmp_dir):
        logger.debug(f"  writable: {os.access(tmp_dir, os.W_OK)}")
    
    logger.debug(f"tmp/logs directory: {tmp_logs_dir}")
    logger.debug(f"  exists: {os.path.exists(tmp_logs_dir)}")
    if os.path.exists(tmp_logs_dir):
        logger.debug(f"  writable: {os.access(tmp_logs_dir, os.W_OK)}")
        logger.debug(f"  contents: {os.listdir(tmp_logs_dir) if os.path.exists(tmp_logs_dir) else 'N/A'}")

def check_logging_status():
    """Check the status of logging in the application."""
    import os
    from pathlib import Path
    
    # Check the tmp/logs directory
    logs_dir = Path(os.path.join(os.getcwd(), "tmp", "logs"))
    
    logger.debug(f"Checking logs directory: {logs_dir}")
    
    if logs_dir.exists():
        log_files = list(logs_dir.glob("*.csv"))
        
        logger.debug(f"Found {len(log_files)} log files:")
        for log_file in log_files:
            size = log_file.stat().st_size
            logger.debug(f"  - {log_file.name} ({size} bytes)")
            
            # If CSV file exists but has almost no content, read it
            if size < 1000 and log_file.suffix.lower() == '.csv':
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.debug(f"    Content: {content[:200]}...")
                except Exception as e:
                    logger.error(f"    Error reading file: {e}")

# Update the create_timer function for a more robust implementation
def create_timer():
    """Create a JavaScript timer that updates every second without page refreshes"""
    timer_html = """
        <div id="timer_display" style="font-size: 1.2rem; font-weight: bold; margin: 10px 0; color: white; background-color: rgba(0,0,0,0.5); padding: 5px 10px; border-radius: 5px; display: inline-block;">⏱️ Elapsed time: 0m 0s</div>
     
    <script>
        // Timer variables
        var startTime = new Date().getTime();
        var timerInterval;
        
        // Format time function
        function formatTime(seconds) {
            var minutes = Math.floor(seconds / 60);
            var secs = seconds % 60;
            return minutes + "m " + secs + "s";
        }
        
        // Update the timer display
        function updateTimer() {
            var currentTime = new Date().getTime();
            var elapsedSeconds = Math.floor((currentTime - startTime) / 1000);
            document.getElementById("timer_display").innerHTML = "⏱️ Elapsed time: " + formatTime(elapsedSeconds);
            
            // Also check for success message on each update (backup method)
            checkForSuccess();
        }
        
        // Function to check for success message
        function checkForSuccess() {
            const successElements = document.querySelectorAll('.element-container');
            for (let element of successElements) {
                if (element.innerText && element.innerText.includes("Video generated successfully")) {
                    console.log("Success message found, stopping timer");
                    clearInterval(timerInterval);
                    return;
                }
            }
        }
        
        // Start the timer
        timerInterval = setInterval(updateTimer, 1000);
        
        // Watch for completion message with enhanced targeting
        const observer = new MutationObserver((mutations) => {
            for (let mutation of mutations) {
                if (mutation.type === 'childList' && mutation.addedNodes.length) {
                    for (let node of mutation.addedNodes) {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            // Check the node itself
                            if (node.innerText && node.innerText.includes("Video generated successfully")) {
                                console.log("Timer stopped: video generation complete (direct match)");
                                clearInterval(timerInterval);
                                return;
                            }
                            
                            // Also check any child elements with class 'stAlert'
                            const alerts = node.querySelectorAll('.stAlert');
                            for (let alert of alerts) {
                                if (alert.innerText && alert.innerText.includes("Video generated successfully")) {
                                    console.log("Timer stopped: video generation complete (alert match)");
                                    clearInterval(timerInterval);
                                    return;
                                }
                            }
                        }
                    }
                }
            }
        });
        
        // Start observing the entire document body with all possible options
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            characterData: true
        });
        
        // Initial check in case the message is already there
        checkForSuccess();
    </script>
    """
    return html(timer_html, height=50)

# Near the beginning of your main() function, add:
st.session_state.setdefault("last_video_path", None)
if "last_video_bytes" not in st.session_state:
    st.session_state["last_video_bytes"] = None

if "queue" not in st.session_state:
    st.session_state["queue"] = {}

# Add this new dictionary for tracking job timestamps
if "job_timestamps" not in st.session_state:
    st.session_state["job_timestamps"] = {}

queue: dict[str, ReelsMakerConfig] = st.session_state["queue"]
job_timestamps = st.session_state["job_timestamps"]  # Reference the timestamps dict

# Add this near the top of your main() function, right after where session state variables are defined

# Make sure we load Kokoro voices at startup and cache them in session_state
if "kokoro_voices" not in st.session_state:
    try:
        from app.kokoro_service import kokoro_client
        kokoro_voices = kokoro_client.get_voices()
        if kokoro_voices:
            st.session_state["kokoro_voices"] = kokoro_voices
            logger.info(f"Loaded {len(kokoro_voices)} Kokoro voices")
        else:
            st.session_state["kokoro_voices"] = []
            logger.warning("No Kokoro voices returned from service")
    except Exception as e:
        st.session_state["kokoro_voices"] = []
        logger.error(f"Failed to load Kokoro voices: {e}")

# Load ElevenLabs voices from JSON
if "elevenlabs_voices" not in st.session_state:
    try:
        import json
        elevenlabs_path = os.path.join(os.getcwd(), "data", "elevenlabs_voices.json")
        if os.path.exists(elevenlabs_path):
            with open(elevenlabs_path, 'r') as f:
                elevenlabs_data = json.load(f)
                st.session_state["elevenlabs_voices"] = elevenlabs_data.get("voices", [])
                logger.info(f"Loaded {len(st.session_state['elevenlabs_voices'])} ElevenLabs voices")
        else:
            # Fallback to default voices if file not found
            st.session_state["elevenlabs_voices"] = [
                "21m00Tcm4TlvDq8ikWAM", "2EiwWnXFnvU5JabPnv8n", "D38z5RcWu1voky8WS1ja", 
                "IKne3meq5aSn9XLyUdCD", "XB0fDUnXU5powFXDhCwa", "pNInz6obpgDQGcFmaJgB", 
                "jBpfuIE2acCO8z3wKNLl", "onwK4e9ZLuTAKqWW03F9", "g5CIjZEefAph4nQFvHAz", 
                "ErXwobaYiN019PkySvjV"
            ]
            logger.warning("ElevenLabs voices file not found, using defaults")
    except Exception as e:
        st.session_state["elevenlabs_voices"] = []
        logger.error(f"Failed to load ElevenLabs voices: {e}")

# Load TikTok voices from JSON
if "tiktok_voices" not in st.session_state:
    try:
        import json
        tiktok_path = os.path.join(os.getcwd(), "data", "tiktok_voices.json")
        if os.path.exists(tiktok_path):
            with open(tiktok_path, 'r') as f:
                tiktok_data = json.load(f)
                st.session_state["tiktok_voices"] = tiktok_data.get("voices", [])
                logger.info(f"Loaded {len(st.session_state['tiktok_voices'])} TikTok voices")
        else:
            # Fallback to default voices if file not found
            st.session_state["tiktok_voices"] = [
                "en_us_001", "en_us_006", "en_us_007", "en_us_009", "en_us_010"
            ]
            logger.warning("TikTok voices file not found, using defaults")
    except Exception as e:
        st.session_state["tiktok_voices"] = []
        logger.error(f"Failed to load TikTok voices: {e}")

# Add to reelsmaker.py where the queue is defined
def clear_queue():
    """Clear all pending jobs from the queue"""
    global queue
    if 'queue' in globals():
        queue.clear()  # Correct way to clear a dictionary
        logger.info("Queue cleared completely")

# Call this when starting a new generation
if "queue_initialized" not in st.session_state:
    clear_queue()
    st.session_state["queue_initialized"] = True


MAX_CONCURRENT = 2  # Adjust based on your server's resources

async def download_to_path(dest: str, buff: UploadedFile) -> str:
    with open(dest, "wb") as f:
        f.write(buff.getbuffer())
    return dest


# Add this function to check audio file validity
async def validate_audio_file(file_path):
    try:
        # Use FFmpeg to probe the file
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"],
            stderr=subprocess.PIPE,
            text=True
        )
        # If there's output, there were errors
        if result.stderr:
            logger.warning(f"Audio validation errors: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"Audio validation failed: {e}")
        return False


def format_elapsed_time(seconds):
    """Format seconds into minutes and seconds display"""
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes}m {seconds}s"

# Add this function definition before your main() function
def add_log_diagnostics(reels_maker):
    """Add diagnostic information about logging after video generation"""
    try:
        if hasattr(reels_maker, 'metrics_logger'):
            logger.info(f"Metrics logger enabled: {reels_maker.metrics_logger.enabled}")
            if hasattr(reels_maker.metrics_logger, 'log_file') and reels_maker.metrics_logger.log_file:
                logger.info(f"Metrics log file: {reels_maker.metrics_logger.log_file}")
                
        if hasattr(reels_maker, 'match_logger'):
            logger.info(f"Match logger enabled: {reels_maker.match_logger.enabled}")
            if hasattr(reels_maker.match_logger, 'log_file') and reels_maker.match_logger.log_file:
                logger.info(f"Match log file: {reels_maker.match_logger.log_file}")
        
        # Run the check_logging_status function we created earlier
        check_logging_status()
    except Exception as e:
        logger.error(f"Error in log diagnostics: {e}")

async def main():
    # Add this at the beginning of your main function
    check_log_dirs()
    
    # At the beginning of your main function, add this to display previously generated videos
    if ("last_video_path" in st.session_state and 
        st.session_state["last_video_path"] is not None and 
        os.path.exists(st.session_state["last_video_path"])):
        with st.expander("Previously Generated Video", expanded=True):
            st.video(st.session_state["last_video_path"])
            
            # Add download button for the previous video
            with open(st.session_state["last_video_path"], "rb") as file:
                video_bytes = file.read()
                st.download_button(
                    label="Download Video",
                    data=video_bytes,
                    file_name=os.path.basename(st.session_state["last_video_path"]),
                    mime="video/mp4"
                )

    # Add this line at the beginning of main()
    cwd = os.path.join(os.getcwd(), "tmp")
    os.makedirs(cwd, exist_ok=True)
    
    st.title("Video Reels Story Maker")
    st.divider()

    sentence_tab, prompt_tab = st.tabs(
        ["Direct Text Input", "AI-Generated Content"]
    )

    with sentence_tab:
        # Create a container for the text area and character count
        input_container = st.container()
        
        # Add the text area
        sentence = input_container.text_area(
            label="Enter your quote",
            placeholder="Nothing is impossible. The word itself says 'I'm possible!, Champions keep playing until they get it right, You are never too old to set another goal or to dream a new dream.",
            height=100,
            key="sentence_input"  # Add a key for on_change detection
        )
        
        # Display character count below the text area
        char_count = len(sentence) if sentence else 0
        color = "#ff4b4b" if char_count > 200 else ("#ffaa4b" if char_count > 150 else "#4bb543")
        input_container.markdown(f"<p style='color:{color};text-align:right;margin-top:-15px;'>Character count: {char_count}</p>", unsafe_allow_html=True)

    with prompt_tab:
        prompt = st.text_area(
            label="Enter your prompt",
            placeholder="A motivation quote about life & pleasure",
            height=100,
        )


    with st.expander("Settings", expanded=True):

        auto_video_tab, upload_video_tab, upload_audio_tab, audio_url_tab = st.tabs([
            "Auto Add Video", 
            "Upload Videos", 
            "Upload Audio", 
            "Enter Audio URL"
        ])

        # Auto Add Video tab content
        with auto_video_tab:
            st.write("We'll automatically download background videos related to your prompt")
            # Video count control moved to the row below
        
        # Upload Videos tab content
        with upload_video_tab:
            st.write("Upload your own background videos")
            uploaded_videos = st.file_uploader(
                "Upload background videos",
                type=["mp4", "webm"],
                accept_multiple_files=True,
            )

        # Upload Audio tab content
        with upload_audio_tab:
            st.write("Upload your own background audio")
            uploaded_audio = st.file_uploader(
                "Upload background audio", 
                type=["mp3", "webm"]
            )

        # Audio URL tab content
        with audio_url_tab:
            st.write("Enter a URL to audio you want to use")
            st.warning("Sorry, this feature is not available yet")
            background_audio_url = st.text_input(
                "Enter a background audio URL", 
                placeholder="Enter URL"
            )

        # Initialize voice provider options
        voice_providers = ["Kokoro", "Elevenlabs", "TikTok"]

        # Create three columns for the voice configuration
        col1, col2, col3 = st.columns(3)

        # Column 1: Voice provider selection with tooltip
        with col1:
            voice_provider_index = 0  # Default to Kokoro
            voice_provider = st.selectbox(
                "Select voice provider", 
                options=voice_providers,
                index=voice_provider_index,
                format_func=lambda x: x,  # Keep it short for column display
                help=("Kokoro: Local AI voice model with many high-quality voices" if voice_providers[voice_provider_index] == "Kokoro" else
                      "Elevenlabs: Professional quality voices (requires API key)" if voice_providers[voice_provider_index] == "Elevenlabs" else
                      "TikTok: Free voice service from TikTok")
            )
            
        # Load Kokoro voices for the dropdown if needed
        if "kokoro_voices" not in st.session_state:
            from app.kokoro_service import kokoro_client
            st.session_state["kokoro_voices"] = kokoro_client.get_voices()

        # Column 2: Always show language selection, but disable for non-Kokoro
        with col2:
            # Default to English only for non-Kokoro providers
            language_options = ["English"]
            default_language_index = 0
            is_kokoro = voice_provider.lower() == "kokoro"
            
            # For Kokoro, populate from actual data
            if is_kokoro:
                kokoro_voices = st.session_state.get("kokoro_voices", [])
                if kokoro_voices:
                    # Group voices by language
                    languages = {}
                    for v in kokoro_voices:
                        name = v["name"]
                        language = name.split(" ")[0]  # Extract language from name
                        if language not in languages:
                            languages[language] = []
                        languages[language].append(v)
                    
                    language_options = sorted(languages.keys())
                    default_language_index = language_options.index("English") if "English" in language_options else 0
            
            # Always show the selectbox, but disable for non-Kokoro
            selected_language = st.selectbox(
                "Select language", 
                language_options, 
                index=default_language_index,
                help="Choose the language for narration" if is_kokoro else "Language selection (only available with Kokoro)",
                disabled=not is_kokoro
            )

        # Column 3: Voice selection based on provider
        with col3:
            if voice_provider.lower() == "tiktok":
                tiktok_voices = st.session_state.get("tiktok_voices", [])
                if not tiktok_voices:
                    st.warning("No TikTok voices available. Using default.")
                    voice = "en_us_001"  # Default fallback
                else:
                    voice = st.selectbox(
                        "Select voice",
                        options=tiktok_voices,
                        index=0,
                        help="Free TikTok voices for video narration"
                    )
                
            elif voice_provider.lower() == "kokoro":
                kokoro_voices = st.session_state.get("kokoro_voices", [])
                
                if not kokoro_voices:
                    st.warning("No Kokoro voices available. Using default voice.")
                    voice = "af_heart"  # Default fallback voice
                else:
                    filtered_voices = []
                    for v in kokoro_voices:
                        if v["name"].startswith(selected_language):
                            filtered_voices.append(v)
                    
                    voice_options = [(v["id"], v["name"]) for v in filtered_voices]
                    
                    if not voice_options:
                        st.warning("No voices available for selected language. Using default voice.")
                        voice = "af_heart"  # Default fallback
                    else:
                        selected_voice_tuple = st.selectbox(
                            "Select voice", 
                            options=voice_options,
                            format_func=lambda x: x[1],  # Display the name
                            help=f"Choose a voice for {selected_language} narration"
                        )
                        
                        if selected_voice_tuple:
                            voice = selected_voice_tuple[0]  # Use the ID for the API
                        else:
                            voice = "af_heart"  # Default fallback
            else:
                elevenlabs_voices = st.session_state.get("elevenlabs_voices", [])
                if not elevenlabs_voices:
                    st.warning("No ElevenLabs voices available. Using default.")
                    voice = "21m00Tcm4TlvDq8ikWAM"  # Default fallback
                else:
                    voice = st.selectbox(
                        "Select voice",
                        options=elevenlabs_voices,
                        index=0,
                        help="Professional quality ElevenLabs voices (requires API key)"
                    )


        
        # NEW ROW: Video configuration in 3 columns
        # st.write("Video Configuration")
        vid_col1, vid_col2, vid_col3 = st.columns(3)
        
        with vid_col1:
            # Convert slider to selectbox with increments of 5
            max_bg_videos = int(os.getenv("MAX_BG_VIDEOS", 20))  # Default to 20 if not set
            video_count_options = list(range(5, max_bg_videos + 1, 5))  # Create options: 5, 10, 15, 20...
            max_videos = st.selectbox(
                "Number of videos to download",
                options=video_count_options,
                index=0,  # Default to the first option
                help="How many background videos to download from Pexels"
            )

        with vid_col2:
            # Move aspect ratio here from Video Processing Options
            aspect_ratio = st.selectbox(
                "Aspect Ratio",
                options=[
                    "Landscape (16:9) - YouTube/Facebook", 
                    "Portrait (9:16) - Instagram/TikTok",             
                    "Square (1:1) - Instagram"
                ],
                index=0,  # Default to landscape
                help="Choose the aspect ratio based on your target platform"
            )

        with vid_col3:
            # Convert select_slider to regular selectbox with same options
            video_quality = st.selectbox(
                "Video Quality",
                options=["Fastest (Low Quality)", "Balanced", "High Quality"],
                index=0,  # Default to fastest
                help="Higher quality takes longer but produces better results"
            )


        col1, col2, col3 = st.columns(3)

        with col1:
            # Font picker selectbox with only the validated fonts
            font_options = [
                "Arial", "Luckiest Guy", "Roboto"  # Match the valid_fonts in subtitle_gen.py
            ]
            font_name = st.selectbox(
                "Subtitle Font",
                options=font_options,
                index=1,  # Default to Arial
                help="Choose the font for your subtitles"
            )
            
        with col2:
            fontsize = st.number_input("Font size", value=200, step=5, min_value=10)

        with col3:
            stroke_width = st.number_input("Border", value=2, step=1, min_value=1)


        # Create a new 3-column row for advanced settings
        # st.write("Advanced Settings")
        adv_col1, adv_col2, adv_col3 = st.columns(3)

           
        with adv_col1:
            # Existing subtitle position control
            subtitles_position = st.selectbox("Subtitle position", ["center,center"])

            
            # # Add sentence pause control
            # sentence_pause = st.slider(
            #     "Pause between sentences (seconds)",
            #     min_value=0.0,
            #     max_value=2.0,
            #     value=0.5,
            #     step=0.1,
            #     help="Add extra pause between sentences in narration"
            # )
            
        with adv_col2:
            # Auto-download toggle (boolean selectbox)
            auto_download = st.selectbox(
                "Auto-download",
                options=[True, False],
                index=1,  # Default to False - this is already working correctly
                format_func=lambda x: "Yes" if x else "No",
                help="Automatically download resources when generating"
            )
            
        with adv_col3:
            # Threads control - dynamic default based on CPU count
            cpu_count = multiprocessing.cpu_count()
            cpu_count = max(2, min(8, cpu_count // 2))  # Ensure minimum of 2 threads
            threads = st.number_input(
                "Threads", 
                value=cpu_count,   # Dynamic default based on CPU count
                step=2,            # Increment by 2
                min_value=2,       # Minimum of 2 threads
                max_value=16,      # Maximum of 16 threads
                help="Number of CPU threads to use for processing"
            )

        # Create the first row of controls for audio settings
        audio_col1, audio_col2, audio_col3 = st.columns(3)
        
        with audio_col1:
            sentence_pause = st.slider(
                "Sentence pause (seconds)",
                min_value=0.5,
                max_value=2.0,
                value=0.75,
                step=0.05,
                help="Add extra pause between sentences in narration"
            )
        
        with audio_col3:
            voice_style = st.selectbox(
                "Voice Style",
                options=["Neutral", "Cheerful", "Serious", "Excited", "Sad"],
                index=0,
                help="Control the emotional tone of the voice (when supported)"
            )
        
        with audio_col2:
            speech_rate = st.slider(
                "Speech Rate",
                min_value=0.7,
                max_value=1.5,
                value=1.0,
                step=0.1,
                help="Control how fast the narration speaks"
            )

      
        # Extract just the ratio value for the config
        aspect_ratio_map = {
            "Portrait (9:16) - Instagram/TikTok": "9:16",
            "Landscape (16:9) - YouTube/Facebook": "16:9",
            "Square (1:1) - Instagram": "1:1"
        }
        aspect_ratio_value = aspect_ratio_map[aspect_ratio]

        # Convert to CPU preset values
        preset_mapping = {
            "Fastest (Low Quality)": "ultrafast", 
            "Balanced": "veryfast",
            "High Quality": "medium"
        }
        cpu_preset = preset_mapping[video_quality]
        

        # Add this after your audio controls row (around line 435)
        # Create a new row for video enhancement controls
        video_enhance_col1, video_enhance_col2, video_enhance_col3 = st.columns(3)

        with video_enhance_col1:
            platform_preset = st.selectbox(
                "Platform Optimization",
                options=["TikTok", "Instagram", "YouTube Shorts", "Facebook", "LinkedIn"],
                index=1,  # Default to Instagram
                help="Optimize settings for specific platforms"
            )

        with video_enhance_col2:
            transition_effect = st.selectbox(
                "Transition Effect",
                options=["None", "Fade", "Dissolve", "Wipe", "Slide"],
                index=1,  # Default to Fade
                help="Effect between video clips"
            )

        with video_enhance_col3:
            video_mood = st.selectbox(
                "Video Mood",
                options=["Neutral", "Motivational", "Professional", "Dramatic", "Cheerful"],
                index=1,  # Default to Motivational
                help="Select mood for video content selection"
            )


  # Subtitles controls with centered color pickers but left-aligned labels
        color_col1, color_col2, color_col3 = st.columns(3)

        with color_col1:
            text_color_label = st.write("Subtitles Text color")
            _, picker_col, _ = st.columns([0.25, 0.5, 0.25])  # Create sub-columns for centering
            with picker_col:
                text_color = st.color_picker("##text_color", value="#ffffff", label_visibility="collapsed")

        with color_col2:
            bg_label = st.write("Background color (None)")
            _, picker_col, _ = st.columns([0.25, 0.5, 0.25])
            with picker_col:
                bg_color = st.color_picker("##bg_color", value=None, label_visibility="collapsed")

        with color_col3:
            stroke_label = st.write("Stroke color")
            _, picker_col, _ = st.columns([0.25, 0.5, 0.25])
            with picker_col:
                stroke_color = st.color_picker("##stroke_color", value="#000000", label_visibility="collapsed")



        config = ReelsMakerConfig(
            job_id="".join(str(uuid4()).split("-")),
            video_type="narrator",
            prompt=prompt or sentence,
            cwd=cwd,
            background_audio_url=background_audio_url,
            max_videos=max_videos,
            auto_download=auto_download,
            video_gen_config=VideoGeneratorConfig(
                bg_color=str(bg_color),
                fontsize=int(fontsize),
                stroke_color=str(stroke_color),
                stroke_width=int(stroke_width),
                subtitles_position=str(subtitles_position),
                text_color=str(text_color),
                font_name=font_name,
                threads=int(threads),
                cpu_preset=cpu_preset,
                aspect_ratio=aspect_ratio_value,
                transition_effect=transition_effect,  # Add new parameters
                platform_preset=platform_preset,
                video_mood=video_mood
            ),
            synth_config=SynthConfig(
                voice=str(voice),
                voice_provider=(voice_provider.lower() if voice_provider else None) or 
                              os.environ.get("VOICE_PROVIDER", "").lower() or 
                              "tiktok",
                sentence_pause=sentence_pause,
                speech_rate=speech_rate,        # Add new parameters
                voice_style=voice_style
            ),
        )

        # Add validation for uploaded audio
        if uploaded_audio:
            try:
                # Validate audio file size
                if uploaded_audio.size > 20 * 1024 * 1024:  # Limit to 20MB
                    st.warning("Audio file is very large, which may cause processing issues")
                
                # Save the audio file
                audio_path = await download_to_path(
                    dest=os.path.join(config.cwd, "background.mp3"), 
                    buff=uploaded_audio
                )
                
                config.video_gen_config.background_music_path = audio_path
                
            except Exception as audio_error:
                st.warning(f"Error processing audio file: {audio_error}. Using default audio instead.")
                config.video_gen_config.background_music_path = None

        if uploaded_videos:
            config.video_paths = [
                await download_to_path(dest=os.path.join(config.cwd, p.name), buff=p)
                for p in uploaded_videos
            ]

    print(f"starting reels maker: {config.model_dump_json()}")
    # st.write(
    #     "This process is CPU-intensive and will take a considerable time to complete"
    # )

    # Replace the current button logic with this simplified version:
    generate_clicked = st.button(
        "🎬 Generate Video", 
        type="primary",
        key="generate_video_button",
        use_container_width=True
    )

    # Only proceed with generation if the button is clicked and text is provided
    if generate_clicked:
        if not prompt.strip() and not sentence.strip():
            st.error("⚠️ Please enter text in either the prompt field or direct text field before generating a video.")
        else:
            # IMPORTANT: Clear previous generation state
            if "last_generated_id" in st.session_state:
                old_id = st.session_state["last_generated_id"]
                if old_id in queue:
                    del queue[old_id]  # Remove previous job from queue
                    logger.info(f"Cleared previous job {old_id} from queue")
                    
            # Create a fresh config with new UUID for this generation
            config = ReelsMakerConfig(
                job_id="".join(str(uuid4()).split("-")),
                video_type="narrator",
                prompt=prompt or sentence,  # This will use the current text input
                cwd=cwd,
                background_audio_url=background_audio_url,
                max_videos=max_videos,
                auto_download=auto_download,
                video_gen_config=VideoGeneratorConfig(
                    bg_color=str(bg_color),
                    fontsize=int(fontsize),
                    stroke_color=str(stroke_color),
                    stroke_width=int(stroke_width),
                    subtitles_position=str(subtitles_position),
                    text_color=str(text_color),
                    font_name=font_name,
                    threads=int(threads),
                    cpu_preset=cpu_preset,
                    aspect_ratio=aspect_ratio_value,
                    transition_effect=transition_effect,  # Add new parameters
                    platform_preset=platform_preset,
                    video_mood=video_mood
                ),
                synth_config=SynthConfig(
                    voice=str(voice),
                    voice_provider=(voice_provider.lower() if voice_provider else None) or 
                                  os.environ.get("VOICE_PROVIDER", "").lower() or 
                                  "tiktok",
                    sentence_pause=sentence_pause,
                    speech_rate=speech_rate,        # Add new parameters
                    voice_style=voice_style
                ),
            )
            
            # Add timestamp to the current job     
            now = time.time()       
            job_timestamps[config.job_id] = now
            
            # Reset timer when generate is clicked
            st.session_state["start_time"] = time.time()
            st.session_state["timer_running"] = True
            st.session_state["cancel_requested"] = False
            st.session_state["is_generating"] = True

            # Create placeholders for UI elements
            elapsed_placeholder = st.empty()
            cancel_placeholder = st.empty()
            
            # Show cancel button
            if cancel_placeholder.button("Cancel Generation", key="cancel_gen"):
                st.session_state["cancel_requested"] = True
                st.warning("Cancellation requested. Please wait for the current operation to complete...")
                
                # Add a direct job cancellation call
                if "last_job_id" in st.session_state and st.session_state["last_job_id"] in queue:
                    # Remove from queue to prevent further processing
                    del queue[st.session_state["last_job_id"]]
                    st.error("Generation cancelled!")
                    # Reset generation state
                    st.session_state["is_generating"] = False
                    st.session_state["timer_running"] = False
                    # Break out of the generation flow
                    st.stop()  # This will stop execution immediately

            # Add the JavaScript timer instead of the Python timer logic
            create_timer()  # Call it directly without trying to write it to a placeholder

            with st.spinner("Generating reels, this will take ~5mins or less..."):
                try:
                    # Add to queue and process
                    queue_id = config.job_id
                    st.session_state["last_job_id"] = queue_id  # Store for cancellation access
                    queue[queue_id] = config
                    
                    # Create and run reels maker
                    reels_maker = ReelsMaker(config)
                    output = await reels_maker.start(st_state=st.session_state)
                    
                    # Add log diagnostics after video generation
                    add_log_diagnostics(reels_maker)
                    
                    # Only show output if not cancelled
                    if output is not None and not st.session_state.get("cancel_requested", False):
                        # Display video and success message
                        st.success("Video generated successfully!")
                        st.session_state["timer_running"] = False  # Stop the timer
                        st.session_state["is_generating"] = False
                        
                        if output is not None and hasattr(output, 'video_file_path'):
                            video_path = output.video_file_path
                            st.session_state["last_video_path"] = video_path  # Store for persistence
                            
                            # Display video
                            st.video(video_path)
                            
                            # Create download button
                            if os.path.exists(video_path):
                                with open(video_path, "rb") as file:
                                    video_bytes = file.read()
                                    st.session_state["last_video_bytes"] = video_bytes
                                
                                st.download_button(
                                    "Download Reels", 
                                    video_bytes, 
                                    file_name=f"reels_{queue_id}.mp4",
                                    mime="video/mp4",
                                    key=f"download_button_{queue_id}"
                                )
                except Exception as e:
                    st.error(f"Generation failed: {e}")
                finally:
                    # Cleanup regardless of success or cancellation
                    st.session_state["is_generating"] = False
                    st.session_state["timer_running"] = False
                    
                    # Close loggers to ensure files are properly written
                    if 'reels_maker' in locals():
                        if hasattr(reels_maker, 'metrics_logger'):
                            reels_maker.metrics_logger.close()
                        if hasattr(reels_maker, 'match_logger'):
                            reels_maker.match_logger.close()

    if os.path.exists(cwd):
        try:
            # Check if reels_maker exists before using it
            if 'reels_maker' in locals() and hasattr(reels_maker, 'cleanup_temp_files'):
                reels_maker.cleanup_temp_files()
        except Exception as cleanup_error:
            logger.warning(f"Failed to clean up temporary files: {cleanup_error}")




if __name__ == "__main__":
    asyncio.run(main())
