import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.solace_client import (
    create_solace_client, 
    SolaceEvent, 
    SolaceEventType,
    BankingEventHandler
)
from app.services.mock_bank import mock_banking_service


async def test_solace_mock():
    print("Testing Mock Solace Client...")
    print("="*60)
    
    client = await create_solace_client(use_mock=True)
    
    # Set up banking event handler
    handler = BankingEventHandler(client, mock_banking_service)
    
    # Test 1: Balance request-reply
    print("\n--- Test 1: Balance Request-Reply ---")
    request = SolaceEvent(
        event_type=SolaceEventType.BALANCE_REQUEST,
        correlation_id="test-correlation-1",
        payload={"account_number": "1234567890", "account_type": "savings"}
    )
    
    response = await client.request_reply(request, SolaceEventType.BALANCE_RESPONSE.value, timeout=5.0)
    print(f"Request: {request.payload}")
    print(f"Response: {response.payload}")
    print(f"Status: {response.payload.get('status')}")
    print(f"Balance: {response.payload.get('balance')}")
    
    # Test 2: Transactions request-reply
    print("\n--- Test 2: Transactions Request-Reply ---")
    request = SolaceEvent(
        event_type=SolaceEventType.TRANSACTIONS_REQUEST,
        correlation_id="test-correlation-2",
        payload={"account_number": "9876543210", "limit": 3}
    )
    
    response = await client.request_reply(request, SolaceEventType.TRANSACTIONS_RESPONSE.value, timeout=5.0)
    print(f"Request: {request.payload}")
    print(f"Response status: {response.payload.get('status')}")
    print(f"Transaction count: {response.payload.get('total_count')}")
    print(f"Returned transactions: {len(response.payload.get('transactions', []))}")
    
    # Test 3: Block card request-reply
    print("\n--- Test 3: Block Card Request-Reply ---")
    request = SolaceEvent(
        event_type=SolaceEventType.BLOCK_CARD_REQUEST,
        correlation_id="test-correlation-3",
        payload={"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}
    )
    
    response = await client.request_reply(request, SolaceEventType.BLOCK_CARD_RESPONSE.value, timeout=5.0)
    print(f"Request: {request.payload}")
    print(f"Response: {response.payload.get('message')}")
    print(f"Blocked: {response.payload.get('blocked')}")
    print(f"Reference: {response.payload.get('block_reference')}")
    
    # Test 4: Error handling - invalid account
    print("\n--- Test 4: Error Handling (Invalid Account) ---")
    request = SolaceEvent(
        event_type=SolaceEventType.BALANCE_REQUEST,
        correlation_id="test-correlation-4",
        payload={"account_number": "9999999999", "account_type": "savings"}
    )
    
    response = await client.request_reply(request, SolaceEventType.ERROR.value, timeout=5.0)
    print(f"Error response: {response.payload}")
    
    await client.disconnect()
    print("\n" + "="*60)
    print("All Solace mock tests passed!")


if __name__ == "__main__":
    asyncio.run(test_solace_mock())