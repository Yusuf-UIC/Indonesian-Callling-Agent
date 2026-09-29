import time
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from app.services.semantic_intent import IntentType, IntentResult

@dataclass
class SessionState:
    session_id: str
    active_intent: Optional[IntentType] = None
    entities: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, str]] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)
    
    def is_expired(self, ttl_seconds: int = 3600) -> bool:
        """Check if session has expired based on TTL."""
        return (time.time() - self.last_updated) > ttl_seconds

    def reset_intent(self):
        """Reset active intent and collected entities after request completion or cancellation."""
        self.active_intent = None
        self.entities = {}

    def touch(self):
        """Update last active timestamp."""
        self.last_updated = time.time()


class SessionManager:
    """Manages active user sessions, conversation context, and TTL eviction."""
    
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self.sessions: Dict[str, SessionState] = {}
        
    def get_session(self, session_id: str) -> SessionState:
        """Get or create session, evicting if expired."""
        now = time.time()
        
        if session_id in self.sessions:
            session = self.sessions[session_id]
            if session.is_expired(self.ttl_seconds):
                # TTL Expired - Reset session state
                session.active_intent = None
                session.entities = {}
                session.history = []
                session.last_updated = now
            else:
                session.touch()
            return session
            
        new_session = SessionState(session_id=session_id, last_updated=now)
        self.sessions[session_id] = new_session
        return new_session

    def resolve_contextual_intent(
        self,
        session: SessionState,
        user_input: str,
        classifier_result: IntentResult
    ) -> IntentResult:
        """
        Resolves intent and entities using session history and active slot-filling context.
        """
        user_input_clean = user_input.strip()
        
        # Check if user explicitly wants to cancel or start over
        if any(cancel_word in user_input_clean.lower() for cancel_word in ["batal", "cancel", "reset", "mulai lagi"]):
            session.reset_intent()
            return IntentResult(intent=IntentType.HELP, confidence=1.0, entities={})

        # Case 1: Classifier identified a clear, actionable intent
        if classifier_result.intent not in (IntentType.UNKNOWN, IntentType.GREETING, IntentType.HELP):
            # If user switches intent, update active_intent in session
            if session.active_intent != classifier_result.intent:
                session.active_intent = classifier_result.intent
                session.entities = classifier_result.entities.copy()
            else:
                # Accumulate entities
                session.entities.update(classifier_result.entities)
                
            return IntentResult(
                intent=session.active_intent,
                confidence=classifier_result.confidence,
                entities=session.entities.copy()
            )

        # Case 2: Classifier returned UNKNOWN, but session has an active_intent (slot filling in progress)
        if session.active_intent is not None:
            extracted_any = False
            
            if session.active_intent == IntentType.BLOCK_CARD:
                # Try extracting 16-digit card number if missing
                if "card_number" not in session.entities:
                    card_match = re.search(r'\b\d{16}\b', user_input_clean.replace(" ", "").replace("-", ""))
                    if card_match:
                        session.entities["card_number"] = card_match.group(0)
                        extracted_any = True
                
                # Try extracting 6-digit OTP if missing
                if "otp" not in session.entities:
                    # Look for 6 consecutive digits or digit words
                    digits_only = "".join(re.findall(r'\d', user_input_clean))
                    if len(digits_only) == 6:
                        session.entities["otp"] = digits_only
                        extracted_any = True
                    else:
                        otp_match = re.search(r'\b\d{6}\b', user_input_clean)
                        if otp_match:
                            session.entities["otp"] = otp_match.group(0)
                            extracted_any = True

            elif session.active_intent in (IntentType.CHECK_BALANCE, IntentType.TRANSACTION_HISTORY):
                # Try extracting 10-16 digit account number if missing
                if "account_number" not in session.entities:
                    acc_match = re.search(r'\b\d{10,16}\b', user_input_clean.replace(" ", "").replace("-", ""))
                    if acc_match:
                        session.entities["account_number"] = acc_match.group(0)
                        extracted_any = True

            if extracted_any:
                return IntentResult(
                    intent=session.active_intent,
                    confidence=0.9,
                    entities=session.entities.copy()
                )

        # Return original classifier result if no context matches
        return classifier_result


session_manager = SessionManager(ttl_seconds=3600)
