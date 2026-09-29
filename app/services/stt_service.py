import asyncio
import io
import time
import wave
from abc import ABC, abstractmethod
from typing import Optional
import numpy as np
import logging

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    from groq import AsyncGroq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

logger = logging.getLogger(__name__)


class BaseSTTEngine(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes) -> str:
        pass


class SileroVAD:
    def __init__(self, sample_rate: int = 16000, threshold: float = 0.5):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.model = None
        self._load_model()

    def _load_model(self):
        if not TORCH_AVAILABLE:
            logger.warning("Torch not available, Silero VAD disabled")
            return

        try:
            self.model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True
            )
            self.get_speech_timestamps = utils[0]
            logger.info("Silero VAD model loaded successfully")
        except Exception as e:
            logger.warning(f"Could not load Silero VAD: {e}")
            self.model = None

    def get_speech_segments(self, audio: np.ndarray) -> list:
        if self.model is None:
            return [(0, len(audio))]

        try:
            audio_tensor = torch.from_numpy(audio.astype(np.float32) / 32768.0)
            speech_timestamps = self.get_speech_timestamps(
                audio_tensor, self.model, sampling_rate=self.sample_rate
            )
            return [(ts['start'], ts['end']) for ts in speech_timestamps]
        except Exception as e:
            logger.error(f"VAD error: {e}")
            return [(0, len(audio))]

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        audio_float = audio_chunk.astype(np.float32)
        rms_energy = np.sqrt(np.mean(audio_float ** 2)) if len(audio_float) > 0 else 0.0

        if rms_energy > 300:
            return True

        if self.model is None:
            return rms_energy > 300

        try:
            audio_tensor = torch.from_numpy(audio_float / 32768.0)
            speech_prob = self.model(audio_tensor, self.sample_rate).item()
            return speech_prob > self.threshold
        except Exception:
            return rms_energy > 300


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        dtype: str = "int16",
        block_size: int = 512,
        vad_threshold: float = 0.5,
        min_speech_duration_ms: int = 300,
        max_silence_duration_ms: int = 1200,
        device: Optional[int] = None,
        hostapi: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self.block_size = block_size
        self.vad = SileroVAD(sample_rate, vad_threshold)
        self.min_speech_frames = int(min_speech_duration_ms / (block_size / sample_rate * 1000))
        self.max_silence_frames = int(max_silence_duration_ms / (block_size / sample_rate * 1000))
        self.device = device
        self.hostapi = hostapi
        self._use_sounddevice = False
        self._test_backends()

    def _test_backends(self):
        """Test audio backends in order of preference."""
        self._test_sounddevice()
        if not self._use_sounddevice:
            # sounddevice failed, try PyAudio
            if self._test_pyaudio():
                logger.info("Using PyAudio for audio input")
                self._use_sounddevice = False  # Explicitly use PyAudio
            else:
                raise RuntimeError(
                    "No working audio input backend found. "
                    "Both sounddevice and PyAudio failed. "
                    "Check microphone permissions and audio drivers."
                )
        else:
            logger.info("Using sounddevice for audio input")

    def _test_sounddevice(self):
        """Test if sounddevice works with the given device/hostapi."""
        if not SOUNDDEVICE_AVAILABLE:
            return
        try:
            # Test opening a stream with the configured parameters
            test_kwargs = {
                "samplerate": self.sample_rate,
                "channels": self.channels,
                "dtype": self.dtype,
                "blocksize": self.block_size,
            }
            if self.device is not None:
                test_kwargs["device"] = self.device
            if self.hostapi is not None:
                test_kwargs["hostapi"] = self.hostapi
            
            # Try to open and immediately close a test stream
            with sd.InputStream(**test_kwargs) as stream:
                pass
            self._use_sounddevice = True
            logger.info("sounddevice test successful")
        except Exception as e:
            logger.warning(f"sounddevice test failed: {e}")
            self._use_sounddevice = False

    def _test_pyaudio(self) -> bool:
        """Test if PyAudio works with the given device."""
        if not PYAUDIO_AVAILABLE:
            return False
        try:
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.block_size,
                input_device_index=self.device,
            )
            stream.start_stream()
            stream.stop_stream()
            stream.close()
            p.terminate()
            logger.info("PyAudio test successful")
            return True
        except Exception as e:
            logger.warning(f"PyAudio test failed: {e}")
            return False

    async def record_until_silence(self) -> np.ndarray:
        if self._use_sounddevice:
            return await self._record_sounddevice()
        else:
            return await self._record_pyaudio()

    async def _record_sounddevice(self) -> np.ndarray:
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice not installed")

        audio_buffer = []
        speech_detected = False
        silence_frames = 0
        speech_frames = 0
        start_time = time.time()
        max_duration_sec = 10.0
        no_speech_timeout_sec = 7.0
        stream = None

        def callback(indata, frames, time_info, status):
            nonlocal speech_detected, silence_frames, speech_frames
            if status and not status.input_overflow:
                logger.warning(f"Audio callback status: {status}")

            chunk = indata.copy().flatten()
            is_speech = self.vad.is_speech(chunk)

            if is_speech:
                if not speech_detected:
                    speech_detected = True
                    print("\n[Speech detected, recording...]", end="", flush=True)
                speech_frames += 1
                silence_frames = 0
            else:
                if speech_detected:
                    silence_frames += 1

            if speech_detected:
                audio_buffer.append(chunk)

        if self.hostapi is not None:
            try:
                sd.default.hostapi = self.hostapi
            except Exception as e:
                logger.warning(f"Could not set sounddevice hostapi: {e}")

        stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
            blocksize=self.block_size,
            callback=callback,
            device=self.device,
        )

        stream.start()

        try:
            while True:
                await asyncio.sleep(0.05)
                elapsed = time.time() - start_time

                if speech_detected and speech_frames >= self.min_speech_frames and silence_frames >= self.max_silence_frames:
                    print(" [Silence detected, done]")
                    break

                if speech_detected and elapsed >= max_duration_sec:
                    print(" [Max duration reached, done]")
                    break

                if not speech_detected and elapsed >= no_speech_timeout_sec:
                    break
        finally:
            stream.stop()
            stream.close()

        if audio_buffer:
            return np.concatenate(audio_buffer)
        return np.array([], dtype=np.int16)

    async def _record_pyaudio(self) -> np.ndarray:
        if not PYAUDIO_AVAILABLE:
            raise RuntimeError("Neither sounddevice nor pyaudio available")

        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.block_size,
            input_device_index=self.device,
        )

        audio_buffer = []
        speech_detected = False
        silence_frames = 0
        speech_frames = 0

        try:
            while True:
                data = stream.read(self.block_size, exception_on_overflow=False)
                chunk = np.frombuffer(data, dtype=np.int16)
                is_speech = self.vad.is_speech(chunk)

                if is_speech:
                    if not speech_detected:
                        speech_detected = True
                    speech_frames += 1
                    silence_frames = 0
                else:
                    if speech_detected:
                        silence_frames += 1

                if speech_detected:
                    audio_buffer.append(chunk)

                if speech_detected and speech_frames >= self.min_speech_frames and silence_frames >= self.max_silence_frames:
                    break

                await asyncio.sleep(0.01)
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

        if audio_buffer:
            return np.concatenate(audio_buffer)
        return np.array([], dtype=np.int16)

    def _energy_vad(self, chunk: np.ndarray) -> bool:
        energy = np.mean(np.abs(chunk.astype(np.float32)))
        return energy > 500


