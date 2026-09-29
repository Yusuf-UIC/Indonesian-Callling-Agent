"""
Twilio Media Stream Gateway
============================
FastAPI WebSocket endpoint for Twilio Media Streams integration.
Handles real-time audio streaming between phone calls and the voice pipeline.
"""

"""
Twilio Media Stream Gateway
============================
FastAPI WebSocket endpoint for Twilio Media Streams integration.
Handles real-time audio streaming between phone calls and the voice pipeline.
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Dict, Optional, Set
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import numpy as np

from app.services.agent_solace import BankingAgentSolace
from app.services.stt_service import STTService
from app.services.semantic_intent import SemanticIntentClassifier, IntentType
from app.core.formatters import clean_text_for_tts
from app.telephony.audio_bridge import audio_bridge
from app.core.formatters import clean_text_for_tts

logger = logging.getLogger(__name__)


@dataclass
class CallSession:
    """Represents an active phone call session."""
    call_sid: str
    stream_sid: str
    agent: 'BankingAgentSolace'
    stt_service: 'STTService'
    active: bool = True
    audio_buffer: bytearray = field(default_factory=bytearray)
    last_activity: float = 0.0


class TwilioGateway:
    """Twilio Media Stream Gateway for handling phone call audio."""
    
    def __init__(
        self,
        agent: 'BankingAgentSolace',
        stt_service: 'STTService',
        intent_classifier: 'SemanticIntentClassifier'
    ):
        self.agent = agent
        self.stt_service = stt_service
        self.intent_classifier = intent_classifier
        self.active_calls: Dict[str, CallSession] = {}
        self.app = FastAPI(title="Twilio IVR Gateway")
        self._setup_routes()
        self._setup_middleware()
        self._customize_openapi()
    
    def _setup_middleware(self):
        """Configure FastAPI middleware."""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def _customize_openapi(self):
        """Customize OpenAPI schema to include WebSocket endpoint."""
        if self.app.openapi_schema:
            return self.app.openapi_schema
        
        openapi_schema = self.app.openapi()
        
        # Add WebSocket endpoint to paths
        openapi_schema["paths"]["/ws/telephony"] = {
            "get": {
                "summary": "Twilio Media Stream WebSocket",
"description": (
                    "WebSocket endpoint for Twilio Media Streams real-time audio streaming.\n\n"
                    "**Events handled:**\n"
                    "- `connected`: Initial connection established\n"
                    "- `start`: Media stream started with call metadata\n"
                    "- `media`: Base64-encoded mu-law audio chunks (8kHz)\n"
                    "- `stop`: Media stream ended\n"
                    "- `mark`: Audio playback marker reached\n\n"
                    "**Audio Format:**\n"
                    "- Input: 8kHz mu-law (base64 encoded)\n"
                    "- Internal processing: 16kHz PCM linear\n"
                    "- Output: 8kHz mu-law (base64 encoded)\n\n"
                    "**Twilio Integration:**\n"
                    "Configure your Twilio webhook to point to `/webhook/voice` which returns\n"
                    "TwiML connecting to this WebSocket endpoint."
                ),
                "tags": ["Telephony"],
                "responses": {
                    "101": {
                        "description": "Switching Protocols - WebSocket upgrade",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        }
                    },
                    "400": {"description": "Bad Request - Invalid WebSocket upgrade"},
                    "401": {"description": "Unauthorized - Invalid Twilio signature"},
                    "500": {"description": "Internal Server Error"}
                }
            }
        }
        
        self.app.openapi_schema = openapi_schema
        return self.app.openapi_schema
    
    def _setup_routes(self):
        """Setup FastAPI routes."""
        
        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "service": "twilio-gateway"}
        
        @self.app.post("/webhook/voice", tags=["Telephony"], summary="Twilio Voice Webhook")
        async def twilio_voice_webhook(request: Request):
            """Handle incoming Twilio voice webhook.
            
            Receives incoming call notifications from Twilio and returns TwiML
            to connect the call to the Media Stream WebSocket.
            
            **Returns:** TwiML response with `<Connect><Stream>` to establish Media Stream.
            """
            form_data = await request.form()
            call_sid = form_data.get("CallSid")
            from_number = form_data.get("From")
            to_number = form_data.get("To")
            
            logger.info(f"Incoming call: {call_sid} from {from_number} to {to_number}")
            
            # Generate TwiML response to connect to WebSocket
            websocket_url = f"wss://{request.headers.get('host')}/ws/telephony"
            
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{websocket_url}" />
    </Connect>
</Response>"""

            return HTMLResponse(content=twiml, media_type="application/xml")
        
        @self.app.websocket("/ws/telephony", name="twilio_media_stream")
        async def websocket_endpoint(websocket: WebSocket):
            """Handle Twilio Media Stream WebSocket connection.
            
            This WebSocket endpoint receives real-time audio streams from Twilio Media Streams.
            
            **Events handled:**
            - `connected`: Initial connection established
            - `start`: Media stream started with call metadata
            - `media`: Base64-encoded mu-law audio chunks (8kHz)
            - `stop`: Media stream ended
            - `mark`: Audio playback marker reached
            
            **Audio Format:**
            - Input: 8kHz mu-law (base64 encoded)
            - Internal processing: 16kHz PCM linear
            - Output: 8kHz mu-law (base64 encoded)
            
            **Twilio Integration:**
            Configure your Twilio webhook to point to `/webhook/voice` which returns
            TwiML connecting to this WebSocket endpoint.
            """
            await websocket.accept()
            
            call_sid = None
            stream_sid = None
            session = None
            
            try:
                while True:
                    message = await websocket.receive_text()
                    data = json.loads(message)
                    event = data.get("event")
                    
                    if event == "connected":
                        logger.info("Twilio Media Stream connected")
                        
                    elif event == "start":
                        stream_sid = data.get("streamSid")
                        call_sid = data.get("callSid")
                        
                        logger.info(f"Media stream started: {stream_sid} for call {call_sid}")
                        
                        # Initialize session
                        session = CallSession(
                            call_sid=call_sid,
                            stream_sid=stream_sid,
                            agent=self.agent,
                            stt_service=self.stt_service
                        )
                        self.active_calls[call_sid] = session
                        
                        # Initialize semantic intent classifier
                        semantic_intent_classifier.initialize()
                        
                    elif event == "media":
                        if not session:
                            continue
                        
                        media = data.get("media", {})
                        payload = media.get("payload")
                        
                        if payload:
                            # Process incoming audio
                            await self._process_audio_chunk(session, payload)
                            
                    elif event == "stop":
                        logger.info(f"Media stream stopped for call {call_sid}")
                        if session:
                            await self._end_call(session)
                        break
                        
                    elif event == "mark":
                        # Mark message - audio playback completed
                        mark_name = data.get("mark", {}).get("name")
                        logger.debug(f"Mark received: {mark_name}")
                        
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for call {call_sid}")
                if session:
                    await self._end_call(session)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if session:
                    await self._end_call(session)
    
    async def _process_audio_chunk(self, session: 'CallSession', mulaw_base64: str):
        """Process incoming audio chunk from Twilio."""
        try:
            # Convert Twilio mu-law to 16kHz PCM
            pcm_audio = audio_bridge.process_incoming_audio(mulaw_base64)
            
            if len(pcm_audio) > 0:
                session.audio_buffer.extend(pcm_audio.tobytes())
                
                # Process when we have enough audio (e.g., 1 second = 16000 samples)
                if len(session.audio_buffer) >= 32000:  # ~1 second at 16kHz
                    await self._process_speech(session)
                    
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
    
    async def _process_speech(self, session: 'CallSession'):
        """Process accumulated audio buffer through STT and agent."""
        try:
            if len(session.audio_buffer) == 0:
                return
            
            # Convert buffer to numpy array
            audio_data = np.frombuffer(session.audio_buffer, dtype=np.int16)
            session.audio_buffer.clear()
            
            # Transcribe using STT
            audio_bytes = audio_bridge._audio_to_wav_bytes(audio_data)
            text = await session.stt_service.listen_and_transcribe()
            
            if not text:
                return
            
            logger.info(f"Transcribed: {text}")
            
            # Process with agent
            response = await session.agent.process(text)
            
            # Send response via TTS
            await self._send_tts_response(session, response.text)
            
        except Exception as e:
            logger.error(f"Error processing speech: {e}")
    
    async def _send_tts_response(self, session: 'CallSession', text: str):
        """Send TTS response back to caller via Twilio."""
        try:
            # Clean text for TTS
            cleaned_text = clean_text_for_tts(text)
            
            # Generate TTS audio
            # This would use Edge-TTS or Cartesia
            # For now, we'll send a placeholder
            logger.info(f"TTS Response: {cleaned_text}")
            
            # In production, this would:
            # 1. Synthesize speech using Edge-TTS/Cartesia
            # 2. Convert to 8kHz mu-law
            # 3. Send as media message via WebSocket
            
        except Exception as e:
            logger.error(f"Error sending TTS response: {e}")
    
    async def _end_call(self, session: 'CallSession'):
        """Clean up call session."""
        try:
            if session.call_sid in self.active_calls:
                del self.active_calls[session.call_sid]
            logger.info(f"Call ended: {session.call_sid}")
        except Exception as e:
            logger.error(f"Error ending call: {e}")


# Global gateway instance
twilio_gateway: Optional[TwilioGateway] = None


def create_twilio_gateway(
    agent: 'BankingAgentSolace',
    stt_service: 'STTService',
    intent_classifier: 'SemanticIntentClassifier'
) -> TwilioGateway:
    """Factory function to create Twilio gateway instance."""
    global twilio_gateway
    twilio_gateway = TwilioGateway(agent, stt_service, intent_classifier)
    return twilio_gateway


def get_twilio_gateway() -> Optional[TwilioGateway]:
    """Get the global Twilio gateway instance."""
    return twilio_gateway