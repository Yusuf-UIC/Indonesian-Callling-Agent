import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.services.stt_service import STTService


async def test_stt():
    print("=" * 60)
    print("Testing STT Service with Microphone")
    print("=" * 60)
    print("\nSpeak something in Indonesian (e.g., 'Cek saldo rekening 1234567890')")
    print("Recording will stop automatically after 1.2 seconds of silence\n")

    groq_key = os.getenv("GROQ_API_KEY")
    use_local = not groq_key
    
    print(f"Using {'Groq API' if groq_key else 'Local FasterWhisper'} for STT")
    
    stt = STTService(groq_api_key=groq_key, use_local_fallback=use_local)
    text = await stt.listen_and_transcribe()
    
    print(f"\nTranscribed text: '{text}'")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_stt())