class GroqSTTEngine(BaseSTTEngine):
    def __init__(self, api_key: str, model: str = "whisper-large-v3-turbo"):
        if not GROQ_AVAILABLE:
            raise RuntimeError("groq SDK not installed")
        self.client = AsyncGroq(api_key=api_key)
        self.model = model

    async def transcribe(self, audio_bytes: bytes) -> str:
        try:
            response = await self.client.audio.transcriptions.create(
                file=("audio.wav", audio_bytes, "audio/wav"),
                model=self.model,
                language="id",
                response_format="text"
            )
            return response.strip()
        except Exception as e:
            logger.error(f"Groq STT error: {e}")
            return ""


class FasterWhisperSTTEngine(BaseSTTEngine):
    def __init__(self, model_size: str = "base", device: str = "cpu"):
        if not FASTER_WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper not installed")
        self.model = WhisperModel(model_size, device=device, compute_type="int8")

    async def transcribe(self, audio_bytes: bytes) -> str:
        try:
            with io.BytesIO(audio_bytes) as audio_file:
                segments, info = self.model.transcribe(audio_file, language="id", beam_size=5)
                text = " ".join([segment.text for segment in segments])
                return text.strip()
        except Exception as e:
            logger.error(f"FasterWhisper STT error: {e}")
            return ""


class STTService:
    def __init__(
        self,
        groq_api_key: Optional[str] = None,
        use_local_fallback: bool = True,
        local_model_size: str = "base",
        input_device: Optional[int] = None,
        input_hostapi: Optional[int] = None,
    ):
        self.recorder = AudioRecorder(device=input_device, hostapi=input_hostapi)
        self.groq_engine = None
        self.local_engine = None

        if groq_api_key and GROQ_AVAILABLE:
            self.groq_engine = GroqSTTEngine(groq_api_key)
            logger.info("Groq STT engine initialized")

        if use_local_fallback and FASTER_WHISPER_AVAILABLE:
            self.local_engine = FasterWhisperSTTEngine(model_size=local_model_size, device="cpu")
            logger.info(f"Local FasterWhisper STT engine initialized (model: {local_model_size})")

        if not self.groq_engine and not self.local_engine:
            raise RuntimeError("No STT engine available. Install groq or faster-whisper.")

    async def listen_and_transcribe(self) -> str:
        print("[Listening...]")
        audio_data = await self.recorder.record_until_silence()

        if len(audio_data) == 0:
            print("[No speech detected]")
            return ""

        audio_bytes = self._audio_to_wav_bytes(audio_data)

        if self.groq_engine:
            print("[Transcribing with Groq...]")
            text = await self.groq_engine.transcribe(audio_bytes)
            if text:
                return text
            print("[Groq failed, trying local...]")

        if self.local_engine:
            print("[Transcribing locally...]")
            text = await self.local_engine.transcribe(audio_bytes)
            return text

        return ""

    def _audio_to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data.tobytes())
        return buffer.getvalue()


async def main():
    import os
    groq_key = os.getenv("GROQ_API_KEY")
    stt = STTService(groq_api_key=groq_key, use_local_fallback=True)
    text = await stt.listen_and_transcribe()
    print(f"Transcribed: {text}")


if __name__ == "__main__":
    asyncio.run(main())