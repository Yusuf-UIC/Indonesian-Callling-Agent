from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api.banking_routes import router as banking_router

app = FastAPI(
    title="Indonesia IVR Speech AI & Telephony Gateway",
    description="Mock API & Twilio Media Stream Gateway untuk layanan perbankan Indonesia IVR Speech AI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(banking_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "mock-banking-and-telephony-api"}


@app.post("/webhook/voice", tags=["Telephony"], summary="Twilio Voice Webhook")
async def twilio_voice_webhook(request: Request):
    """Handle incoming Twilio voice webhook.
    
    Receives incoming call notifications from Twilio and returns TwiML
    to connect the call to the Media Stream WebSocket.
    """
    call_sid = request.headers.get("x-call-sid", "simulated-call-id")
    websocket_url = f"wss://{request.headers.get('host', 'localhost:8000')}/ws/telephony"
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{websocket_url}" />
    </Connect>
</Response>"""
    return HTMLResponse(content=twiml, media_type="application/xml")


# Customize OpenAPI schema so WebSocket endpoint is documented in Swagger UI (/docs)
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Manually document the /ws/telephony WebSocket endpoint in Swagger UI
    openapi_schema["paths"]["/ws/telephony"] = {
        "get": {
            "summary": "Twilio Media Stream WebSocket Endpoint",
            "description": (
                "WebSocket endpoint for real-time bi-directional audio streaming from Twilio Media Streams.\n\n"
                "**Audio Format:**\n"
                "- Input: 8kHz mu-law base64 encoded audio frames\n"
                "- Pipeline: 16kHz linear PCM audio + Silero VAD + Speech-to-Text\n"
                "- Output: 8kHz mu-law base64 encoded TTS audio frames"
            ),
            "tags": ["Telephony"],
            "responses": {
                "101": {"description": "Switching Protocols - WebSocket Upgrade Success"},
                "400": {"description": "Bad Request"}
            }
        }
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi


@app.get("/")
async def root():
    return {
        "message": "Indonesia IVR Speech AI & Telephony Gateway API",
        "endpoints": {
            "balance": "POST /api/v1/banking/balance",
            "transactions": "POST /api/v1/banking/transactions",
            "block_card": "POST /api/v1/banking/block-card",
            "twilio_webhook": "POST /webhook/voice",
            "telephony_websocket": "ws://localhost:8000/ws/telephony",
        },
        "test_accounts": ["1234567890", "9876543210", "1122334455"],
        "test_cards": ["4567890123456789", "4567890123456790", "4567890123456791"],
        "test_otp": "123456",
    }