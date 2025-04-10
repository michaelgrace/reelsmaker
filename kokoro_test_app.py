# run with docker 
#  docker-compose -f docker-compose-test.yml down && docker-compose -f docker-compose-test.yml up --build

"""Simple Streamlit app to test TTS integration."""
import os
import json
import re
import asyncio
import streamlit as st
import aiohttp
from loguru import logger
import sys
import tempfile
import uuid

# Configure logger
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

# Basic app title
st.title("TTS Test App")
st.subheader("Test text-to-speech service")

# Get current provider from environment or default to "kokoro"
voice_provider = os.environ.get("VOICE_PROVIDER", "kokoro").lower()

# Load voices from JSON file based on provider
def load_voices(provider=voice_provider):
    """Load voices for the specified provider."""
    try:
        # Build path based on provider
        voices_path = os.path.join(os.path.dirname(__file__), "data", f"{provider}_voices.json")
        if not os.path.exists(voices_path):
            # Try alternate location
            voices_path = f"/app/{provider}_voices.json"
        
        if os.path.exists(voices_path):
            logger.info(f"Loading {provider} voices from {voices_path}")
            with open(voices_path, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "voices" in data:
                    return data["voices"]
                return data
        else:
            logger.warning(f"Voices file not found at {voices_path}")
            
    except Exception as e:
        logger.error(f"Error loading {provider} voices: {e}")
    
    # Provider-specific fallbacks
    if provider == "kokoro":
        return ["af_heart", "af_nova", "af_shimmer", "am_onyx", "am_echo"]
    elif provider == "elevenlabs":
        return ["Rachel", "Domi", "Bella", "Antoni", "Arnold"]
    elif provider == "tiktok":
        return ["en_us_001", "en_us_006", "en_us_007", "en_us_009", "en_us_010"]
    else:
        return ["default_voice"]

# Input fields
available_voices = load_voices()
text_input = st.text_area("Enter text to synthesize:", value="Hello, this is a test of the text to speech service.")
voice_input = st.selectbox(f"Select {voice_provider} voice:", available_voices)

# Add speech rate control (only for Kokoro provider)
speech_rate = 1.0
if voice_provider == "kokoro":
    speech_rate = st.number_input(
        "Speech rate:",
        min_value=0.26,
        max_value=4.0,
        value=0.8,
        step=0.02,
        help="Controls the speed of speech (0.25 = slower, 4.0 = faster)"
    )

test_button = st.button("Generate Speech")

# Function to generate speech
async def generate_speech(text, voice, speech_rate=1.0):
    # Get service URL based on provider
    service_url = os.environ.get(f"{voice_provider.upper()}_SERVICE_URL")
    
    if not service_url:
        # Provider-specific fallbacks
        if voice_provider == "kokoro":
            service_url = "http://kokoro_service:8880"
        elif voice_provider == "elevenlabs":
            service_url = "https://api.elevenlabs.io/v1"
        elif voice_provider == "tiktok":
            service_url = "https://tiktok-tts.weilnet.workers.dev/api/generation"
        else:
            st.error(f"No service URL configured for provider: {voice_provider}")
            return None, f"No service URL configured for provider: {voice_provider}"
    
    logger.info(f"Using {voice_provider} service URL: {service_url}")
    
    # Create provider-specific payload
    if voice_provider == "kokoro":
        payload = {
            "input": text, 
            "voice": voice,
            "speed": speech_rate  # Add speech rate parameter
        }
        endpoint = "/v1/audio/speech"
    elif voice_provider == "elevenlabs":
        payload = {"text": text, "voice_id": voice}
        endpoint = "/text-to-speech"
    elif voice_provider == "tiktok":
        payload = {"text": text, "voice": voice}
        endpoint = ""  # TikTok endpoints are complete URLs
    else:
        # Generic fallback
        payload = {"text": text, "voice": voice}
        endpoint = "/tts"
    
    # Create temp file for output WITH .mp3 extension
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f"tts_test_{uuid.uuid4().hex}.mp3")
    
    st.write(f"Sending request to {voice_provider.title()} TTS service...")
    logger.info(f"Sending TTS request to {service_url}{endpoint}")
    logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{service_url}{endpoint}",
                json=payload
            ) as response:
                status = response.status
                
                if status == 200:
                    logger.info("Received 200 OK response")
                    # Check content type to determine format
                    content_type = response.headers.get('Content-Type', '')
                    logger.info(f"Content type: {content_type}")
                    
                    if 'application/json' in content_type:
                        # Try to process as JSON
                        try:
                            response_data = await response.json()
                            logger.debug(f"Response data: {response_data}")
                            
                            if "audio" in response_data:
                                logger.info("Response contains direct audio data")
                                import base64
                                audio_b64 = response_data["audio"]
                                audio_bytes = base64.b64decode(audio_b64)
                            elif "download_link" in response_data:
                                logger.info(f"Response contains download link")
                                download_url = response_data["download_link"]
                                
                                async with session.get(download_url) as dl_response:
                                    if dl_response.status == 200:
                                        audio_bytes = await dl_response.read()
                                    else:
                                        return None, f"Download failed: {dl_response.status}"
                            else:
                                return None, f"Unexpected JSON format: {response_data}"
                        except json.JSONDecodeError:
                            return None, "Failed to parse JSON response"
                    else:
                        # Directly use the binary response as audio data
                        logger.info("Response is direct audio data (not JSON)")
                        audio_bytes = await response.read()
                    
                    # Save audio to file
                    with open(output_path, "wb") as f:
                        f.write(audio_bytes)
                    
                    file_size = os.path.getsize(output_path)
                    logger.info(f"Saved audio to {output_path} ({file_size/1024:.2f} KB)")
                    
                    if file_size > 0:
                        return output_path, None
                    else:
                        return None, "Generated audio file is empty"
                
                else:
                    error_text = await response.text()
                    logger.error(f"Request failed: {status} - {error_text}")
                    return None, f"Request failed: {status} - {error_text}"
    
    except Exception as e:
        logger.exception(f"Error: {e}")
        return None, f"Error: {e}"

# Handle button click
if test_button:
    with st.spinner("Generating speech..."):
        # Run the async function
        audio_path, error = asyncio.run(generate_speech(text_input, voice_input, speech_rate))
        
        if error:
            st.error(f"Failed to generate speech: {error}")
        else:
            # Play the audio
            st.success(f"Speech generated successfully!")
            
            # Read the file and display it
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            
            # Get the filename from the path
            filename = os.path.basename(audio_path)
            
            # Display audio without explicit filename
            st.audio(audio_bytes, format="audio/mp3")
            
            # Show file details
            file_size = os.path.getsize(audio_path) / 1024
            st.info(f"Audio file size: {file_size:.2f} KB | Filename: {filename}")
            
            # Add a download button with proper filename
            st.download_button(
                label="Download MP3",
                data=audio_bytes,
                file_name=filename,
                mime="audio/mp3"
            )