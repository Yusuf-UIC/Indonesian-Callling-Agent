import asyncio
import os
import sys
import signal
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path when running this script directly
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.services.agent_solace import BankingAgentSolace
from app.services.stt_service import STTService, AudioRecorder
from app.services.semantic_intent import semantic_intent_classifier
from app.core.formatters import clean_text_for_tts

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

import tempfile
import threading


class MockSTTEngine:
    """Mock STT engine for testing without microphone."""
    
    def __init__(self):
        self.transcriptions = [
            "halo",
            "cek saldo rekening 1234567890",
            "transaksi terakhir rekening 9876543210",
            "blokir kartu 4567890123456791 o t p 123456",
            "bantuan",
            "keluar",
        ]
        self.index = 0
    
    async def transcribe(self, audio_bytes: bytes = b"") -> str:
        await asyncio.sleep(0.5)
        if self.index < len(self.transcriptions):
            result = self.transcriptions[self.index]
            self.index += 1
            return result
        return "keluar"


class TTSPlayer:
    """Text-to-Speech player using Edge-TTS with pygame or system playback."""
    
    def __init__(self, voice: str = "id-ID-ArdiNeural"):
        self.voice = voice
        self.temp_dir = tempfile.gettempdir()
        self._playback_thread: Optional[threading.Thread] = None
        self._stop_playback = threading.Event()
        self._current_file: Optional[str] = None
    
    async def synthesize(self, text: str) -> str:
        if not EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts not installed")
        
        output_file = os.path.join(self.temp_dir, f"tts_{hash(text) % 100000}.mp3")
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(output_file)
        return output_file
    
    def play(self, audio_file: str):
        """Play audio file, interrupting any current playback."""
        self.stop()
        self._current_file = audio_file
        self._stop_playback.clear()
        
        if PYGAME_AVAILABLE:
            self._play_pygame(audio_file)
        else:
            self._play_system(audio_file)
    
    def stop(self):
        """Stop current playback."""
        self._stop_playback.set()
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=1.0)
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.quit()
            except:
                pass
    
    def _play_pygame(self, audio_file: str):
        def _play():
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(audio_file)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy() and not self._stop_playback.is_set():
                    pygame.time.wait(50)
            except Exception as e:
                print(f"[Pygame playback error: {e}]")
            finally:
                try:
                    pygame.mixer.quit()
                except:
                    pass
        
        self._playback_thread = threading.Thread(target=_play, daemon=True)
        self._playback_thread.start()
    
    def _play_system(self, audio_file: str):
        def _play():
            try:
                import subprocess
                if sys.platform == "win32":
                    subprocess.run(["start", "", audio_file], shell=True, check=True)
                elif sys.platform == "darwin":
                    subprocess.run(["afplay", audio_file], check=True)
                else:
                    subprocess.run(["aplay", audio_file], check=True)
            except Exception as e:
                print(f"[System playback error: {e}]")
        
        self._playback_thread = threading.Thread(target=_play, daemon=True)
        self._playback_thread.start()


