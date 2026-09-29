import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.agent import BankingAgent


async def main():
    agent = BankingAgent()
    
    # Test blocking the second card which should not be blocked yet
    test_inputs = [
        "Blokir kartu 4567890123456790 OTP 123456",
    ]
    
    for user_input in test_inputs:
        print(f"\n{'='*60}")
        print(f"User: {user_input}")
        print(f"{'='*60}")
        
        response = await agent.process(user_input)
        print(f"Intent: {response.intent.value}")
        print(f"Tool: {response.tool_called}")
        print(f"Response: {response.text}")
    
    await agent.close()


if __name__ == "__main__":
    asyncio.run(main())