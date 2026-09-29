import os
import json
import asyncio
import httpx
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from app.services.intent_classifier import IndonesianIntentClassifier, IntentType, IntentResult
from app.core.prompts import SYSTEM_PROMPT, ERROR_TEMPLATES
from app.core.formatters import (
    format_rupiah_for_tts,
    format_number_for_tts,
    format_date_for_tts,
    format_datetime_for_tts,
    format_transaction_for_tts,
)
from app.schemas.banking import (
    BalanceEnquiryRequest,
    BalanceEnquiryResponse,
    TransactionListRequest,
    TransactionListResponse,
    CardBlockRequest,
    CardBlockResponse,
    AccountType,
)


@dataclass
class AgentResponse:
    text: str
    intent: IntentType
    tool_called: Optional[str] = None
    tool_result: Optional[Dict] = None
    requires_tts: bool = True


class BankingAPIClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=10.0)
    
    async def check_balance(self, account_number: str, account_type: str = "savings") -> BalanceEnquiryResponse:
        request = BalanceEnquiryRequest(account_number=account_number, account_type=AccountType(account_type))
        response = await self.client.post(
            f"{self.base_url}/api/v1/banking/balance",
            json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return BalanceEnquiryResponse(**response.json())
    
    async def get_transactions(self, account_number: str, limit: int = 5) -> TransactionListResponse:
        request = TransactionListRequest(account_number=account_number, limit=limit)
        response = await self.client.post(
            f"{self.base_url}/api/v1/banking/transactions",
            json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return TransactionListResponse(**response.json())
    
    async def block_card(self, card_number: str, identity_otp: str, reason: str = "lost_stolen") -> CardBlockResponse:
        request = CardBlockRequest(card_number=card_number, identity_otp=identity_otp, reason=reason)
        response = await self.client.post(
            f"{self.base_url}/api/v1/banking/block-card",
            json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return CardBlockResponse(**response.json())
    
    async def close(self):
        await self.client.aclose()


class BankingAgent:
    def __init__(self, api_base_url: str = "http://localhost:8000", llm_provider: str = "mock"):
        self.api_client = BankingAPIClient(api_base_url)
        self.intent_classifier = IndonesianIntentClassifier()
        self.llm_provider = llm_provider
        self.conversation_history: List[Dict] = []
    
    async def process(self, user_input: str) -> AgentResponse:
        self.conversation_history.append({"role": "user", "content": user_input})
        
        intent_result = self.intent_classifier.classify(user_input)
        
        if intent_result.intent == IntentType.GREETING:
            response_text = "Selamat datang di layanan pelanggan kami! Ada yang bisa saya bantu hari ini? Anda bisa menanyakan saldo, transaksi, atau memblokir kartu."
            return AgentResponse(text=response_text, intent=intent_result.intent)
        
        if intent_result.intent == IntentType.HELP:
            response_text = (
                "Saya bisa membantu Anda dengan:\n"
                "1. Cek saldo rekening - sebutkan nomor rekening\n"
                "2. Lihat riwayat transaksi - sebutkan nomor rekening\n"
                "3. Blokir kartu ATM/Debit/Kredit - sebutkan nomor kartu 16 digit dan OTP 6 digit\n\n"
                "Contoh: 'Cek saldo rekening 1234567890' atau 'Blokir kartu 4567890123456789 OTP 123456'"
            )
            return AgentResponse(text=response_text, intent=intent_result.intent)
        
        if intent_result.intent == IntentType.UNKNOWN:
            response_text = "Maaf, saya tidak mengerti permintaan Anda. Bisa tolong ulangi atau katakan 'bantuan' untuk melihat menu yang tersedia?"
            return AgentResponse(text=response_text, intent=intent_result.intent)
        
        tool_result = None
        tool_called = None
        
        try:
            if intent_result.intent == IntentType.CHECK_BALANCE:
                account_number = self.intent_classifier.extract_account_number(user_input)
                if not account_number:
                    response_text = "Untuk cek saldo, saya butuh nomor rekening Anda. Bisa sebutkan nomor rekening 10-16 digit?"
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                tool_called = "check_balance"
                result = await self.api_client.check_balance(account_number)
                tool_result = result.model_dump(mode="json")
                
                balance_formatted = format_rupiah_for_tts(result.balance)
                account_formatted = format_number_for_tts(account_number)
                last_updated = format_datetime_for_tts(result.last_updated.isoformat())
                
                response_text = (
                    f"Saldo rekening {account_formatted} ({result.account_type.value}) "
                    f"adalah {balance_formatted}. Terakhir diperbarui pada {last_updated}."
                )
                
            elif intent_result.intent == IntentType.TRANSACTION_HISTORY:
                account_number = self.intent_classifier.extract_account_number(user_input)
                if not account_number:
                    response_text = "Untuk lihat transaksi, saya butuh nomor rekening Anda. Bisa sebutkan nomor rekening?"
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                tool_called = "get_transactions"
                result = await self.api_client.get_transactions(account_number, limit=5)
                tool_result = result.model_dump(mode="json")
                
                account_formatted = format_number_for_tts(account_number)
                
                if not result.transactions:
                    response_text = f"Tidak ada transaksi ditemukan untuk rekening {account_formatted}."
                else:
                    txn_texts = []
                    for txn in result.transactions[:5]:
                        txn_dict = txn.model_dump(mode="json")
                        txn_texts.append(format_transaction_for_tts(txn_dict))
                    
                    response_text = (
                        f"Berikut {len(txn_texts)} transaksi terakhir untuk rekening {account_formatted}:\n"
                        + "\n".join(txn_texts)
                        + f"\nTotal {result.total_count} transaksi."
                    )
                
            elif intent_result.intent == IntentType.BLOCK_CARD:
                card_number = self.intent_classifier.extract_card_number(user_input)
                otp = self.intent_classifier.extract_otp(user_input)
                
                if not card_number:
                    response_text = "Untuk memblokir kartu, saya butuh nomor kartu 16 digit. Bisa sebutkan nomor kartunya?"
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                if not otp:
                    response_text = (
                        f"Untuk memblokir kartu {format_number_for_tts(card_number)}, "
                        f"saya butuh kode OTP 6 digit yang dikirim ke nomor terdaftar. "
                        f"Bisa sebutkan kode OTP-nya?"
                    )
                    return AgentResponse(text=response_text, intent=intent_result.intent)
                
                tool_called = "block_card"
                result = await self.api_client.block_card(card_number, otp)
                tool_result = result.model_dump(mode="json")
                
                card_formatted = format_number_for_tts(card_number)
                ref_formatted = format_number_for_tts(result.block_reference)
                blocked_at = format_datetime_for_tts(result.blocked_at.isoformat())
                
                response_text = (
                    f"Kartu {card_formatted} berhasil diblokir. "
                    f"Nomor referensi pemblokiran: {ref_formatted}. "
                    f"Kartu diblokir pada {blocked_at}."
                )
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                if "rekening" in str(e.response.text).lower() or "account" in str(e.response.text).lower():
                    acc_num = self.intent_classifier.extract_account_number(user_input) or "tidak diketahui"
                    response_text = ERROR_TEMPLATES["account_not_found"].format(account_number=format_number_for_tts(acc_num))
                else:
                    card_num = self.intent_classifier.extract_card_number(user_input) or "tidak diketahui"
                    response_text = ERROR_TEMPLATES["card_not_found"].format(card_number=format_number_for_tts(card_num))
            elif e.response.status_code == 400:
                if "otp" in str(e.response.text).lower():
                    response_text = ERROR_TEMPLATES["invalid_otp"]
                elif "sudah diblokir" in str(e.response.text).lower():
                    card_num = self.intent_classifier.extract_card_number(user_input) or "tidak diketahui"
                    response_text = ERROR_TEMPLATES["card_already_blocked"].format(
                        card_number=format_number_for_tts(card_num),
                        blocked_at="sebelumnya"
                    )
                else:
                    response_text = ERROR_TEMPLATES["generic_error"]
            else:
                response_text = ERROR_TEMPLATES["generic_error"]
        
        except Exception as e:
            response_text = ERROR_TEMPLATES["generic_error"]
        
        self.conversation_history.append({"role": "assistant", "content": response_text})
        
        return AgentResponse(
            text=response_text,
            intent=intent_result.intent,
            tool_called=tool_called,
            tool_result=tool_result
        )
    
    async def close(self):
        await self.api_client.close()


async def main():
    agent = BankingAgent()
    
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