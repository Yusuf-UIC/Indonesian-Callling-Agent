import logging
from typing import Optional
from app.services.sam.sam_event import SAMEvent, SAMHeader, SAMAgentType
from app.services.solace_client import SolaceClient, SolaceEvent, SolaceEventType

logger = logging.getLogger(__name__)


class SAMCardSecurityAgent:
    """
    Specialized SAM Agent for card security operations: Card Blocking with OTP verification.
    Communicates via Solace Agent Mesh topics (sam/v1/agent/banking/card/request).
    """

    def __init__(self, solace_client: Optional[SolaceClient] = None):
        self.solace_client = solace_client
        self.agent_name = SAMAgentType.CARD_SECURITY.value
        self.topic_request = "sam/v1/agent/banking/card/request"
        self.topic_response = "sam/v1/agent/banking/card/response"

    async def handle_event(self, sam_event: SAMEvent) -> SAMEvent:
        """Handle incoming SAM card security requests."""
        intent = sam_event.intent
        payload = sam_event.payload
        session_id = sam_event.header.session_id

        logger.info(f"[SAM Card Security Agent] Processing intent '{intent}' for session '{session_id}'")

        if not self.solace_client:
            return SAMEvent(
                header=SAMHeader(
                    session_id=session_id,
                    correlation_id=sam_event.header.correlation_id,
                    sender_agent=self.agent_name,
                    target_agent=sam_event.header.sender_agent,
                ),
                topic=self.topic_response,
                intent=intent,
                payload={},
                error="Solace client not initialized",
            )

        if intent == "block_card":
            card_number = payload.get("card_number")
            identity_otp = payload.get("identity_otp")

            if not card_number or len(card_number) != 16:
                return SAMEvent(
                    header=SAMHeader(
                        session_id=session_id,
                        correlation_id=sam_event.header.correlation_id,
                        sender_agent=self.agent_name,
                        target_agent=sam_event.header.sender_agent,
                    ),
                    topic=self.topic_response,
                    intent=intent,
                    payload={"needs_slot": "card_number"},
                    error="Nomor kartu 16 digit diperlukan untuk memblokir kartu.",
                )

            if not identity_otp or len(identity_otp) != 6:
                return SAMEvent(
                    header=SAMHeader(
                        session_id=session_id,
                        correlation_id=sam_event.header.correlation_id,
                        sender_agent=self.agent_name,
                        target_agent=sam_event.header.sender_agent,
                    ),
                    topic=self.topic_response,
                    intent=intent,
                    payload={"needs_slot": "identity_otp", "card_number": card_number},
                    error="Kode OTP 6 digit diperlukan untuk verifikasi pemblokiran kartu.",
                )

            # All slots present — execute block_card on Solace banking backbone
            req_event = SolaceEvent(
                event_type=SolaceEventType.BLOCK_CARD_REQUEST,
                correlation_id=sam_event.header.correlation_id,
                payload={
                    "card_number": card_number,
                    "identity_otp": identity_otp,
                    "reason": payload.get("reason", "lost_stolen"),
                },
            )
            try:
                res_event = await self.solace_client.request_reply(
                    req_event, SolaceEventType.BLOCK_CARD_RESPONSE.value, timeout=10.0
                )
                res_payload = res_event.payload

                # Check for OTP validation errors from banking service
                if res_payload.get("status") == "error" or "error" in res_payload:
                    err_msg = res_payload.get("error", "")
                    if "otp" in err_msg.lower():
                        return SAMEvent(
                            header=SAMHeader(
                                session_id=session_id,
                                correlation_id=sam_event.header.correlation_id,
                                sender_agent=self.agent_name,
                                target_agent=sam_event.header.sender_agent,
                            ),
                            topic=self.topic_response,
                            intent=intent,
                            payload={"needs_slot": "identity_otp", "card_number": card_number},
                            error="Kode OTP tidak valid. Silakan masukkan kode OTP 6 digit yang benar.",
                        )

            except Exception as e:
                res_payload = {"error": f"Failed to block card: {str(e)}"}

        else:
            res_payload = {"error": f"Unsupported intent '{intent}' for SAM Card Security Agent"}

        return SAMEvent(
            header=SAMHeader(
                session_id=session_id,
                correlation_id=sam_event.header.correlation_id,
                sender_agent=self.agent_name,
                target_agent=sam_event.header.sender_agent,
            ),
            topic=self.topic_response,
            intent=intent,
            payload=res_payload,
        )
