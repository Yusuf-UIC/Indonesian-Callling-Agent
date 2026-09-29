import asyncio
from app.services.agent_solace import BankingAgentSolace

async def main():
    agent = BankingAgentSolace(use_mock=True, use_mqtt=False)
    await agent.initialize()
    
    print("\n" + "=" * 60)
    print("TRANSACTION HISTORY FOR 1122334455")
    print("=" * 60)
    res = await agent.process("Cek 5 transaksi terakhir untuk 1122334455")
    print(res.text)
    
    print("\n" + "=" * 60)
    print("TRANSACTION HISTORY FOR 9876543210")
    print("=" * 60)
    res2 = await agent.process("Tampilkan transaksi terakhir rekening 9876543210")
    print(res2.text)
    
    await agent.close()

if __name__ == "__main__":
    asyncio.run(main())
