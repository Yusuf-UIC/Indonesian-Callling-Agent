import logging
from typing import Optional, Dict, Any
from app.services.sam.sam_event import SAMEvent, SAMHeader, SAMAgentType
from app.services.sam.account_agent import SAMAccountOperationsAgent
from app.services.sam.card_security_agent import SAMCardSecurityAgent
from app.services.solace_client import SolaceClient

logger = logging.getLogger(__name__)


# Maps LLM tool names to SAM agent types
TOOL_TO_AGENT_MAP: Dict[str, SAMAgentType] = {
    "check_balance": SAMAgentType.ACCOUNT_OPERATIONS,
    "get_transactions": SAMAgentType.ACCOUNT_OPERATIONS,
    "block_card": SAMAgentType.CARD_SECURITY,
}


class SAMOrchestratorAgent:
    """
    Central Voice Orchestrator Agent that routes user requests to
    specialized domain agents over the Solace Agent Mesh.

    Responsibilities:
    1. Receive user intent + extracted entities from the LLM reasoning layer.
    2. Route the request to the correct domain agent (Account or Card Security).
    3. Collect the domain agent response and return it for TTS synthesis.
    """

    def __init__(self, solace_client: Optional[SolaceClient] = None):
        self.solace_client = solace_client
        self.agent_name = SAMAgentType.ORCHESTRATOR.value

        # Initialize specialized domain agents
        self.account_agent = SAMAccountOperationsAgent(solace_client=solace_client)
        self.card_security_agent = SAMCardSecurityAgent(solace_client=solace_client)

        self._agent_registry: Dict[SAMAgentType, Any] = {
            SAMAgentType.ACCOUNT_OPERATIONS: self.account_agent,
            SAMAgentType.CARD_SECURITY: self.card_security_agent,
        }

        logger.info("[SAM Orchestrator] Initialized with Account Operations & Card Security agents")

    async def route(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        session_id: str = "default_session",
        correlation_id: Optional[str] = None,
    ) -> SAMEvent:
        """
        Route a tool call from the LLM to the appropriate SAM domain agent.

        Args:
            tool_name: The function/tool name decided by the LLM (e.g. 'check_balance').
            tool_args: The arguments extracted by the LLM (e.g. {"account_number": "1122334455"}).
            session_id: Active session ID for context tracking.
            correlation_id: Optional correlation ID for Solace tracing.

        Returns:
            SAMEvent with the domain agent's response payload.
        """
        target_agent_type = TOOL_TO_AGENT_MAP.get(tool_name)

        if target_agent_type is None:
            logger.warning(f"[SAM Orchestrator] No agent registered for tool '{tool_name}'")
            return SAMEvent(
                header=SAMHeader(
                    session_id=session_id,
                    sender_agent=self.agent_name,
                    target_agent="unknown",
                ),
                topic="sam/v1/agent/orchestrator/response",
                intent=tool_name,
                payload={},
                error=f"No SAM agent registered for tool '{tool_name}'",
            )

        domain_agent = self._agent_registry[target_agent_type]

        # Build SAM A2A request event
        sam_request = SAMEvent(
            header=SAMHeader(
                session_id=session_id,
                correlation_id=correlation_id or "",
                sender_agent=self.agent_name,
                target_agent=target_agent_type.value,
            ),
            topic=domain_agent.topic_request,
            intent=tool_name,
            payload=tool_args,
        )

        logger.info(
            f"[SAM Orchestrator] Routing tool '{tool_name}' to {target_agent_type.value} "
            f"(topic: {domain_agent.topic_request})"
        )

        # Dispatch to domain agent and await response
        sam_response = await domain_agent.handle_event(sam_request)

        logger.info(
            f"[SAM Orchestrator] Received response from {sam_response.header.sender_agent} "
            f"(error: {sam_response.error})"
        )

        return sam_response