class VoiceCLI:
    """Interactive voice CLI for the Indonesian Banking IVR."""
    
    def __init__(
        self,
        tts_voice: str = "id-ID-ArdiNeural",
        use_mock_stt: bool = True,
        groq_api_key: Optional[str] = None,
        input_device: Optional[int] = None,
        input_hostapi: Optional[int] = None,
        use_mock_solace: bool = True,
        use_mqtt_solace: bool = False,
        llm_provider: Optional[str] = None,
    ):
        provider = llm_provider or os.getenv("LLM_PROVIDER", "mock")
        self.agent = BankingAgentSolace(
            use_mock=use_mock_solace,
            use_mqtt=use_mqtt_solace,
            llm_provider=provider,
        )
        self.use_mock_stt = use_mock_stt
        self.groq_api_key = groq_api_key
        self.input_device = input_device
        self.input_hostapi = input_hostapi
        
        # Initialize STT
        if use_mock_stt:
            self.stt_service = None
            self.mock_stt = MockSTTEngine()
        else:
            try:
                self.stt_service = STTService(
                    groq_api_key=groq_api_key,
                    use_local_fallback=True,
                    input_device=input_device,
                    input_hostapi=input_hostapi,
                )
                self.mock_stt = None
            except RuntimeError as e:
                print(f"[Warning] STT service initialization failed: {e}")
                print("Falling back to mock STT mode")
                self.stt_service = None
                self.mock_stt = MockSTTEngine()
        
        self.tts_player = TTSPlayer(voice=tts_voice)
        self.running = False
        self._interrupted = False
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        print("\n[Interrupt received]")
        self._interrupted = True
        self.tts_player.stop()
        self.running = False
    
    async def start(self):
        """Start the interactive voice CLI."""
        self.running = True
        
        # Initialize BankingAgentSolace and Solace handlers
        await self.agent.initialize()
        
        mode_str = "Mock STT" if self.use_mock_stt or self.stt_service is None else "Live STT"
        print("\n" + "=" * 60)
        print(f"INDONESIA IVR VOICE ASSISTANT ({mode_str} Mode)")
        print("=" * 60)
        if self.use_mock_stt or self.stt_service is None:
            print("This demo uses mock STT for testing without microphone.")
            print("Pre-recorded Indonesian phrases will be simulated.")
        else:
            print("Live microphone mode active. Speak naturally in Bahasa Indonesia.")
            print("Recording stops automatically after 1.2s of silence.")
        print("Commands: 'exit', 'keluar', 'quit', 'selesai' to quit")
        print("Press Ctrl+C to interrupt TTS or exit")
        print("=" * 60 + "\n")
        
        # Initial greeting
        greeting = "Selamat datang di layanan pelanggan kami! Ada yang bisa saya bantu hari ini?"
        await self._speak(greeting)
        
        try:
            while self.running and not self._interrupted:
                print("\n[LISTENING...]")
                
                try:
                    if self.stt_service:
                        text = await self.stt_service.listen_and_transcribe()
                    elif self.mock_stt:
                        text = await self.mock_stt.transcribe()
                    else:
                        text = await self._get_text_input()
                except Exception as e:
                    print(f"[STT Error: {e}]")
                    await asyncio.sleep(1)
                    continue
                
                if self._interrupted:
                    break
                
                if not text:
                    print("[No speech detected]")
                    continue
                
                print(f"Anda: {text}")
                
                # Check for exit commands
                if text.lower().strip() in ["exit", "keluar", "quit", "selesai"]:
                    await self._speak("Terima kasih telah menggunakan layanan kami. Sampai jumpa!")
                    break
                
                # Process with agent
                response = await self.agent.process(text)
                print(f"Asisten: {response.text}")
                
                # Speak response
                if response.requires_tts:
                    await self._speak(response.text)
                
                # Small delay to prevent tight loop
                await asyncio.sleep(0.1)
        
        except KeyboardInterrupt:
            print("\n\n[Interrupted by user]")
            await self._speak("Terima kasih. Sampai jumpa!")
        except Exception as e:
            print(f"\n[Error: {e}]")
        finally:
            await self.agent.close()
            self.tts_player.stop()
    
    async def _get_text_input(self) -> str:
        """Get text input from console (fallback)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: input("Masukkan teks (atau 'exit' untuk keluar): ").strip())
    
    async def _speak(self, text: str):
        """Synthesize and play TTS."""
        if self._interrupted:
            return
        
        print("[Speaking...]")
        try:
            cleaned_text = clean_text_for_tts(text)
            audio_file = await self.tts_player.synthesize(cleaned_text)
            self.tts_player.play(audio_file)
            
            # Wait for playback to complete or be interrupted
            while self.tts_player._playback_thread and self.tts_player._playback_thread.is_alive():
                if self._interrupted:
                    self.tts_player.stop()
                    break
                await asyncio.sleep(0.1)
            
            # Clean up temp file
            try:
                os.remove(audio_file)
            except:
                pass
        except Exception as e:
            print(f"[TTS Error: {e}]")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Indonesia IVR Voice Assistant")
    parser.add_argument("--voice", default="id-ID-ArdiNeural", help="Edge-TTS voice")
    parser.add_argument("--mock", action="store_true", help="Use mock pre-recorded STT")
    parser.add_argument("--live", action="store_true", help="Use live microphone STT")
    parser.add_argument("--text", action="store_true", help="Use interactive text input with spoken audio output")
    parser.add_argument("--groq-key", help="Groq API key for STT")
    parser.add_argument("--device", type=int, help="Audio input device index")
    parser.add_argument("--hostapi", type=int, help="Audio host API index")
    parser.add_argument("--mqtt", action="store_true", help="Connect to real Solace broker via MQTT (localhost:1883)")
    parser.add_argument("--no-mock", action="store_true", help="Use real Solace broker instead of in-memory mock")
    parser.add_argument("--llm", default=os.getenv("LLM_PROVIDER", "gemini"), help="LLM provider: gemini, groq, openrouter, or mock")
    args = parser.parse_args()
    
    use_text = args.text
    use_mock_stt = args.mock or (not args.live and not args.text)
    groq_key = args.groq_key or os.getenv("GROQ_API_KEY")
    
    use_mqtt_solace = args.mqtt
    use_mock_solace = not (args.mqtt or args.no_mock)
    llm_provider = args.llm
    
    if use_text:
        cli = VoiceCLI(
            tts_voice=args.voice,
            use_mock_stt=False,
            groq_api_key=None,
            use_mock_solace=use_mock_solace,
            use_mqtt_solace=use_mqtt_solace,
            llm_provider=llm_provider,
        )
        cli.stt_service = None  # Forces interactive text prompt
        cli.mock_stt = None
    else:
        cli = VoiceCLI(
            tts_voice=args.voice,
            use_mock_stt=use_mock_stt,
            groq_api_key=groq_key,
            input_device=args.device,
            input_hostapi=args.hostapi,
            use_mock_solace=use_mock_solace,
            use_mqtt_solace=use_mqtt_solace,
            llm_provider=llm_provider,
        )
    await cli.start()


if __name__ == "__main__":
    asyncio.run(main())