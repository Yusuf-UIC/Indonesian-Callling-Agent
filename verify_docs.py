from app.telephony.twilio_gateway import TwilioGateway
from app.services.agent_solace import BankingAgentSolace
from app.services.stt_service import STTService
from app.services.semantic_intent import SemanticIntentClassifier

agent = BankingAgentSolace(use_mock=True, use_mqtt=False)
stt = STTService(use_local_fallback=True)
intent = SemanticIntentClassifier()
gateway = TwilioGateway(agent, stt, SemanticIntentClassifier())

ws_path = '/ws/telephony'
schema = gateway.app.openapi()
if ws_path in schema.get('paths', {}):
    print('WebSocket endpoint documented in OpenAPI: YES')
    ws_info = schema['paths']['/ws/telephony'].get('get', {})
    print(f'  Summary: {ws_info.get("summary", "N/A")}')
    print(f'  Tags: {ws_info.get("tags", [])}')
    print(f'  Responses: {list(ws_info.get("responses", {}).keys())}')
else:
    print('WebSocket endpoint NOT documented in OpenAPI')