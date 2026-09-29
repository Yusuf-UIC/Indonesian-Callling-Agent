import httpx
import asyncio
from app.main import app


async def test_endpoints():
    transport = httpx.ASGITransport(app=app)
    
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        print("=" * 60)
        print("Testing Balance Enquiry")
        print("=" * 60)
        response = await client.post(
            "/api/v1/banking/balance",
            json={"account_number": "1234567890", "account_type": "savings"}
        )
        assert response.status_code == 200
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        print("\n" + "=" * 60)
        print("Testing Transaction List")
        print("=" * 60)
        response = await client.post(
            "/api/v1/banking/transactions",
            json={"account_number": "1234567890", "limit": 5}
        )
        assert response.status_code == 200
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        print("\n" + "=" * 60)
        print("Testing Card Block")
        print("=" * 60)
        response = await client.post(
            "/api/v1/banking/block-card",
            json={"card_number": "4567890123456789", "identity_otp": "123456", "reason": "lost_stolen"}
        )
        assert response.status_code == 200
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        print("\n" + "=" * 60)
        print("Testing Error Cases")
        print("=" * 60)
        
        # Test invalid account
        response = await client.post(
            "/api/v1/banking/balance",
            json={"account_number": "9999999999", "account_type": "savings"}
        )
        assert response.status_code == 404
        print(f"Invalid Account - Status: {response.status_code}")
        print(f"Response: {response.json()}")
        
        # Test invalid OTP
        response = await client.post(
            "/api/v1/banking/block-card",
            json={"card_number": "4567890123456790", "identity_otp": "000000", "reason": "lost_stolen"}
        )
        assert response.status_code == 400
        print(f"Invalid OTP - Status: {response.status_code}")
        print(f"Response: {response.json()}")


if __name__ == "__main__":
    asyncio.run(test_endpoints())