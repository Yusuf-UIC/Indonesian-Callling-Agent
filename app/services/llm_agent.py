import os
import json
import logging
import httpx
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from dotenv import load_dotenv

from app.core.formatters import (
    format_rupiah_for_tts,
    format_number_for_tts,
    format_datetime_for_tts,
    format_transaction_for_tts,
)
from app.services.solace_client import SolaceClient, SolaceEvent, SolaceEventType
from app.services.session_manager import session_manager, SessionState

load_dotenv()

logger = logging.getLogger(__name__)

# System prompt for Indonesian Banking Assistant
SYSTEM_PROMPT = """Anda adalah asisten layanan pelanggan perbankan Indonesia yang profesional, ramah, dan santun.

ATURAN UTAMA:
1. HANYA gunakan data dari fungsi/tool perbankan yang tersedia untuk jawaban terkait saldo, transaksi, atau blokir kartu. JANGAN PERNAH mengarang angka atau informasi perbankan.
2. Jika pengguna ingin mengecek saldo tapi belum memberikan nomor rekening (10-16 digit), minta nomor rekeningnya secara ramah.
3. Jika pengguna ingin memblokir kartu, Anda membutuhkan nomor kartu 16 digit dan kode OTP 6 digit. Jika salah satu belum ada, minta digit yang belum diberikan.
4. Jika pengguna ingin melihat transaksi terakhir tapi belum memberikan nomor rekening, minta nomor rekeningnya.
5. Format jawaban agar mudah dan enak dibaca oleh Text-to-Speech (TTS) Bahasa Indonesia:
   - Sebutkan nominal uang dalam rupiah (contoh: "delapan ratus lima puluh ribu rupiah").
   - Sebutkan digit nomor kartu/rekening secara terpisah jika perlu.
6. Untuk pertanyaan umum (seperti salam, bantuan, produk perbankan, jam operasional), jawab langsung secara ramah dan informatif tanpa memanggil tool.
"""

# Tool definitions in OpenAI / Gemini function calling format
BANKING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_balance",
            "description": "Mengecek saldo rekening perbankan pengguna berdasarkan nomor rekening.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_number": {
                        "type": "string",
                        "description": "Nomor rekening perbankan 10-16 digit."
                    },
                    "account_type": {
                        "type": "string",
                        "description": "Jenis rekening, contoh: savings, checking, deposit. Default: savings."
                    }
                },
                "required": ["account_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_transactions",
            "description": "Mengambil riwayat transaksi terakhir dari rekening pengguna.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_number": {
                        "type": "string",
                        "description": "Nomor rekening perbankan 10-16 digit."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Jumlah transaksi yang ingin diambil (default 5)."
                    }
                },
                "required": ["account_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "block_card",
            "description": "Memblokir kartu debit atau kredit yang hilang/dicuri. Membutuhkan nomor kartu 16 digit dan OTP 6 digit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_number": {
                        "type": "string",
                        "description": "Nomor kartu debit/kredit 16 digit."
                    },
                    "identity_otp": {
                        "type": "string",
                        "description": "Kode verifikasi OTP 6 digit yang dikirim ke HP terdaftar."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Alasan pemblokiran (contoh: lost_stolen, suspicious_activity)."
                    }
                },
                "required": ["card_number", "identity_otp"]
            }
        }
    }
]


@dataclass
class LLMResponse:
    text: str
    tool_called: Optional[str] = None
    tool_result: Optional[Dict[str, Any]] = None


class LLMAgentSolace:
    """
    LLM-Powered Reasoning Agent supporting Gemini (Google AI Studio), Groq, OpenRouter,
    and local vLLM, executing tool calls over the Solace Event Backbone.
    """
    def __init__(
        self,
        solace_client: Optional[SolaceClient] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.solace_client = solace_client
        self.provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY")
        
        if self.provider == "gemini":
            self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif self.provider == "groq":
            self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.base_url = "https://api.groq.com/openai/v1"
        else:
            self.model = model or "gpt-3.5-turbo"
            self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    async def _execute_solace_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tool call over Solace MQTT/SMF Broker."""
        if not self.solace_client:
            return {"error": "Solace client not connected"}

        topic_map = {
            "check_balance": (SolaceEventType.BALANCE_REQUEST, SolaceEventType.BALANCE_RESPONSE),
            "get_transactions": (SolaceEventType.TRANSACTIONS_REQUEST, SolaceEventType.TRANSACTIONS_RESPONSE),
            "block_card": (SolaceEventType.BLOCK_CARD_REQUEST, SolaceEventType.BLOCK_CARD_RESPONSE),
        }

        if tool_name not in topic_map:
            return {"error": f"Unknown tool name: {tool_name}"}

        req_type, resp_type = topic_map[tool_name]
        correlation_id = f"llm-{tool_name}-{int(httpx._utils.get_environment_proxies().keys().__len__())}"
        
        event = SolaceEvent(
            event_type=req_type,
            correlation_id=correlation_id,
            payload=args,
        )

        try:
            logger.info(f"[Solace LLM] Publishing tool request {req_type.value} with args {args}")
            response_event = await self.solace_client.request_reply(
                event, resp_type.value, timeout=10.0
            )
            return response_event.payload
        except Exception as e:
            logger.error(f"[Solace LLM] Error executing {tool_name}: {e}")
            return {"error": str(e)}

    async def process(
        self,
        user_input: str,
        session_id: str = "default_cli_session",
        history: Optional[List[Dict[str, Any]]] = None
    ) -> LLMResponse:
        """Process user input through Gemini/Groq LLM and execute Solace tools if requested."""
        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            logger.warning("No valid API key provided for LLM. Falling back to default message.")
            return LLMResponse(text="API key Gemini belum dikonfigurasi. Silakan isi GEMINI_API_KEY di file .env")

        # Build full message conversation context
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": BANKING_TOOLS,
            "tool_choice": "auto",
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]["message"]

                # Check if LLM requested a Tool Call
                if "tool_calls" in choice and choice["tool_calls"]:
                    tool_call = choice["tool_calls"][0]
                    tool_name = tool_call["function"]["name"]
                    try:
                        tool_args = json.loads(tool_call["function"]["arguments"])
                    except Exception:
                        tool_args = {}

                    logger.info(f"[LLM Agent] Decided to call tool: {tool_name} with args {tool_args}")

                    # Execute Tool on Solace Backbone
                    tool_result = await self._execute_solace_tool(tool_name, tool_args)

                    # Append Assistant tool request & Tool result to conversation for final synthesis
                    messages.append(choice)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", "call_1"),
                        "name": tool_name,
                        "content": json.dumps(tool_result),
                    })

                    # Second call to LLM to generate user-friendly response from tool result
                    synth_payload = {
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.2,
                    }
                    synth_resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=synth_payload,
                    )
                    synth_resp.raise_for_status()
                    synth_data = synth_resp.json()
                    final_text = synth_data["choices"][0]["message"]["content"]

                    return LLMResponse(
                        text=final_text,
                        tool_called=tool_name,
                        tool_result=tool_result,
                    )

                else:
                    final_text = choice.get("content", "")
                    return LLMResponse(text=final_text)

            except Exception as e:
                logger.error(f"[LLM Agent] API error: {e}")
                return LLMResponse(text=f"Maaf, terjadi masalah koneksi ke layanan AI: {str(e)}")
