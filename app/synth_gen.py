import os
import shutil
import json
import re
from typing import Literal, Optional
from enum import Enum
from uuid import uuid4

from app.utils.strings import log_attempt_number
from app.utils.strings import make_cuid
from elevenlabs import Voice, VoiceSettings, save
from elevenlabs.client import ElevenLabs
import httpx
from loguru import logger
from pydantic import BaseModel, Field

from app import tiktokvoice
from app.config import speech_cache_path
from app.utils.path_util import search_file, text_to_sha256_hash
from app.kokoro_service import kokoro_client
from tenacity import retry, stop_after_attempt, wait_fixed
from pydub import AudioSegment

VOICE_PROVIDER = Literal["kokoro", "elevenlabs", "tiktok", "openai", "airforce"]

class VoiceProvider(str, Enum):
    """Voice provider enum."""
    KOKORO = "kokoro"
    ELEVENLABS = "elevenlabs"
    TIKTOK = "tiktok"
    OPENAI = "openai"  # Add missing providers
    AIRFORCE = "airforce"

class SynthConfig(BaseModel):
    voice: str = "af_alloy"  # Default to a Kokoro voice
    voice_provider: VoiceProvider = VoiceProvider.KOKORO  # Default to Kokoro
    static_mode: bool = False
    """ if we're generating static audio for test """
    speech_rate: float = 1.0
    voice_style: str = "Neutral"

