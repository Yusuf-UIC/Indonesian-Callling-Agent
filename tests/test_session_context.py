import pytest
import asyncio
import time
from app.services.agent_solace import BankingAgentSolace
from app.services.session_manager import session_manager, SessionState
from app.services.semantic_intent import IntentType

@pytest.mark.asyncio
async def test_multiturn_card_blocking_flow():
    from app.services.mock_bank import mock_banking_service
    mock_banking_service.cards["4567890123456789"]["blocked"] = False
    
    agent = BankingAgentSolace(use_mock=True, use_mqtt=False, default_session_id="test_card_block_session")
    await agent.initialize()
    
    session_id = "test_card_block_session"
    
    # Turn 1: User asks to block card (no card number, no OTP)
    res1 = await agent.process("Kartu saya hilang, tolong diblokir", session_id=session_id)
    assert res1.intent == IntentType.BLOCK_CARD
    assert "nomor kartu 16 digit" in res1.text
    
    session = session_manager.get_session(session_id)
    assert session.active_intent == IntentType.BLOCK_CARD
    assert "card_number" not in session.entities
    
    # Turn 2: User provides 16-digit card number only
    res2 = await agent.process("4567890123456789", session_id=session_id)
    assert res2.intent == IntentType.BLOCK_CARD
    assert "kode OTP 6 digit" in res2.text
    assert session.entities.get("card_number") == "4567890123456789"
    assert "otp" not in session.entities
    
    # Turn 3: User provides 6-digit OTP
    res3 = await agent.process("123456", session_id=session_id)
    assert res3.intent == IntentType.BLOCK_CARD
    assert "berhasil diblokir" in res3.text
    
    # Verify session active_intent was reset after completion
    await agent.close()


@pytest.mark.asyncio
async def test_multiturn_card_blocking_wrong_otp_retry():
    from app.services.mock_bank import mock_banking_service
    mock_banking_service.cards["4567890123456790"]["blocked"] = False
    
    agent = BankingAgentSolace(use_mock=True, use_mqtt=False, default_session_id="test_wrong_otp_session")
    await agent.initialize()
    
    session_id = "test_wrong_otp_session"
    
    # Turn 1: User asks to block card
    await agent.process("Kartu saya hilang, tolong diblokir", session_id=session_id)
    
    # Turn 2: User provides 16-digit card number
    await agent.process("4567890123456790", session_id=session_id)
    
    # Turn 3: User provides WRONG OTP (000000)
    res3 = await agent.process("000000", session_id=session_id)
    assert "tidak valid" in res3.text.lower()
    
    # Session state should RETAIN active_intent = BLOCK_CARD and card_number = 4567890123456790
    session = session_manager.get_session(session_id)
    assert session.active_intent == IntentType.BLOCK_CARD
    assert session.entities.get("card_number") == "4567890123456790"
    assert "otp" not in session.entities
    
    # Turn 4: User provides CORRECT OTP (123456)
    res4 = await agent.process("123456", session_id=session_id)
    assert res4.intent == IntentType.BLOCK_CARD
    assert "berhasil diblokir" in res4.text
    
    # Session should now be reset
    assert session.active_intent is None
    assert len(session.entities) == 0
    
    await agent.close()


@pytest.mark.asyncio
async def test_multiturn_balance_check_flow():
    agent = BankingAgentSolace(use_mock=True, use_mqtt=False, default_session_id="test_balance_session")
    await agent.initialize()
    
    session_id = "test_balance_session"
    
    # Turn 1: User asks for balance without account number
    res1 = await agent.process("Cek saldo saya dong", session_id=session_id)
    assert res1.intent == IntentType.CHECK_BALANCE
    assert "nomor rekening" in res1.text
    
    # Turn 2: User provides account number
    res2 = await agent.process("1234567890", session_id=session_id)
    assert res2.intent == IntentType.CHECK_BALANCE
    assert "lima juta" in res2.text.lower()
    
    await agent.close()


def test_session_ttl_expiry():
    sm = session_manager
    session_id = "ttl_test_session"
    
    session = sm.get_session(session_id)
    session.active_intent = IntentType.BLOCK_CARD
    session.entities = {"card_number": "4567890123456789"}
    session.last_updated = time.time() - 3601  # 1 hour 1 sec ago
    
    # Request session after TTL expiry
    expired_session = sm.get_session(session_id)
    assert expired_session.active_intent is None
    assert len(expired_session.entities) == 0
