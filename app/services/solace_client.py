import asyncio
import json
import uuid
import os
from typing import Dict, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging

try:
    import solace.messaging as solace
    from solace.messaging.messaging_service import MessagingService, ServiceInterruptionListener, RetryStrategy
    from solace.messaging.resources.topic import Topic
    from solace.messaging.resources.queue import Queue
    from solace.messaging.publisher.direct_message_publisher import DirectMessagePublisher
    from solace.messaging.receiver.direct_message_receiver import DirectMessageReceiver
    from solace.messaging.receiver.message_handler import MessageHandler
    from solace.messaging.config.solace_properties import SolaceProperties
    SOLACE_AVAILABLE = True
except ImportError:
    SOLACE_AVAILABLE = False
    solace = None

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

logger = logging.getLogger(__name__)


class SolaceEventType(str, Enum):
    BALANCE_REQUEST = "bank/account/balance/request"
    BALANCE_RESPONSE = "bank/account/balance/response"
    TRANSACTIONS_REQUEST = "bank/account/transactions/request"
    TRANSACTIONS_RESPONSE = "bank/account/transactions/response"
    BLOCK_CARD_REQUEST = "bank/card/block/request"
    BLOCK_CARD_RESPONSE = "bank/card/block/response"
    CALL_STARTED = "ivr/call/started"
    CALL_ENDED = "ivr/call/ended"
    INTENT_DETECTED = "ivr/intent/detected"
    ERROR = "ivr/error"


@dataclass
class SolaceEvent:
    event_type: SolaceEventType
    correlation_id: str
    payload: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "ivr-agent"
    
    def to_json(self) -> str:
        return json.dumps({
            "event_type": self.event_type.value,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "source": self.source
        })
    
    @classmethod
    def from_json(cls, json_str: str) -> "SolaceEvent":
        data = json.loads(json_str)
        return cls(
            event_type=SolaceEventType(data["event_type"]),
            correlation_id=data["correlation_id"],
            payload=data["payload"],
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            source=data.get("source", "unknown")
        )


class MockSolaceClient:
    def __init__(self):
        self.subscriptions: Dict[str, list] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}
    
    async def connect(self):
        logger.info("Mock Solace client connected")
    
    async def disconnect(self):
        logger.info("Mock Solace client disconnected")
    
    def subscribe(self, topic: str, handler: Callable[[SolaceEvent], Awaitable[None]]):
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
        self.subscriptions[topic].append(handler)
        logger.info(f"Subscribed to topic: {topic}")
    
    async def publish(self, event: SolaceEvent):
        logger.info(f"Publishing event: {event.event_type.value} (correlation_id: {event.correlation_id})")
        
        topic = event.event_type.value
        if topic in self.subscriptions:
            for handler in self.subscriptions[topic]:
                try:
                    await handler(event)
                except Exception as e:
                    logger.error(f"Error in handler for {topic}: {e}")
        
        # Check for request-reply pattern (only match response events)
        if event.correlation_id in self.pending_requests and not event.event_type.value.endswith("/request"):
            future = self.pending_requests.pop(event.correlation_id)
            if not future.done():
                future.set_result(event)
    
    async def request_reply(self, request_event: SolaceEvent, response_topic: str, timeout: float = 10.0) -> SolaceEvent:
        future = asyncio.Future()
        self.pending_requests[request_event.correlation_id] = future
        
        await self.publish(request_event)
        
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_event.correlation_id, None)
            raise TimeoutError(f"Request timeout for correlation_id: {request_event.correlation_id}")


