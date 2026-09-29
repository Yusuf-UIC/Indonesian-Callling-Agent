import asyncio
from app.services.agent_solace import BankingAgentSolace

async def test_mode(use_mock: bool, use_mqtt: bool, mode_name: str):
    print(f"\n==========================================")
    print(f" TESTING MODE: {mode_name}")
    print(f"==========================================")
    
    agent = BankingAgentSolace(use_mock=use_mock, use_mqtt=use_mqtt)
    await agent.initialize()
    
    print("\n--- TEST 1: Non-existent account ---")
    r1 = await agent.process('Cek saldo rekening 9999999999')
    print(f"Assistant Output: {r1.text}")
    assert "tidak ditemukan" in r1.text.lower() or "error" in r1.text.lower(), f"Unexpected output: {r1.text}"
    
    print("\n--- TEST 2: Invalid OTP ---")
    r2 = await agent.process('Blokir kartu 4567890123456789 OTP 000000')
    print(f"Assistant Output: {r2.text}")
    assert "tidak valid" in r2.text.lower() or "error" in r2.text.lower(), f"Unexpected output: {r2.text}"
    
    print("\n--- TEST 3: Valid Balance Query ---")
    r3 = await agent.process('Cek saldo rekening 1234567890')
    print(f"Assistant Output: {r3.text}")
    assert "lima juta" in r3.text.lower(), f"Unexpected output: {r3.text}"
    
    await agent.close()
    print(f"[OK] All tests passed for {mode_name}!")

async def main():
    # Test Mock mode
    await test_mode(use_mock=True, use_mqtt=False, mode_name="MOCK SOLACE")
    
    # Test MQTT mode
    await test_mode(use_mock=False, use_mqtt=True, mode_name="REAL SOLACE (MQTT)")

if __name__ == "__main__":
    asyncio.run(main())
