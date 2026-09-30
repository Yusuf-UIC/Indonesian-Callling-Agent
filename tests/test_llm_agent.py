import pytest
import os
import asyncio
from unittest.mock import AsyncMock, patch

from app.services.llm_agent import LLMAgentSolace, BANKING_TOOLS, LLMResponse
from app.services.agent_solace import BankingAgentSolace
from app.services.solace_client import SolaceEvent, SolaceEventType


def test_banking_tools_schema():
    """Verify tool definitions have required schemas for OpenAI / Gemini function calling."""
    tool_names = [t["function"]["name"] for t in BANKING_TOOLS]
    assert "check_balance" in tool_names
    assert "get_transactions" in tool_names
    assert "block_card" in tool_names

    check_bal = next(t for t in BANKING_TOOLS if t["function"]["name"] == "check_balance")
    assert "account_number" in check_bal["function"]["parameters"]["required"]

    block = next(t for t in BANKING_TOOLS if t["function"]["name"] == "block_card")
    assert "card_number" in block["function"]["parameters"]["required"]
    assert "identity_otp" in block["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_llm_agent_fallback_without_api_key():
    """Verify LLM agent responds gracefully when no API key is provided."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
        agent = LLMAgentSolace(provider="gemini", api_key=None)
        response = await agent.process("Halo")
        assert "API key" in response.text or "dikondisikan" in response.text or "diperbarui" in response.text or "belum" in response.text


@pytest.mark.asyncio
async def test_llm_agent_tool_execution():
    """Verify tool execution calls Solace request_reply."""
    mock_solace = AsyncMock()
    mock_solace.request_reply.return_value = SolaceEvent(
        event_type=SolaceEventType.BALANCE_RESPONSE,
        correlation_id="test-corr",
        payload={"account_number": "1122334455", "balance": 850000, "status": "success"}
    )

    agent = LLMAgentSolace(solace_client=mock_solace, provider="gemini", api_key="dummy_key", use_sam=False)
    result = await agent._execute_tool("check_balance", {"account_number": "1122334455"})

    assert result["balance"] == 850000
    mock_solace.request_reply.assert_called_once()
