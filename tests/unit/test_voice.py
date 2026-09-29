import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.voice_pipeline import VoicePipeline, AudioConfig, MockSTTEngine, EdgeTTSEngine
from app.services.agent import BankingAgent


async def test_voice_pipeline():
    agent = BankingAgent()
    
    config = AudioConfig(
        sample_rate=16000,
        block_size=512,
        vad_threshold=0.5,
        min_speech_duration_ms=300,
        max_silence_duration_ms=1500,
    )
    
    stt = MockSTTEngine()
    tts = EdgeTTSEngine(voice="id-ID-ArdiNeural")
    
    pipeline = VoicePipeline(agent, config, stt, tts)
    
    print("\n" + "="*60)
    print("Testing Voice Pipeline with Mock STT")
    print("This will simulate voice interactions")
    print("="*60 + "\n")
    
    test_inputs = [
        "halo",
        "cek saldo rekening 1234567890",
        "transaksi terakhir rekening 9876543210",
        "blokir kartu 4567890123456791 o t p 123456",
        "bantuan",
        "keluar",
    ]
    
    for i, text in enumerate(test_inputs):
        print(f"\n--- Test {i+1} ---")
        print(f"Simulated input: {text}")
        
        response = await agent.process(text)
        print(f"Intent: {response.intent.value}")
        print(f"Response: {response.text}")
        
        if response.requires_tts and text != "keluar":
            print("[Generating TTS...]")
            audio_file = await tts.synthesize(response.text)
            print(f"TTS saved to: {audio_file}")
            try:
                os.remove(audio_file)
            except:
                pass
    
    await agent.close()
    print("\nVoice pipeline test completed!")


if __name__ == "__main__":
    asyncio.run(test_voice_pipeline())