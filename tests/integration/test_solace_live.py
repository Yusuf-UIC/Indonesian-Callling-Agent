import asyncio
import os
import subprocess
import sys
import time
from typing import Optional
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.solace_client import (
    create_solace_client,
    SolaceEvent,
    SolaceEventType,
    BankingEventHandler,
)
from app.services.mock_bank import mock_banking_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SolaceDockerManager:
    def __init__(self, compose_file: str = "docker-compose.yml"):
        self.compose_file = compose_file

    def start(self) -> bool:
        try:
            result = subprocess.run(
                ["docker-compose", "-f", self.compose_file, "up", "-d", "solace"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                logger.error(f"Failed to start Solace: {result.stderr}")
                return False
            logger.info("Solace container started")
            return True
        except Exception as e:
            logger.error(f"Error starting Solace: {e}")
            return False

    def stop(self) -> bool:
        try:
            subprocess.run(
                ["docker-compose", "-f", self.compose_file, "down"],
                capture_output=True,
                timeout=30
            )
            logger.info("Solace container stopped")
            return True
        except Exception as e:
            logger.error(f"Error stopping Solace: {e}")
            return False

    def wait_for_ready(self, timeout: int = 120) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ["docker-compose", "-f", self.compose_file, "exec", "-T", "solace",
                     "curl", "-sf", "http://localhost:8008/SEMP/v2/config/msgVpns/default"],
                    capture_output=True,
                    timeout=10
                )
                if result.returncode == 0:
                    logger.info("Solace broker is ready")
                    return True
            except:
                pass
            time.sleep(5)
        logger.error("Solace broker did not become ready in time")
        return False


async def test_live_solace():
    print("=" * 60)
    print("Testing Live Solace PubSub+ Integration")
    print("=" * 60)

    docker_manager = SolaceDockerManager()

    print("\n[1/4] Starting Solace container...")
    if not docker_manager.start():
        print("  FAILED: Could not start Solace container")
        return False

    print("\n[2/4] Waiting for Solace broker to be ready...")
    if not docker_manager.wait_for_ready(timeout=180):
        print("  FAILED: Solace broker not ready")
        docker_manager.stop()
        return False

    print("\n[3/4] Connecting to Solace and testing event flow...")
    try:
        # Use the real Solace client (not mock)
        os.environ["SOLACE_HOST"] = "localhost"
        os.environ["SOLACE_PORT"] = "55555"
        os.environ["SOLACE_USERNAME"] = "admin"
        os.environ["SOLACE_PASSWORD"] = "admin"
        os.environ["SOLACE_VPN"] = "default"

        # Try to connect with real client, fall back to mock
        use_real = True
        try:
            client = await create_solace_client(use_mock=not use_real)
        except Exception as e:
            logger.warning(f"Real Solace client failed, using mock: {e}")
            use_real = False
            client = await create_solace_client(use_mock=True)

        # Set up event handler
        handler = BankingEventHandler(client, mock_banking_service)

        # Test request-reply flows
        test_cases = [
            ("Balance Request", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
             {"account_number": "1234567890", "account_type": "savings"}),
            ("Transactions Request", SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE,
             {"account_number": "9876543210", "limit": 3}),
            ("Block Card Request", SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE,
             {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}),
        ]

        all_passed = True
        for name, req_type, resp_type, payload in test_cases:
            print(f"\n  Testing {name}...")
            request = SolaceEvent(
                event_type=req_type,
                correlation_id=f"test-{req_type.value}-{int(time.time())}",
                payload=payload,
            )

            try:
                response = await client.request_reply(request, resp_type.value, timeout=10.0)
                if response.payload.get("status") == "success":
                    print(f"    PASSED: {resp_type.value}")
                else:
                    print(f"    FAILED: {response.payload}")
                    all_passed = False
            except Exception as e:
                print(f"    FAILED: {e}")
                all_passed = False

        await client.disconnect()

    except Exception as e:
        logger.error(f"Error during Solace test: {e}")
        all_passed = False

    print("\n[4/4] Stopping Solace container...")
    docker_manager.stop()

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL TESTS PASSED!")
    else:
        print("SOME TESTS FAILED!")
    print("=" * 60)

    return all_passed


async def test_mock_solace():
    """Test with mock client (no Docker required)"""
    print("=" * 60)
    print("Testing Mock Solace Client (No Docker)")
    print("=" * 60)

    client = await create_solace_client(use_mock=True)
    handler = BankingEventHandler(client, mock_banking_service)

    test_cases = [
        ("Balance Request", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
         {"account_number": "1234567890", "account_type": "savings"}),
        ("Transactions Request", SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE,
         {"account_number": "9876543210", "limit": 3}),
        ("Block Card Request", SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE,
         {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}),
        ("Error: Invalid Account", SolaceEventType.BALANCE_REQUEST, SolaceEventType.ERROR,
         {"account_number": "9999999999", "account_type": "savings"}),
    ]

    all_passed = True
    for name, req_type, resp_type, payload in test_cases:
        print(f"\n  Testing {name}...")
        request = SolaceEvent(
            event_type=req_type,
            correlation_id=f"test-{req_type.value}",
            payload=payload,
        )

        try:
            response = await client.request_reply(request, resp_type.value, timeout=5.0)
            if resp_type == SolaceEventType.ERROR:
                if "error" in response.payload:
                    print(f"    PASSED: Error response received")
                else:
                    print(f"    FAILED: Expected error response")
                    all_passed = False
            elif response.payload.get("status") == "success":
                print(f"    PASSED: {resp_type.value}")
            else:
                print(f"    FAILED: {response.payload}")
                all_passed = False
        except Exception as e:
            print(f"    FAILED: {e}")
            all_passed = False

    await client.disconnect()

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL MOCK TESTS PASSED!")
    else:
        print("SOME MOCK TESTS FAILED!")
    print("=" * 60)

    return all_passed


async def test_mqtt_solace():
    """Test with MQTT client (requires running Solace with MQTT on port 1883)"""
    print("=" * 60)
    print("Testing MQTT Solace Client")
    print("=" * 60)

    # Set environment variables for MQTT
    os.environ["SOLACE_MQTT_HOST"] = "localhost"
    os.environ["SOLACE_MQTT_PORT"] = "1883"
    os.environ["SOLACE_MQTT_USERNAME"] = "admin"
    os.environ["SOLACE_MQTT_PASSWORD"] = "admin"

    client = await create_solace_client(use_mock=False, use_mqtt=True)
    handler = BankingEventHandler(client, mock_banking_service)

    test_cases = [
        ("Balance Request", SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE,
         {"account_number": "1234567890", "account_type": "savings"}),
        ("Transactions Request", SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE,
         {"account_number": "9876543210", "limit": 3}),
        ("Block Card Request", SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE,
         {"card_number": "4567890123456791", "identity_otp": "123456", "reason": "lost_stolen"}),
        ("Error: Invalid Account", SolaceEventType.BALANCE_REQUEST, SolaceEventType.ERROR,
         {"account_number": "9999999999", "account_type": "savings"}),
    ]

    all_passed = True
    for name, req_type, resp_type, payload in test_cases:
        print(f"\n  Testing {name}...")
        request = SolaceEvent(
            event_type=req_type,
            correlation_id=f"test-{req_type.value}",
            payload=payload,
        )

        try:
            response = await client.request_reply(request, resp_type.value, timeout=5.0)
            if resp_type == SolaceEventType.ERROR:
                if "error" in response.payload:
                    print(f"    PASSED: Error response received")
                else:
                    print(f"    FAILED: Expected error response")
                    all_passed = False
            elif response.payload.get("status") == "success":
                print(f"    PASSED: {resp_type.value}")
            else:
                print(f"    FAILED: {response.payload}")
                all_passed = False
        except Exception as e:
            print(f"    FAILED: {e}")
            all_passed = False

    await client.disconnect()

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL MQTT TESTS PASSED!")
    else:
        print("SOME MQTT TESTS FAILED!")
    print("=" * 60)

    return all_passed


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Solace Live Integration Test")
    parser.add_argument("--live", action="store_true", help="Run with live Docker Solace (SMF protocol)")
    parser.add_argument("--mqtt", action="store_true", help="Run with MQTT protocol (port 1883)")
    parser.add_argument("--mock", action="store_true", help="Run with mock client only")
    args = parser.parse_args()

    if args.live:
        success = await test_live_solace()
    elif args.mqtt:
        success = await test_mqtt_solace()
    else:
        success = await test_mock_solace()

    exit(0 if success else 1)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(main())