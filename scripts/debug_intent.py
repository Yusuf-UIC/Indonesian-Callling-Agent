import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.semantic_intent import SemanticIntentClassifier, IntentType
import numpy as np


def debug_similarity():
    print("=" * 60)
    print("Debugging Semantic Intent Classifier - Similarity Scores")
    print("=" * 60)

    classifier = SemanticIntentClassifier(similarity_threshold=0.3)  # Lower threshold for debugging
    initialized = classifier.initialize()

    print(f"\nModel initialized: {initialized}")

    test_cases = [
        "cek saldo rekening 1234567890",
        "saldo saya tinggal berapa?",
        "duitku di rekening ada berapa",
        "sisa uang di tabungan berapa",
        "blokir kartu 4567890123456789",
        "kartuku hilang, tolong diblokir",
        "kartu atm tertelan mesin",
        "nonaktifkan kartu kredit saya",
        "riwayat transaksi rekening 9876543210",
        "mutasi rekening bulan ini",
        "terakhir transfer kapan ya",
        "pengeluaran kemarin berapa",
        "halo, apa kabar?",
        "selamat pagi",
        "assalamualaikum",
        "bantuan dong",
        "apa yang bisa kamu lakukan",
        "menu fitur apa saja",
        "ini teks acak yang tidak jelas",
        "blokir kartu 4567890123456789 OTP 123456",
        "cek saldo 1234567890",
    ]

    print("\n" + "-" * 80)
    print(f"{'Input':<45} {'Best Intent':<20} {'Score':<8} {'Threshold':<8}")
    print("-" * 80)

    for text in test_cases:
        text_lower = text.lower().strip()
        query_embedding = classifier.model.encode([text_lower], convert_to_numpy=True, normalize_embeddings=True)[0]

        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        all_scores = {}

        for intent, anchor_embedding in classifier.anchor_embeddings.items():
            similarity = float(np.dot(query_embedding, anchor_embedding))
            all_scores[intent.value] = similarity
            if similarity > best_score:
                best_score = similarity
                best_intent = intent

        # Show all scores
        scores_str = ", ".join(f"{k}:{v:.3f}" for k, v in sorted(all_scores.items(), key=lambda x: -x[1]))
        print(f"{text[:43]:<45} {best_intent.value:<20} {best_score:.3f}    {classifier.similarity_threshold}")

    print("-" * 80)


if __name__ == "__main__":
    debug_similarity()