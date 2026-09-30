import logging
from typing import Dict, Any, Optional
from app.services.sam.sam_event import SAMEvent, SAMHeader, SAMAgentType
from app.services.solace_client import SolaceClient, SolaceEvent, SolaceEventType

logger = logging.getLogger(__name__)


class SAMAccountOperationsAgent:
    """
    Specialized SAM Agent for handling account queries: Balance Enquiry & Transaction History.
    Communicates via Solace Agent Mesh topics (sam/v1/agent/banking/account/request).
    """

    def __init__(self, solace_client: Optional[SolaceClient] = None):
        self.solace_client = solace_client
        self.agent_name = SAMAgentType.ACCOUNT_OPERATIONS.value
        self.topic_request = "sam/v1/agent/banking/account/request"
        self.topic_response = "sam/v1/agent/banking/account/response"

    async def handle_event(self, sam_event: SAMEvent) -> SAMEvent:
        """Handle incoming SAM account requests and call Solace core banking services."""
        intent = sam_event.intent
        payload = sam_event.payload
        session_id = sam_event.header.session_id

        logger.info(f"[SAM Account Agent] Processing intent '{intent}' for session '{session_id}'")

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

        if intent == "check_balance":
            req_event = SolaceEvent(
                event_type=SolaceEventType.BALANCE_REQUEST,
                correlation_id=sam_event.header.correlation_id,
                payload=payload,
            )
            try:
                res_event = await self.solace_client.request_reply(
                    req_event, SolaceEventType.BALANCE_RESPONSE.value, timeout=10.0
                )
                res_payload = res_event.payload
            except Exception as e:
                res_payload = {"error": f"Failed to check balance: {str(e)}"}

        elif intent == "get_transactions":
            req_event = SolaceEvent(
                event_type=SolaceEventType.TRANSACTIONS_REQUEST,
                correlation_id=sam_event.header.correlation_id,
                payload=payload,
            )
            try:
                res_event = await self.solace_client.request_reply(
                    req_event, SolaceEventType.TRANSACTIONS_RESPONSE.value, timeout=10.0
                )
                res_payload = res_event.payload
            except Exception as e:
                res_payload = {"error": f"Failed to retrieve transactions: {str(e)}"}

        else:
            res_payload = {"error": f"Unsupported intent '{intent}' for SAM Account Operations Agent"}

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