class SolaceClient:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 55555,
        username: str = "admin",
        password: str = "admin",
        vpn: str = "default"
    ):
        if not SOLACE_AVAILABLE:
            raise RuntimeError("Solace Python API not installed. Install with: pip install solace-messaging")
        
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.vpn = vpn
        self.messaging_service: Optional[MessagingService] = None
        self.publisher: Optional[DirectMessagePublisher] = None
        self.receivers: Dict[str, DirectMessageReceiver] = {}
        self.handlers: Dict[str, Callable] = {}
    
    async def connect(self):
        props = {
            SolaceProperties.TRANSPORT_PROTOCOL: "tcp",
            SolaceProperties.HOST: f"{self.host}:{self.port}",
            SolaceProperties.USERNAME: self.username,
            SolaceProperties.PASSWORD: self.password,
            SolaceProperties.VPN_NAME: self.vpn,
        }
        
        self.messaging_service = MessagingService.builder().from_properties(props).build()
        self.messaging_service.connect()
        
        self.publisher = self.messaging_service.create_direct_message_publisher_builder().build()
        self.publisher.set_publish_failure_listener(self._on_publish_failure)
        self.publisher.start()
        
        logger.info(f"Connected to Solace at {self.host}:{self.port}")
    
    def _on_publish_failure(self, error):
        logger.error(f"Publish failure: {error}")
    
    async def disconnect(self):
        if self.publisher:
            self.publisher.terminate()
        for receiver in self.receivers.values():
            receiver.terminate()
        if self.messaging_service:
            self.messaging_service.disconnect()
        logger.info("Disconnected from Solace")
    
    def subscribe(self, topic: str, handler: Callable[[SolaceEvent], Awaitable[None]]):
        topic_obj = Topic.of(topic)
        receiver = self.messaging_service.create_direct_message_receiver_builder() \
            .with_subscriptions([topic_obj]) \
            .build()
        
        receiver.start()
        receiver.receive_async(handler)
        
        self.receivers[topic] = receiver
        self.handlers[topic] = handler
        logger.info(f"Subscribed to topic: {topic}")
    
    async def publish(self, event: SolaceEvent):
        topic_obj = Topic.of(event.event_type.value)
        message = self.messaging_service.message_builder() \
            .with_application_message_id(event.correlation_id) \
            .build(event.to_json())
        
        self.publisher.publish(destination=topic_obj, message=message)
        logger.info(f"Published event: {event.event_type.value}")
    
    async def request_reply(self, request_event: SolaceEvent, response_topic: str, timeout: float = 10.0) -> SolaceEvent:
        correlation_id = request_event.correlation_id
        future = asyncio.Future()
        
        response_handler = None
        
        def create_handler():
            nonlocal response_handler
            async def handler(message):
                try:
                    event = SolaceEvent.from_json(message.get_payload_as_string())
                    if event.correlation_id == correlation_id:
                        if not future.done():
                            future.set_result(event)
                        if response_handler and response_topic in self.receivers:
                            self.receivers[response_topic].receive_async(None)
                except Exception as e:
                    logger.error(f"Error handling response: {e}")
                    if not future.done():
                        future.set_exception(e)
            response_handler = handler
            return handler
        
        self.subscribe(response_topic, create_handler())
        
        await self.publish(request_event)
        
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            raise TimeoutError(f"Request timeout for correlation_id: {correlation_id}")
        finally:
            if response_topic in self.receivers:
                self.receivers[response_topic].terminate()
                del self.receivers[response_topic]


