import os
import json
import asyncio
import uuid
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from app.services.semantic_intent import SemanticIntentClassifier, IntentType, IntentResult
from app.core.prompts import ERROR_TEMPLATES
from app.core.formatters import (
    format_rupiah_for_tts,
    format_number_for_tts,
    format_date_for_tts,
    format_datetime_for_tts,
    format_transaction_for_tts,
)
from app.services.solace_client import (
    create_solace_client,
    SolaceEvent,
    SolaceEventType,
    MockSolaceClient,
    MQTTSolaceClient,
    SolaceClient,
)


@dataclass
class AgentResponse:
    text: str
    intent: IntentType
    tool_called: Optional[str] = None
    tool_result: Optional[Dict] = None
    requires_tts: bool = True


from app.services.session_manager import session_manager, SessionState


class BankingAgentSolace:
    def __init__(
        self,
        solace_host: str = "localhost",
        solace_port: int = 55555,
        solace_username: str = "admin",
        solace_password: str = "admin",
        solace_vpn: str = "default",
        use_mock: bool = True,
        use_mqtt: bool = False,
        llm_provider: str = "mock",
        shared_classifier: Optional[SemanticIntentClassifier] = None,
        default_session_id: str = "cli_session",
    ):
        # Solace client configuration
        self.solace_host = solace_host
        self.solace_port = solace_port
        self.solace_username = solace_username
        self.solace_password = solace_password
        self.solace_vpn = solace_vpn
        self.use_mock = use_mock
        self.use_mqtt = use_mqtt
        
        self.solace_client = None
        self.intent_classifier = shared_classifier or SemanticIntentClassifier()
        self.llm_provider = llm_provider
        self.default_session_id = default_session_id
        
        # Map intents to Solace request topics
        self.intent_to_topic = {
            IntentType.CHECK_BALANCE: SolaceEventType.BALANCE_REQUEST,
            IntentType.TRANSACTION_HISTORY: SolaceEventType.TRANSACTIONS_REQUEST,
            IntentType.BLOCK_CARD: SolaceEventType.BLOCK_CARD_REQUEST,
        }
        
        # Map intents to response topics
        self.intent_to_response_topic = {
            IntentType.CHECK_BALANCE: SolaceEventType.BALANCE_RESPONSE,
            IntentType.TRANSACTION_HISTORY: SolaceEventType.TRANSACTIONS_RESPONSE,
            IntentType.BLOCK_CARD: SolaceEventType.BLOCK_CARD_RESPONSE,
        }

    async def initialize(self):
        """Initialize Solace client and intent classifier."""
        print("[Initializing Solace client...]")
        self.solace_client = await create_solace_client(
            use_mock=self.use_mock,
            use_mqtt=self.use_mqtt,
        )
        
        # Register BankingEventHandler to handle requests on Solace backbone
        from app.services.mock_bank import mock_banking_service
        from app.services.solace_client import BankingEventHandler
        self.banking_handler = BankingEventHandler(self.solace_client, mock_banking_service)
        print("[Banking event handler registered]")
        
        # Initialize intent classifier only if not already initialized
        print("[Initializing intent classifier...]")
        if not self.intent_classifier._initialized:
            self.intent_classifier.initialize()

        # Initialize LLM Agent if provider is specified
        if self.llm_provider != "mock":
            from app.services.llm_agent import LLMAgentSolace
            print(f"[Initializing LLM Agent with provider '{self.llm_provider}'...]")
            self.llm_agent = LLMAgentSolace(
                solace_client=self.solace_client,
                provider=self.llm_provider
            )
        else:
            self.llm_agent = None

        print("[Banking Agent initialized]")

    async def close(self):
        if self.solace_client:
            await self.solace_client.disconnect()

    def _generate_correlation_id(self, intent: IntentType) -> str:
        """Generate unique correlation ID for request tracking."""
        timestamp = int(time.time() * 1000)
        unique = uuid.uuid4().hex[:8]
        return f"req-{intent.value}-{timestamp}-{unique}"

    async def _send_request_reply(
        self,
        intent: IntentType,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Send request via Solace and wait for response."""
        if not self.solace_client:
            return {"error": "Solace client not initialized"}
        
        request_topic = self.intent_to_topic[intent]
        response_topic = self.intent_to_response_topic[intent]
        correlation_id = self._generate_correlation_id(intent)
        
        request_event = SolaceEvent(
            event_type=request_topic,
            correlation_id=correlation_id,
            payload=payload,
        )
        
        try:
            print(f"[Solace] Publishing {request_topic.value} (correlation_id: {correlation_id})")
            response_event = await self.solace_client.request_reply(
                request_event, 
                response_topic.value, 
                timeout=10.0
            )
            print(f"[Solace] Received response on {response_event.event_type.value}")
            return response_event.payload
        except asyncio.TimeoutError:
            return {"error": "Request timeout - no response from banking service"}
        except Exception as e:
            return {"error": f"Solace communication error: {str(e)}"}

    async def process(self, user_input: str, session_id: Optional[str] = None) -> AgentResponse:
        sid = session_id or self.default_session_id
        session = session_manager.get_session(sid)

        # Route to LLM Agent if active
        if self.llm_agent:
            llm_res = await self.llm_agent.process(
                user_input=user_input,
                session_id=sid,
                history=session.history
            )
            session.history.append({"role": "user", "content": user_input})
            session.history.append({"role": "assistant", "content": llm_res.text})
            return AgentResponse(
                text=llm_res.text,
                intent=IntentType.UNKNOWN,
                tool_called=llm_res.tool_called,
                tool_result=llm_res.tool_result,
            )

        session.history.append({"role": "user", "content": user_input})
        
        raw_intent = self.intent_classifier.classify(user_input)
        intent_result = session_manager.resolve_contextual_intent(session, user_input, raw_intent)
        
        if intent_result.intent == IntentType.GREETING:
            response_text = "Selamat datang di layanan pelanggan kami! Ada yang bisa saya bantu hari ini? Anda bisa menanyakan saldo, transaksi, atau memblokir kartu."
            session.history.append({"role": "assistant", "content": response_text})
            return AgentResponse(text=response_text, intent=intent_result.intent)
        
        if intent_result.intent == IntentType.HELP:
            response_text = (
                "Saya bisa membantu Anda dengan:\n"
                "1. Cek saldo rekening - sebutkan nomor rekening\n"
                "2. Lihat riwayat transaksi - sebutkan nomor rekening\n"
                "3. Blokir kartu ATM/Debit/Kredit - sebutkan nomor kartu 16 digit dan OTP 6 digit\n\n"
                "Contoh: 'Cek saldo rekening 1234567890' atau 'Blokir kartu 4567890123456789 OTP 123456'"
            )
            session.history.append({"role": "assistant", "content": response_text})
            return AgentResponse(text=response_text, intent=intent_result.intent)
        
        if intent_result.intent == IntentType.UNKNOWN:
            response_text = "Maaf, saya tidak mengerti permintaan Anda. Bisa tolong ulangi atau katakan 'bantuan' untuk melihat menu yang tersedia?"
            session.history.append({"role": "assistant", "content": response_text})
            return AgentResponse(text=response_text, intent=intent_result.intent)
        
        tool_result = None
        tool_called = None
        response_text = ""
        
        try:
            if intent_result.intent == IntentType.CHECK_BALANCE:
                account_number = intent_result.entities.get("account_number")
                if not account_number:
                    session.active_intent = IntentType.CHECK_BALANCE
                    response_text = "Untuk cek saldo, saya butuh nomor rekening Anda. Bisa sebutkan nomor rekening 10-16 digit?"
                    session.history.append({"role": "assistant", "content": response_text})
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                tool_called = "check_balance"
                response = await self._send_request_reply(
                    IntentType.CHECK_BALANCE,
                    {"account_number": account_number, "account_type": "savings"}
                )
                tool_result = response
                session.reset_intent()
                
                if "error" in response or response.get("status") == "error":
                    response_text = response.get("error", "Terjadi kesalahan saat memeriksa saldo.")
                else:
                    balance_formatted = format_rupiah_for_tts(response.get("balance", 0))
                    account_formatted = format_number_for_tts(account_number)
                    last_updated = format_datetime_for_tts(response.get("last_updated", ""))
                    
                    response_text = (
                        f"Saldo rekening {account_formatted} (savings) "
                        f"adalah {balance_formatted}. Terakhir diperbarui pada {last_updated}."
                    )
                
            elif intent_result.intent == IntentType.TRANSACTION_HISTORY:
                account_number = intent_result.entities.get("account_number")
                if not account_number:
                    session.active_intent = IntentType.TRANSACTION_HISTORY
                    response_text = "Untuk lihat transaksi, saya butuh nomor rekening Anda. Bisa sebutkan nomor rekening?"
                    session.history.append({"role": "assistant", "content": response_text})
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                limit = int(intent_result.entities.get("limit", 5))
                start_date = intent_result.entities.get("start_date")
                end_date = intent_result.entities.get("end_date")
                
                payload = {"account_number": account_number, "limit": limit}
                if start_date:
                    payload["start_date"] = start_date
                if end_date:
                    payload["end_date"] = end_date
                
                tool_called = "get_transactions"
                response = await self._send_request_reply(
                    IntentType.TRANSACTION_HISTORY,
                    payload
                )
                tool_result = response
                session.reset_intent()
                
                if "error" in response or response.get("status") == "error":
                    response_text = response.get("error", "Terjadi kesalahan saat mengambil transaksi.")
                else:
                    account_formatted = format_number_for_tts(account_number)
                    transactions = response.get("transactions", [])
                    total_account_txns = response.get("total_count", len(transactions))
                    
                    if not transactions:
                        response_text = f"Tidak ada transaksi ditemukan untuk rekening {account_formatted}."
                    else:
                        txn_texts = []
                        for txn in transactions:
                            txn_texts.append(format_transaction_for_tts(txn))
                        
                        count_displayed = len(transactions)
                        response_text = (
                            f"Berikut {count_displayed} transaksi untuk rekening {account_formatted}:\n"
                            + "\n".join(txn_texts)
                            + f"\nMenampilkan {count_displayed} dari total {total_account_txns} transaksi."
                        )
                
            elif intent_result.intent == IntentType.BLOCK_CARD:
                card_number = intent_result.entities.get("card_number")
                otp = intent_result.entities.get("otp")
                
                if not card_number:
                    session.active_intent = IntentType.BLOCK_CARD
                    response_text = "Untuk memblokir kartu, saya butuh nomor kartu 16 digit. Bisa sebutkan nomor kartunya?"
                    session.history.append({"role": "assistant", "content": response_text})
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                if not otp:
                    session.active_intent = IntentType.BLOCK_CARD
                    response_text = (
                        f"Untuk memblokir kartu {format_number_for_tts(card_number)}, "
                        f"saya butuh kode OTP 6 digit yang dikirim ke nomor terdaftar. "
                        f"Bisa sebutkan kode OTP-nya?"
                    )
                    session.history.append({"role": "assistant", "content": response_text})
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                tool_called = "block_card"
                response = await self._send_request_reply(
                    IntentType.BLOCK_CARD,
                    {"card_number": card_number, "identity_otp": otp, "reason": "lost_stolen"}
                )
                tool_result = response
                
                if "error" in response or response.get("status") == "error":
                    err_msg = response.get("error", "Terjadi kesalahan saat memblokir kartu.")
                    if "otp" in err_msg.lower():
                        session.active_intent = IntentType.BLOCK_CARD
                        session.entities.pop("otp", None)
                        response_text = f"{err_msg} Silakan sebutkan kembali kode OTP 6 digit yang benar."
                    else:
                        session.reset_intent()
                        response_text = err_msg
                else:
                    session.reset_intent()
                    card_formatted = format_number_for_tts(card_number)
                    ref_formatted = format_number_for_tts(response.get("block_reference", ""))
                    blocked_at = format_datetime_for_tts(response.get("blocked_at", ""))
                    
                    response_text = (
                        f"Kartu {card_formatted} berhasil diblokir. "
                        f"Nomor referensi pemblokiran: {ref_formatted}. "
                        f"Kartu diblokir pada {blocked_at}."
                    )
                
        except Exception as e:
            response_text = ERROR_TEMPLATES["generic_error"]
        
        session.history.append({"role": "assistant", "content": response_text})
        
        return AgentResponse(
            text=response_text,
            intent=intent_result.intent,
            tool_called=tool_called,
            tool_result=tool_result
        )


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Banking Agent with Solace")
    parser.add_argument("--mock", action="store_true", default=True, help="Use mock Solace client")
    parser.add_argument("--mqtt", action="store_true", help="Use MQTT protocol")
    parser.add_argument("--host", default="localhost", help="Solace host")
    parser.add_argument("--port", type=int, default=55555, help="Solace port")
    args = parser.parse_args()
    
    agent = BankingAgentSolace(
        solace_host=args.host,
        solace_port=args.port,
        use_mock=args.mock,
        use_mqtt=args.mqtt,
    )
    
    await agent.initialize()
    
    test_inputs = [
        "Halo",
        "Cek saldo rekening 1234567890",
        "Transaksi terakhir rekening 9876543210",
        "Blokir kartu 4567890123456789 OTP 123456",
        "Bantuan",
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