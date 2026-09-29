import asyncio
import numpy as np
import sounddevice as sd
import edge_tts
import tempfile
import os
from typing import AsyncGenerator, Optional, Callable
from dataclasses import dataclass
import logging

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    dtype: str = "int16"
    block_size: int = 512
    vad_threshold: float = 0.5
    min_speech_duration_ms: int = 250
    max_silence_duration_ms: int = 1000


class SileroVAD:
    def __init__(self, config: AudioConfig):
        self.config = config
        self.model = None
        self.get_speech_timestamps = None
        self._load_model()
    
    def _load_model(self):
        if not TORCH_AVAILABLE:
            logger.info("Torch not available, using energy-based VAD")
            self.model = None
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
            logger.warning(f"Could not load Silero VAD: {e}. Using simple energy-based VAD.")
            self.model = None
    
    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if self.model is None:
            return self._energy_based_vad(audio_chunk)
        
        try:
            audio_tensor = torch.from_numpy(audio_chunk.astype(np.float32) / 32768.0)
            speech_timestamps = self.get_speech_timestamps(audio_tensor, self.model, sampling_rate=self.config.sample_rate)
            return len(speech_timestamps) > 0
        except Exception:
            return self._energy_based_vad(audio_chunk)
    
    def _energy_based_vad(self, audio_chunk: np.ndarray) -> bool:
        energy = np.mean(np.abs(audio_chunk.astype(np.float32)))
        return energy > 500


class AudioRecorder:
    def __init__(self, config: AudioConfig):
        self.config = config
        self.vad = SileroVAD(config)
        self.stream = None
        self.is_recording = False
    
    async def record_until_silence(self, on_speech_start: Optional[Callable] = None, 
                                    on_speech_end: Optional[Callable] = None) -> np.ndarray:
        audio_buffer = []
        speech_detected = False
        silence_frames = 0
        max_silence_frames = int(self.config.max_silence_duration_ms / (self.config.block_size / self.config.sample_rate * 1000))
        min_speech_frames = int(self.config.min_speech_duration_ms / (self.config.block_size / self.config.sample_rate * 1000))
        speech_frames = 0
        
        def callback(indata, frames, time, status):
            nonlocal speech_detected, silence_frames, speech_frames
            if status:
                logger.warning(f"Audio callback status: {status}")
            
            chunk = indata.copy().flatten()
            is_speech = self.vad.is_speech(chunk)
            
            if is_speech:
                if not speech_detected:
                    speech_detected = True
                    if on_speech_start:
                        on_speech_start()
                speech_frames += 1
                silence_frames = 0
            else:
                if speech_detected:
                    silence_frames += 1
            
            if speech_detected:
                audio_buffer.append(chunk)
        
        self.stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            dtype=self.config.dtype,
            blocksize=self.config.block_size,
            callback=callback
        )
        
        self.stream.start()
        self.is_recording = True
        
        try:
            while self.is_recording:
                await asyncio.sleep(0.05)
                
                if speech_detected and speech_frames >= min_speech_frames and silence_frames >= max_silence_frames:
                    if on_speech_end:
                        on_speech_end()
                    break
        finally:
            self.stream.stop()
            self.stream.close()
            self.is_recording = False
        
        if audio_buffer:
            return np.concatenate(audio_buffer)
        return np.array([], dtype=np.int16)
    
    def stop(self):
        self.is_recording = False


class EdgeTTSEngine:
    def __init__(self, voice: str = "id-ID-ArdiNeural"):
        self.voice = voice
        self._rate = "+0%"
        self._volume = "+0%"
    
    async def synthesize(self, text: str, output_file: Optional[str] = None) -> str:
        if output_file is None:
            fd, output_file = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
        
        communicate = edge_tts.Communicate(text, self.voice, rate=self._rate, volume=self._volume)
        await communicate.save(output_file)
        return output_file
    
    async def synthesize_streaming(self, text: str) -> AsyncGenerator[bytes, None]:
        communicate = edge_tts.Communicate(text, self.voice, rate=self._rate, volume=self._volume)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
    
    def play_audio(self, file_path: str):
        import subprocess
        try:
            subprocess.run(["start", "", file_path], shell=True, check=True)
        except Exception as e:
            logger.error(f"Failed to play audio: {e}")


class MockSTTEngine:
    def __init__(self):
        self.transcriptions = [
            "halo",
            "cek saldo rekening 1234567890",
            "transaksi terakhir rekening 9876543210",
            "blokir kartu 4567890123456789 o t p 123456",
            "bantuan",
        ]
        self.index = 0
    
    async def transcribe(self, audio_data: np.ndarray) -> str:
        await asyncio.sleep(0.1)
        if self.index < len(self.transcriptions):
            result = self.transcriptions[self.index]
            self.index += 1
            return result
        return ""


class GroqSTTEngine:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
    
    async def transcribe(self, audio_data: np.ndarray) -> str:
        if self.client is None:
            from groq import AsyncGroq
            self.client = AsyncGroq(api_key=self.api_key)
        
        import io
        import wave
        
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio_data.tobytes())
        
        buffer.seek(0)
        
        try:
            response = await self.client.audio.transcriptions.create(
                file=("audio.wav", buffer, "audio/wav"),
                model="whisper-large-v3-turbo",
                language="id",
                response_format="text"
            )
            return response
        except Exception as e:
            logger.error(f"Groq STT error: {e}")
            return ""


class VoicePipeline:
    def __init__(self, agent, config: Optional[AudioConfig] = None, 
                 stt_engine=None, tts_engine=None):
        self.agent = agent
        self.config = config or AudioConfig()
        self.recorder = AudioRecorder(self.config)
        self.stt = stt_engine or MockSTTEngine()
        self.tts = tts_engine or EdgeTTSEngine()
        self.is_running = False
    
    async def run_voice_loop(self):
        self.is_running = True
        print("\n" + "="*60)
        print("Voice Pipeline Started - Speak in Indonesian")
        print("Say 'keluar' or 'exit' to quit")
        print("="*60 + "\n")
        
        while self.is_running:
            print("\n[Listening...]")
            
            def on_speech_start():
                print("[Speech detected]")
            
            def on_speech_end():
                print("[Speech ended, processing...]")
            
            audio_data = await self.recorder.record_until_silence(
                on_speech_start=on_speech_start,
                on_speech_end=on_speech_end
            )
            
            if len(audio_data) == 0:
                print("[No speech detected]")
                continue
            
            print("[Transcribing...]")
            text = await self.stt.transcribe(audio_data)
            
            if not text:
                print("[No transcription]")
                continue
            
            print(f"You said: {text}")
            
            if text.lower().strip() in ["keluar", "exit", "quit", "selesai"]:
                print("[Exiting...]")
                break
            
            print("[Processing...]")
            response = await self.agent.process(text)
            
            print(f"Agent: {response.text}")
            
            if response.requires_tts:
                print("[Speaking...]")
                audio_file = await self.tts.synthesize(response.text)
                self.tts.play_audio(audio_file)
                await asyncio.sleep(1)
                try:
                    os.remove(audio_file)
                except:
                    pass
        
        print("\nVoice pipeline stopped.")
    
    def stop(self):
        self.is_running = False
        self.recorder.stop()


async def main():
    from app.services.agent import BankingAgent
    
    agent = BankingAgent()
    pipeline = VoicePipeline(agent)
    
    try:
        await pipeline.run_voice_loop()
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())