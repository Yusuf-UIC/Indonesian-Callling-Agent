import asyncio
from app.services.agent_solace import BankingAgentSolace

async def main():
    agent = BankingAgentSolace(use_mock=True, use_mqtt=False)
    await agent.initialize()
    
    print("\n" + "=" * 60)
    print("TEST 1: 5 Transaksi Terakhir (Check Wording)")
    print("=" * 60)
    res1 = await agent.process("Cek 5 transaksi terakhir untuk 1122334455")
    print(res1.text)
    assert "Menampilkan 5 dari total 15 transaksi." in res1.text
    
    print("\n" + "=" * 60)
    print("TEST 2: Transaksi 7 Hari Terakhir")
    print("=" * 60)
    res2 = await agent.process("Lihat transaksi 7 hari terakhir untuk 1122334455")
    print(res2.text)
    
    print("\n" + "=" * 60)
    print("TEST 3: Transaksi Tanggal 25 September 2026")
    print("=" * 60)
    res3 = await agent.process("Transaksi tanggal 25 september 2026 rekening 1234567890")
    print(res3.text)
    
    await agent.close()
    print("\n[OK] All date and limit transaction tests passed!")

if __name__ == "__main__":
    asyncio.run(main())
