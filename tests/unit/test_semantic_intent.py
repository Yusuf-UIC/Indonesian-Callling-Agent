import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.semantic_intent import SemanticIntentClassifier, IntentType


def test_semantic_intent_classifier():
    print("=" * 60)
    print("Testing Semantic Intent Classifier")
    print("=" * 60)

    classifier = SemanticIntentClassifier()
    initialized = classifier.initialize()

    print(f"\nModel initialized: {initialized}")
    print(f"Model name: {classifier.model_name}")
    print(f"Similarity threshold: {classifier.similarity_threshold}")

    test_cases = [
        ("cek saldo rekening 1234567890", IntentType.CHECK_BALANCE),
        ("saldo saya tinggal berapa?", IntentType.CHECK_BALANCE),
        ("duitku di rekening ada berapa", IntentType.CHECK_BALANCE),
        ("sisa uang di tabungan berapa", IntentType.CHECK_BALANCE),
        ("blokir kartu 4567890123456789", IntentType.BLOCK_CARD),
        ("kartuku hilang, tolong diblokir", IntentType.BLOCK_CARD),
        ("kartu atm tertelan mesin", IntentType.BLOCK_CARD),
        ("nonaktifkan kartu kredit saya", IntentType.BLOCK_CARD),
        ("riwayat transaksi rekening 9876543210", IntentType.TRANSACTION_HISTORY),
        ("mutasi rekening bulan ini", IntentType.TRANSACTION_HISTORY),
        ("terakhir transfer kapan ya", IntentType.TRANSACTION_HISTORY),
        ("pengeluaran kemarin berapa", IntentType.TRANSACTION_HISTORY),
        ("halo, apa kabar?", IntentType.GREETING),
        ("selamat pagi", IntentType.GREETING),
        ("assalamualaikum", IntentType.GREETING),
        ("bantuan dong", IntentType.HELP),
        ("apa yang bisa kamu lakukan", IntentType.HELP),
        ("menu fitur apa saja", IntentType.HELP),
        ("ini teks acak yang tidak jelas", IntentType.UNKNOWN),
        ("blokir kartu 4567890123456789 OTP 123456", IntentType.BLOCK_CARD),
        ("cek saldo 1234567890", IntentType.CHECK_BALANCE),
    ]

    print("\n" + "-" * 60)
    print("Test Results:")
    print("-" * 60)

    passed = 0
    failed = 0

    for text, expected_intent in test_cases:
        result = classifier.classify(text)
        status = "[PASS]" if result.intent == expected_intent else "[FAIL]"
        if result.intent == expected_intent:
            passed += 1
        else:
            failed += 1

        entities_str = ", ".join(f"{k}={v}" for k, v in result.entities.items()) if result.entities else "none"
        print(f"{status} | Intent: {result.intent.value:20s} | Conf: {result.confidence:.3f} | Entities: [{entities_str}] | Input: '{text}'")

    print("-" * 60)
    print(f"Passed: {passed}, Failed: {failed}, Total: {len(test_cases)}")
    print("=" * 60)

    assert failed == 0


if __name__ == "__main__":
    success = test_semantic_intent_classifier()
    exit(0 if success else 1)