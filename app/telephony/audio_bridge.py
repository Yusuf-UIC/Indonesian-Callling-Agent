"""
Audio Bridge for Telephony Integration
=======================================
Handles audio format conversion between telephony formats (8kHz mu-law)
and internal processing formats (16kHz PCM linear).
"""

import base64
import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AudioBridge:
    """
    Handles audio format conversion between telephony (8kHz mu-law) 
    and internal processing formats (16kHz PCM linear).
    """
    
    # Telephony constants
    TELEPHONY_SAMPLE_RATE = 8000
    INTERNAL_SAMPLE_RATE = 16000
    
    def __init__(self):
        """Initialize audio bridge with mu-law conversion tables."""
        self._mulaw_table = self._generate_mulaw_table()
        self._inv_mulaw_table = self._generate_inv_mulaw_table()
    
    def _generate_mulaw_table(self) -> np.ndarray:
        """Generate mu-law encoding table (linear PCM to mu-law)."""
        mu = 255.0
        table = np.zeros(65536, dtype=np.uint8)
        for i in range(65536):
            # Convert 16-bit signed to normalized float
            sample = (i - 32768) / 32768.0
            # Mu-law encoding
            sign = 1 if sample >= 0 else -1
            abs_sample = abs(sample)
            compressed = sign * (np.log(1 + mu * abs_sample) / np.log(1 + mu))
            # Convert to 8-bit mu-law
            table[i] = int((compressed + 1) * 127.5)
        return table
    
    def _generate_inv_mulaw_table(self) -> np.ndarray:
        """Generate inverse mu-law decoding table (mu-law to linear PCM)."""
        mu = 255.0
        table = np.zeros(256, dtype=np.int16)
        for i in range(256):
            # Mu-law to linear
            mu_law = (i - 127.5) / 127.5
            sign = 1 if mu_law >= 0 else -1
            abs_mu = abs(mu_law)
            linear = sign * ((1 + mu) ** abs_mu - 1) / mu
            table[i] = int(linear * 32767)
        return table
    
    def mulaw_encode(self, pcm_data: np.ndarray) -> bytes:
        """
        Encode 16-bit linear PCM to 8-bit mu-law.
        
        Args:
            pcm_data: 16-bit linear PCM audio data (int16 numpy array)
            
        Returns:
            Mu-law encoded audio as bytes
        """
        if pcm_data.dtype != np.int16:
            pcm_data = pcm_data.astype(np.int16)
        
        # Use lookup table for fast encoding
        indices = pcm_data + 32768
        mulaw_data = self._mulaw_table[indices]
        return mulaw_data.tobytes()
    
    def mulaw_decode(self, mulaw_data: bytes) -> np.ndarray:
        """
        Decode 8-bit mu-law to 16-bit linear PCM.
        
        Args:
            mulaw_data: Mu-law encoded audio data (bytes)
            
        Returns:
            16-bit linear PCM audio data (int16 numpy array)
        """
        mulaw_array = np.frombuffer(mulaw_data, dtype=np.uint8)
        pcm_data = self._inv_mulaw_table[mulaw_array]
        return pcm_data.astype(np.int16)
    
    def resample_audio(self, audio_data: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
        """
        Resample audio from one sample rate to another using linear interpolation.
        
        Args:
            audio_data: Audio data as numpy array (int16)
            from_rate: Source sample rate
            to_rate: Target sample rate
            
        Returns:
            Resampled audio data
        """
        if from_rate == to_rate:
            return audio_data
        
        # Convert to float for interpolation
        audio_float = audio_data.astype(np.float32) / 32768.0
        
        # Calculate resampling ratio
        ratio = to_rate / from_rate
        input_len = len(audio_float)
        output_len = int(input_len * ratio)
        
        # Linear interpolation
        input_indices = np.arange(input_len)
        output_indices = np.arange(output_len) / ratio
        
        # Interpolate
        resampled = np.interp(output_indices, input_indices, audio_float)
        
        # Convert back to int16
        resampled = np.clip(resampled * 32768, -32768, 32767).astype(np.int16)
        
        return resampled
    
    def process_incoming_audio(self, mulaw_base64: str) -> np.ndarray:
        """
        Process incoming Twilio audio (base64 encoded mu-law) to internal format.
        
        Args:
            mulaw_base64: Base64 encoded mu-law audio from Twilio
            
        Returns:
            16kHz PCM linear audio as numpy array (int16)
        """
        try:
            # Decode base64
            mulaw_bytes = base64.b64decode(mulaw_base64)
            
            # Decode mu-law to 8kHz PCM
            pcm_8khz = self.mulaw_decode(mulaw_bytes)
            
            # Resample from 8kHz to 16kHz
            pcm_16khz = self.resample_audio(pcm_8khz, self.TELEPHONY_SAMPLE_RATE, self.INTERNAL_SAMPLE_RATE)
            
            return pcm_16khz
            
        except Exception as e:
            logger.error(f"Error processing incoming audio: {e}")
            return np.array([], dtype=np.int16)
    
    def prepare_outgoing_audio(self, pcm_16khz: np.ndarray) -> str:
        """
        Convert internal 16kHz PCM to Twilio-compatible base64 mu-law.
        
        Args:
            pcm_16khz: 16kHz PCM linear audio as numpy array (int16)
            
        Returns:
            Base64 encoded mu-law audio for Twilio
        """
        try:
            # Resample from 16kHz to 8kHz
            pcm_8khz = self.resample_audio(pcm_16khz, self.INTERNAL_SAMPLE_RATE, self.TELEPHONY_SAMPLE_RATE)
            
            # Encode to mu-law
            mulaw_bytes = self.mulaw_encode(pcm_8khz)
            
            # Encode to base64
            mulaw_base64 = base64.b64encode(mulaw_bytes).decode('utf-8')
            
            return mulaw_base64
            
        except Exception as e:
            logger.error(f"Error preparing outgoing audio: {e}")
            return ""

    def _audio_to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        """
        Convert numpy audio array to WAV bytes for STT processing.
        
        Args:
            audio_data: Audio data as numpy array (int16)
            
        Returns:
            WAV file as bytes
        """
        import io
        import wave
        
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.INTERNAL_SAMPLE_RATE)
            wf.writeframes(audio_data.tobytes())
        return buffer.getvalue()


# Global audio bridge instance
audio_bridge = AudioBridge()