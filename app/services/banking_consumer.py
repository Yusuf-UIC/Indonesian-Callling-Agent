import asyncio
import logging
import signal
import sys
from typing import Optional

sys.path.insert(0, ".")

from app.services.solace_client import (
    create_solace_client,
    SolaceEvent,
    SolaceEventType,
    BankingEventHandler,
)
from app.services.mock_bank import mock_banking_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BankingConsumer:
    """Standalone banking microservice worker that consumes Solace events and processes banking operations."""
    
    def __init__(
        self,
        use_mock: bool = True,
        use_mqtt: bool = False,
        solace_host: str = "localhost",
        solace_port: int = 55555,
        solace_username: str = "admin",
        solace_password: str = "admin",
        solace_vpn: str = "default",
        solace_mqtt_port: int = 1883,
    ):
        self.use_mock = use_mock
        self.use_mqtt = use_mqtt
        self.solace_host = solace_host
        self.solace_port = solace_port
        self.solace_username = solace_username
        self.solace_password = solace_password
        self.solace_vpn = solace_vpn
        self.solace_mqtt_port = solace_mqtt_port
        
        self.solace_client = None
        self.event_handler = None
        self.running = False
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    async def initialize(self):
        """Initialize Solace client and event handler."""
        logger.info("[Initializing Solace client...]")
        self.solace_client = await create_solace_client(
            use_mock=self.use_mock,
            use_mqtt=self.use_mqtt,
        )
        
        logger.info("[Registering banking event handler...]")
        self.event_handler = BankingEventHandler(self.solace_client, mock_banking_service)
        logger.info("[Banking consumer initialized]")
    
    async def start(self):
        """Start the consumer and wait for shutdown signal."""
        self.running = True
        logger.info("=" * 60)
        logger.info("BANKING CONSUMER WORKER STARTED")
        logger.info("=" * 60)
        logger.info("Listening for banking events on Solace topics:")
        logger.info("  - bank/account/balance/request")
        logger.info("  - bank/account/transactions/request")
        logger.info("  - bank/card/block/request")
        logger.info("Press Ctrl+C to stop")
        logger.info("=" * 60)
        
        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Consumer cancelled")
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("[Shutting down banking consumer...]")
        self.running = False
        if self.solace_client:
            await self.solace_client.disconnect()
        logger.info("[Banking consumer stopped]")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Banking Consumer Worker")
    parser.add_argument("--mock", action="store_true", default=True, help="Use mock Solace client")
    parser.add_argument("--mqtt", action="store_true", help="Use MQTT protocol")
    parser.add_argument("--host", default="localhost", help="Solace host")
    parser.add_argument("--port", type=int, default=55555, help="Solace SMF port")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="Solace MQTT port")
    parser.add_argument("--username", default="admin", help="Solace username")
    parser.add_argument("--password", default="admin", help="Solace password")
    parser.add_argument("--vpn", default="default", help="Solace VPN")
    args = parser.parse_args()
    
    consumer = BankingConsumer(
        use_mock=args.mock,
        use_mqtt=args.mqtt,
        solace_host=args.host,
        solace_port=args.port,
        solace_username=args.username,
        solace_password=args.password,
        solace_vpn=args.vpn,
        solace_mqtt_port=args.mqtt_port,
    )
    
    await consumer.initialize()
    await consumer.start()


if __name__ == "__main__":
    asyncio.run(main())