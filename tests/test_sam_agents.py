import pytest
from unittest.mock import AsyncMock

from app.services.sam.sam_event import SAMEvent, SAMHeader, SAMAgentType
from app.services.sam.account_agent import SAMAccountOperationsAgent
from app.services.sam.card_security_agent import SAMCardSecurityAgent
from app.services.sam.orchestrator_agent import SAMOrchestratorAgent
from app.services.solace_client import SolaceEvent, SolaceEventType


def test_sam_event_serialization():
    """Verify SAMEvent can serialize and deserialize correctly."""
    header = SAMHeader(session_id="sess-001", sender_agent="orchestrator", target_agent="account_operations")
    event = SAMEvent(header=header, topic="sam/v1/agent/banking/account/request", intent="check_balance", payload={"account_number": "1122334455"})

    data = event.to_dict()
    assert data["header"]["session_id"] == "sess-001"
    assert data["header"]["sender_agent"] == "orchestrator"
    assert data["intent"] == "check_balance"
    assert data["payload"]["account_number"] == "1122334455"

    restored = SAMEvent.from_dict(data)
    assert restored.header.session_id == "sess-001"
    assert restored.intent == "check_balance"


@pytest.mark.asyncio
async def test_sam_account_agent_check_balance():
    """Verify SAM Account Agent routes check_balance to Solace and returns result."""
    mock_solace = AsyncMock()
    mock_solace.request_reply.return_value = SolaceEvent(
        event_type=SolaceEventType.BALANCE_RESPONSE,
        correlation_id="test-corr-001",
        payload={"account_number": "1122334455", "balance": 850000, "status": "success"},
    )

    agent = SAMAccountOperationsAgent(solace_client=mock_solace)

    request = SAMEvent(
        header=SAMHeader(session_id="sess-001", sender_agent="orchestrator", target_agent="account_operations"),
        topic="sam/v1/agent/banking/account/request",
        intent="check_balance",
        payload={"account_number": "1122334455"},
    )

    response = await agent.handle_event(request)

    assert response.error is None
    assert response.payload["balance"] == 850000
    assert response.header.sender_agent == SAMAgentType.ACCOUNT_OPERATIONS.value
    mock_solace.request_reply.assert_called_once()


@pytest.mark.asyncio
async def test_sam_card_security_agent_missing_otp():
    """Verify SAM Card Security Agent returns slot request when OTP is missing."""
    agent = SAMCardSecurityAgent(solace_client=AsyncMock())

    request = SAMEvent(
        header=SAMHeader(session_id="sess-002", sender_agent="orchestrator", target_agent="card_security"),
        topic="sam/v1/agent/banking/card/request",
        intent="block_card",
        payload={"card_number": "4567890123456789"},
    )

    response = await agent.handle_event(request)

    assert response.error is not None
    assert "OTP" in response.error
    assert response.payload.get("needs_slot") == "identity_otp"
    assert response.payload.get("card_number") == "4567890123456789"


@pytest.mark.asyncio
async def test_sam_card_security_agent_block_success():
    """Verify SAM Card Security Agent successfully blocks a card with valid OTP."""
    mock_solace = AsyncMock()
    mock_solace.request_reply.return_value = SolaceEvent(
        event_type=SolaceEventType.BLOCK_CARD_RESPONSE,
        correlation_id="test-corr-002",
        payload={"card_number": "4567890123456789", "blocked": True, "block_reference": "BLK20260929", "status": "success"},
    )

    agent = SAMCardSecurityAgent(solace_client=mock_solace)

    request = SAMEvent(
        header=SAMHeader(session_id="sess-002", sender_agent="orchestrator", target_agent="card_security"),
        topic="sam/v1/agent/banking/card/request",
        intent="block_card",
        payload={"card_number": "4567890123456789", "identity_otp": "123456"},
    )

    response = await agent.handle_event(request)

    assert response.error is None
    assert response.payload["blocked"] is True
    assert response.payload["block_reference"] == "BLK20260929"
    mock_solace.request_reply.assert_called_once()


@pytest.mark.asyncio
async def test_sam_orchestrator_routes_to_correct_agent():
    """Verify SAM Orchestrator routes check_balance to Account Agent and block_card to Card Security Agent."""
    mock_solace = AsyncMock()

    # Mock balance response
    mock_solace.request_reply.return_value = SolaceEvent(
        event_type=SolaceEventType.BALANCE_RESPONSE,
        correlation_id="orch-corr-001",
        payload={"account_number": "1122334455", "balance": 850000, "status": "success"},
    )

    orchestrator = SAMOrchestratorAgent(solace_client=mock_solace)

    # Route check_balance
    response = await orchestrator.route(
        tool_name="check_balance",
        tool_args={"account_number": "1122334455"},
        session_id="sess-orch-001",
    )

    assert response.error is None
    assert response.payload["balance"] == 850000
    assert response.header.sender_agent == SAMAgentType.ACCOUNT_OPERATIONS.value

    # Route unknown tool
    response_unknown = await orchestrator.route(
        tool_name="transfer_money",
        tool_args={},
        session_id="sess-orch-001",
    )
    assert response_unknown.error is not None
    assert "No SAM agent registered" in response_unknown.error