class SynthGenerator:
    def __init__(self, cwd: str, config: SynthConfig):
        self.config = config
        self.cwd = cwd
        self.cache_key: str | None = None
        self.base = os.path.join(self.cwd, "audio_chunks")
        os.makedirs(self.base, exist_ok=True)
        self.client = ElevenLabs(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
        )

    def set_speech_props(self):
        ky = (
            self.config.voice
            if self.config.static_mode
            else make_cuid(self.config.voice + "_")
        )
        self.speech_path = os.path.join(
            self.base,
            f"{self.config.voice_provider}_{ky}.mp3",
        )
        text_hash = text_to_sha256_hash(self.text)
        self.cache_key = f"{self.config.voice}_{text_hash}"

    async def generate_with_eleven(self, text: str) -> str:
        # Prioritize config.voice over environment variable
        voice_id = self.config.voice or os.environ.get("VOICE") or "21m00Tcm4TlvDq8ikWAM"
        # Add logging for debugging
        logger.info(f"Using ElevenLabs voice_id: {voice_id}")
        voice = Voice(
            voice_id=voice_id,
            settings=VoiceSettings(
                stability=0.71, similarity_boost=0.5, style=0.0, use_speaker_boost=True
            ),
        )
        audio = self.client.generate(
            text=text, voice=voice, model="eleven_multilingual_v2", stream=False
        )
        save(audio, self.speech_path)
        return self.speech_path

    # Update the is_valid_voice method
    def is_valid_voice(self, provider: VoiceProvider, voice: str) -> bool:
        """Check if the selected voice is valid for the current provider."""
        try:
            # First handle None or empty voices
            if not voice:
                logger.warning(f"Empty voice provided for {provider}. Using default voice.")
                return False
            if provider == VoiceProvider.KOKORO:
                # Get the list of valid Kokoro voices
                from app.kokoro_service import kokoro_client
                voice_options = kokoro_client.get_voices()
                return any(v["id"] == voice for v in voice_options) or voice == "af_alloy"
            elif provider == VoiceProvider.ELEVENLABS:
                # Basic validation for Elevenlabs voices
                return len(voice) > 0
            elif provider == VoiceProvider.TIKTOK:
                # Basic validation for TikTok voices
                tiktok_voices = ["en_us_001", "en_us_006", "en_us_007", "en_us_009", "en_us_010"]
                return voice in tiktok_voices
            else:
                # For other providers, just check if voice ID isn't empty
                return len(voice) > 0
        except Exception as e:
            logger.warning(f"Voice validation error: {e}, defaulting to allow voice")
            return True  # Allow it by default if validation fails

    async def generate_with_tiktok(self, text: str) -> str:
        try:
            result = tiktokvoice.tts(text, voice=str(self.config.voice), filename=self.speech_path)
            # Check if the file was actually created
            if not os.path.exists(self.speech_path) or os.path.getsize(self.speech_path) == 0:
                raise ValueError("TikTok voice generation failed to create audio file")
            return self.speech_path
        except Exception as e:
            logger.error(f"TikTok voice generation error: {e}")
            raise

    async def generate_with_kokoro(self, text: str) -> Optional[str]:
        """Generate speech using Kokoro Service."""
        logger.info(f"Generating speech with Kokoro Service, voice: {self.config.voice}")
        audio_bytes = await kokoro_client.create_speech(
            text=text,
            voice=self.config.voice
        )
        if audio_bytes:
            logger.info(f"Successfully generated speech with Kokoro, saving to {self.speech_path}")
            # Save the audio bytes to file
            os.makedirs(os.path.dirname(self.speech_path), exist_ok=True)
            with open(self.speech_path, "wb") as f:
                f.write(audio_bytes)
            return self.speech_path
        else:
            logger.error("Failed to generate audio with Kokoro, using fallback")
            return self._create_fallback_audio(text)

    def _create_fallback_audio(self, text: str) -> str:
        """Create a fallback audio file if TTS fails."""
        logger.warning("Creating fallback silent audio file")
        from pydub import AudioSegment
        # Create a silent audio file with duration based on text length
        duration_ms = max(2000, len(text) * 30)  # About 30ms per character, min 2 seconds
        # Generate silent audio
        silent_audio = AudioSegment.silent(duration=duration_ms)
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.speech_path), exist_ok=True)
        # Save to file
        silent_audio.export(self.speech_path, format="mp3")
        logger.info(f"Created fallback audio at: {self.speech_path}")
        return self.speech_path

    async def cache_speech(self, text: str):
        try:
            if not self.cache_key:
                logger.warning("Skipping speech cache because it is not set")
                return
            speech_path = os.path.join(speech_cache_path, f"{self.cache_key}.mp3")
            # Add check if source file exists before copying
            if os.path.exists(self.speech_path):
                shutil.copy2(self.speech_path, speech_path)
            else:
                logger.warning(f"Cannot cache speech: Source file {self.speech_path} does not exist")
        except Exception as e:
            logger.exception(f"Error in cache_speech(): {e}")

    async def generate_with_openai(self, text: str) -> str:
        raise NotImplementedError

    async def generate_with_airforce(self, text: str) -> str:
        url = f"https://api.airforce/get-audio?text={text}&voice={self.config.voice}"
        async with httpx.AsyncClient() as client:
            res = await client.get(url)
            save(res.content, self.speech_path)
        return self.speech_path

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(4), after=log_attempt_number) # type: ignore
    async def synth_speech(self, text: str) -> str:
        # Add text validation
        if not text or text.strip() == "":
            logger.warning("Empty text provided for speech synthesis. Using placeholder.")
            text = "No text was provided."  # Use a placeholder text instead
        # Add state tracking for failed providers
        if not hasattr(self, '_failed_providers'):
            self._failed_providers = set()
        try:
            # Skip if this provider has already failed
            if self.config.voice_provider.lower() in self._failed_providers:
                logger.warning(f"Skipping previously failed provider: {self.config.voice_provider}")
                # Use TikTok as fallback
                old_provider = self.config.voice_provider
                self.config.voice_provider = "tiktok"
                try:
                    return await self._synth_with_provider(text)
                finally:
                    self.config.voice_provider = old_provider
            # Attempt with configured provider
            return await self._synth_with_provider(text)
        except Exception as e:
            logger.error(f"Primary voice provider failed: {e}")
            # Mark this provider as failed
            self._failed_providers.add(self.config.voice_provider.lower())
            # Switch to fallback provider
            old_provider = self.config.voice_provider
            self.config.voice_provider = "tiktok"  # Use TikTok as fallback
            logger.info(f"Switching to fallback voice provider: {self.config.voice_provider}")
            try:
                return await self._synth_with_provider(text)
            finally:
                # Restore original setting
                self.config.voice_provider = old_provider

    async def _synth_with_provider(self, text: str) -> str:
        # Double-check that text is not empty
        if not text or text.strip() == "":
            text = "No text was provided."
        self.text = text
        self.set_speech_props()
        cached_speech = search_file(speech_cache_path, self.cache_key)
        if cached_speech:
            logger.info(f"Found speech in cache: {cached_speech}")
            shutil.copy2(cached_speech, self.speech_path)
            return cached_speech
        logger.info(f"Synthesizing text: {text}")
        # Use the enum directly for better type safety
        provider = self.config.voice_provider
        voice = self.config.voice
        # Validate the voice before proceeding
        if not self.is_valid_voice(provider, voice):
            logger.warning(f"Voice '{voice}' is not valid for provider {provider}. Using default voice.")
            # Use a default voice for the selected provider
            if provider == VoiceProvider.KOKORO:
                self.config.voice = "af_alloy"
            elif provider == VoiceProvider.ELEVENLABS:
                self.config.voice = "Rachel"
            elif provider == VoiceProvider.TIKTOK:
                self.config.voice = "en_us_001"
            elif provider == VoiceProvider.OPENAI:
                self.config.voice = "alloy"
            elif provider == VoiceProvider.AIRFORCE:
                self.config.voice = "default"
            # Update speech props with new voice
            self.set_speech_props()
        if provider == VoiceProvider.KOKORO:
            generator = self.generate_with_kokoro
        elif provider == VoiceProvider.ELEVENLABS:
            generator = self.generate_with_eleven  
        elif provider == VoiceProvider.TIKTOK:
            generator = self.generate_with_tiktok                        
        elif provider == VoiceProvider.OPENAI:
            generator = self.generate_with_openai
        elif provider == VoiceProvider.AIRFORCE:
            generator = self.generate_with_airforce 
        else:
            raise ValueError(f"Voice provider '{provider}' is not recognized")
        speech_path = await generator(text)
        await self.cache_speech(text)
        return self.speech_path