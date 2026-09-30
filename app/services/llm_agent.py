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
    LLM-Powered Reasoning Agent supporting Gemini, Groq, NVIDIA NIM, Cloudflare,
    and local vLLM. Tool calls are routed through SAM Orchestrator Agent over the
    Solace Event Backbone.
    """
    def __init__(
        self,
        solace_client: Optional[SolaceClient] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        use_sam: bool = True,
    ):
        self.solace_client = solace_client
        self.provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower()
        self.use_sam = use_sam
        
        if self.provider == "nvidia_nim":
            self.api_key = api_key or os.getenv("NVIDIA_NIM_API_KEY")
            self.model = model or os.getenv("NVIDIA_NIM_MODEL", "meta/llama-3.3-70b-instruct")
            self.base_url = os.getenv("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
        elif self.provider == "gemini":
            self.api_key = api_key or os.getenv("GEMINI_API_KEY")
            self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif self.provider == "groq":
            self.api_key = api_key or os.getenv("GROQ_API_KEY")
            self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.base_url = "https://api.groq.com/openai/v1"
        elif self.provider == "cloudflare":
            self.api_key = api_key or os.getenv("CLOUDFLARE_API_TOKEN")
            account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
            self.model = model or os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct")
            self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        else:
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            self.model = model or "gpt-3.5-turbo"
            self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

        # Initialize SAM Orchestrator if enabled
        self.sam_orchestrator = None
        if self.use_sam and solace_client:
            from app.services.sam.orchestrator_agent import SAMOrchestratorAgent
            self.sam_orchestrator = SAMOrchestratorAgent(solace_client=solace_client)
            logger.info(f"[LLM Agent] SAM Orchestrator enabled (provider: {self.provider}, model: {self.model})")
        else:
            logger.info(f"[LLM Agent] Direct Solace mode (provider: {self.provider}, model: {self.model})")

    async def _execute_tool(
        self, tool_name: str, args: Dict[str, Any], session_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Execute tool call through SAM Orchestrator (multi-agent mesh) or direct Solace.
        SAM routes: check_balance -> AccountAgent, block_card -> CardSecurityAgent.
        """
        # Route through SAM Orchestrator if available
        if self.sam_orchestrator:
            logger.info(f"[LLM Agent] Routing tool '{tool_name}' through SAM Orchestrator")
            sam_response = await self.sam_orchestrator.route(
                tool_name=tool_name,
                tool_args=args,
                session_id=session_id,
            )
            if sam_response.error:
                return {"error": sam_response.error, **sam_response.payload}
            return sam_response.payload

        # Fallback: Direct Solace request-reply (no SAM mesh)
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
        import uuid
        correlation_id = f"llm-{tool_name}-{uuid.uuid4().hex[:8]}"
        
        event = SolaceEvent(
            event_type=req_type,
            correlation_id=correlation_id,
            payload=args,
        )

        try:
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
        """Process user input through LLM and execute Solace tools via SAM mesh if requested."""
        PLACEHOLDER_KEYS = {
            "your_gemini_api_key_here",
            "your_groq_api_key_here",
            "your_nvidia_nim_api_key_here",
            "your_cloudflare_api_token_here",
            "your_openai_api_key_here",
        }
        if not self.api_key or self.api_key in PLACEHOLDER_KEYS:
            logger.warning("No valid API key provided for LLM.")
            return LLMResponse(text="API key belum dikonfigurasi. Silakan isi API key di file .env")

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

                    logger.info(f"[LLM Agent] Tool call: {tool_name}({tool_args})")

                    # Execute Tool through SAM Orchestrator -> Domain Agent -> Solace Banking
                    tool_result = await self._execute_tool(tool_name, tool_args, session_id)

                    # Append Assistant tool request & Tool result for final LLM synthesis
                    messages.append(choice)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", "call_1"),
                        "name": tool_name,
                        "content": json.dumps(tool_result),
                    })

                    # Second LLM call to generate natural Indonesian response from tool result
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