class MQTTSolaceClient:
    """MQTT-based Solace client using paho-mqtt for cross-platform compatibility."""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        username: str = "admin",
        password: str = "admin",
        client_id: str = None,
    ):
        if not MQTT_AVAILABLE:
            raise RuntimeError("paho-mqtt not installed. Install with: pip install paho-mqtt")
        
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id or f"solace-client-{uuid.uuid4().hex[:8]}"
        
        self.mqtt_client: Optional[mqtt.Client] = None
        self.connected = False
        self.subscriptions: Dict[str, list] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.connected = True
            logger.info(f"Connected to Solace MQTT at {self.host}:{self.port}")
            # Resubscribe to all topics
            for topic in self.subscriptions:
                self.mqtt_client.subscribe(topic)
        else:
            logger.error(f"Failed to connect to MQTT broker: {rc}")
    
    def _on_disconnect(self, client, userdata, *args, **kwargs):
        self.connected = False
        logger.info("Disconnected from MQTT broker")
    
    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            event = SolaceEvent.from_json(payload)
            
            # Handle request-reply pattern (only match response events)
            if event.correlation_id in self.pending_requests and not event.event_type.value.endswith("/request"):
                future = self.pending_requests.pop(event.correlation_id)
                if not future.done():
                    future.set_result(event)
            
            # Call subscription handlers
            if topic in self.subscriptions:
                for handler in self.subscriptions[topic]:
                    try:
                        # Schedule handler in event loop
                        if self._loop and self._loop.is_running():
                            asyncio.run_coroutine_threadsafe(handler(event), self._loop)
                    except Exception as e:
                        logger.error(f"Error in handler for {topic}: {e}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
    
    async def connect(self):
        self._loop = asyncio.get_running_loop()
        
        self.mqtt_client = mqtt.Client(
            client_id=self.client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        self.mqtt_client.username_pw_set(self.username, self.password)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message
        
        # Connect in a thread to avoid blocking
        await asyncio.to_thread(self.mqtt_client.connect, self.host, self.port)
        self.mqtt_client.loop_start()
        
        # Wait for connection
        for _ in range(50):  # 5 second timeout
            if self.connected:
                logger.info(f"Connected to Solace MQTT at {self.host}:{self.port}")
                return
            await asyncio.sleep(0.1)
        raise ConnectionError(f"Failed to connect to MQTT broker at {self.host}:{self.port}")
    
    async def disconnect(self):
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            await asyncio.to_thread(self.mqtt_client.disconnect)
            self.connected = False
            logger.info("Disconnected from Solace MQTT")
    
    def subscribe(self, topic: str, handler: Callable[[SolaceEvent], Awaitable[None]]):
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
        self.subscriptions[topic].append(handler)
        if self.connected and self.mqtt_client:
            self.mqtt_client.subscribe(topic)
        logger.info(f"Subscribed to topic: {topic}")
    
    async def publish(self, event: SolaceEvent):
        if not self.connected or not self.mqtt_client:
            raise RuntimeError("Not connected to MQTT broker")
        
        topic = event.event_type.value
        payload = event.to_json()
        
        # Publish in thread to avoid blocking
        await asyncio.to_thread(self.mqtt_client.publish, topic, payload, qos=1)
        logger.info(f"Published event: {event.event_type.value}")
    
    async def request_reply(self, request_event: SolaceEvent, response_topic: str, timeout: float = 10.0) -> SolaceEvent:
        future = asyncio.Future()
        self.pending_requests[request_event.correlation_id] = future
        
        # Subscribe to response topic
        if response_topic not in self.subscriptions:
            self.subscriptions[response_topic] = []
        if self.connected and self.mqtt_client:
            self.mqtt_client.subscribe(response_topic)
        
        await self.publish(request_event)
        
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_event.correlation_id, None)
            raise TimeoutError(f"Request timeout for correlation_id: {request_event.correlation_id}")
        finally:
            # Note: We keep the subscription for future requests
            pass
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


class BankingEventHandler:
    def __init__(self, solace_client, banking_service):
        self.solace = solace_client
        self.banking_service = banking_service
        self._setup_subscriptions()
    
    def _setup_subscriptions(self):
        self.solace.subscribe(SolaceEventType.BALANCE_REQUEST.value, self._handle_balance_request)
        self.solace.subscribe(SolaceEventType.TRANSACTIONS_REQUEST.value, self._handle_transactions_request)
        self.solace.subscribe(SolaceEventType.BLOCK_CARD_REQUEST.value, self._handle_block_card_request)
    
    async def _handle_balance_request(self, event: SolaceEvent):
        try:
            account_number = event.payload.get("account_number")
            account_type = event.payload.get("account_type", "savings")
            
            from app.services.mock_bank import mock_banking_service
            from app.schemas.banking import BalanceEnquiryRequest, AccountType
            
            request = BalanceEnquiryRequest(
                account_number=account_number,
                account_type=AccountType(account_type)
            )
            response = mock_banking_service.get_balance(request)
            
            reply_event = SolaceEvent(
                event_type=SolaceEventType.BALANCE_RESPONSE,
                correlation_id=event.correlation_id,
                payload=response.model_dump(mode="json")
            )
            await self.solace.publish(reply_event)
            
        except Exception as e:
            logger.error(f"Error handling balance request: {e}")
            reply_event = SolaceEvent(
                event_type=SolaceEventType.BALANCE_RESPONSE,
                correlation_id=event.correlation_id,
                payload={"status": "error", "error": str(e)}
            )
            await self.solace.publish(reply_event)
            error_event = SolaceEvent(
                event_type=SolaceEventType.ERROR,
                correlation_id=event.correlation_id,
                payload={"error": str(e), "original_event": event.event_type.value}
            )
            await self.solace.publish(error_event)
    
    async def _handle_transactions_request(self, event: SolaceEvent):
        try:
            account_number = event.payload.get("account_number")
            limit = event.payload.get("limit", 10)
            start_date_str = event.payload.get("start_date")
            end_date_str = event.payload.get("end_date")
            
            start_date = datetime.fromisoformat(start_date_str) if start_date_str else None
            end_date = datetime.fromisoformat(end_date_str) if end_date_str else None
            
            from app.services.mock_bank import mock_banking_service
            from app.schemas.banking import TransactionListRequest
            
            request = TransactionListRequest(
                account_number=account_number,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
            )
            response = mock_banking_service.get_transactions(request)
            
            reply_event = SolaceEvent(
                event_type=SolaceEventType.TRANSACTIONS_RESPONSE,
                correlation_id=event.correlation_id,
                payload=response.model_dump(mode="json")
            )
            await self.solace.publish(reply_event)
            
        except Exception as e:
            logger.error(f"Error handling transactions request: {e}")
            reply_event = SolaceEvent(
                event_type=SolaceEventType.TRANSACTIONS_RESPONSE,
                correlation_id=event.correlation_id,
                payload={"status": "error", "error": str(e)}
            )
            await self.solace.publish(reply_event)
            error_event = SolaceEvent(
                event_type=SolaceEventType.ERROR,
                correlation_id=event.correlation_id,
                payload={"error": str(e), "original_event": event.event_type.value}
            )
            await self.solace.publish(error_event)
    
    async def _handle_block_card_request(self, event: SolaceEvent):
        try:
            card_number = event.payload.get("card_number")
            identity_otp = event.payload.get("identity_otp")
            reason = event.payload.get("reason", "lost_stolen")
            
            from app.services.mock_bank import mock_banking_service
            from app.schemas.banking import CardBlockRequest
            
            request = CardBlockRequest(
                card_number=card_number,
                identity_otp=identity_otp,
                reason=reason
            )
            response = mock_banking_service.block_card(request)
            
            reply_event = SolaceEvent(
                event_type=SolaceEventType.BLOCK_CARD_RESPONSE,
                correlation_id=event.correlation_id,
                payload=response.model_dump(mode="json")
            )
            await self.solace.publish(reply_event)
            
        except Exception as e:
            logger.error(f"Error handling block card request: {e}")
            reply_event = SolaceEvent(
                event_type=SolaceEventType.BLOCK_CARD_RESPONSE,
                correlation_id=event.correlation_id,
                payload={"status": "error", "error": str(e)}
            )
            await self.solace.publish(reply_event)
            error_event = SolaceEvent(
                event_type=SolaceEventType.ERROR,
                correlation_id=event.correlation_id,
                payload={"error": str(e), "original_event": event.event_type.value}
            )
            await self.solace.publish(error_event)


async def create_solace_client(
    use_mock: bool = True, 
    use_mqtt: bool = False
) -> SolaceClient | MockSolaceClient | MQTTSolaceClient:
    if use_mock:
        client = MockSolaceClient()
        await client.connect()
        return client
    elif use_mqtt:
        if not MQTT_AVAILABLE:
            raise RuntimeError("paho-mqtt not installed. Install with: pip install paho-mqtt")
        host = os.getenv("SOLACE_MQTT_HOST", "localhost")
        port = int(os.getenv("SOLACE_MQTT_PORT", "1883"))
        username = os.getenv("SOLACE_MQTT_USERNAME", "admin")
        password = os.getenv("SOLACE_MQTT_PASSWORD", "admin")
        
        client = MQTTSolaceClient(host, port, username, password)
        await client.connect()
        return client
    else:
        if not SOLACE_AVAILABLE:
            raise RuntimeError("Solace Python API not installed. Install with: pip install solace-messaging")
        host = os.getenv("SOLACE_HOST", "localhost")
        port = int(os.getenv("SOLACE_PORT", "55555"))
        username = os.getenv("SOLACE_USERNAME", "admin")
        password = os.getenv("SOLACE_PASSWORD", "admin")
        vpn = os.getenv("SOLACE_VPN", "default")
        
        client = SolaceClient(host, port, username, password, vpn)
        await client.connect()
        return client


async def main():
    client = await create_solace_client(use_mock=True)
    
    # Test publish/subscribe
    received_events = []
    
    async def test_handler(event: SolaceEvent):
        received_events.append(event)
        print(f"Received: {event.event_type.value} - {event.payload}")
    
    client.subscribe(SolaceEventType.BALANCE_REQUEST.value, test_handler)
    
    # Test request-reply
    request = SolaceEvent(
        event_type=SolaceEventType.BALANCE_REQUEST,
        correlation_id=str(uuid.uuid4()),
        payload={"account_number": "1234567890", "account_type": "savings"}
    )
    
    print("Testing request-reply...")
    response = await client.request_reply(request, SolaceEventType.BALANCE_RESPONSE.value)
    print(f"Response: {response.payload}")
    
    await client.disconnect()
    print("Mock Solace test completed!")


if __name__ == "__main__":
    asyncio.run(main())