from app.telephony.twilio_gateway import TwilioGateway
from app.services.agent_solace import BankingAgentSolace
from app.services.stt_service import STTService
from app.services.semantic_intent import SemanticIntentClassifier

agent = BankingAgentSolace(use_mock=True, use_mqtt=False)
stt = STTService(use_local_fallback=True)
intent = SemanticIntentClassifier()
gateway = TwilioGateway(agent, stt, SemanticIntentClassifier())

print('Routes:')
for route in gateway.app.routes:
    print(f'  {route.path} - {route.methods}')

print()
print('OpenAPI schema paths:')
schema = gateway.app.openapi()
for path in schema.get('paths', {}):
    print(f'  {path}: {list(schema["paths"][path].keys())}')