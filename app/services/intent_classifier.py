from typing import List, Dict, Optional
from enum import Enum
from dataclasses import dataclass


class IntentType(str, Enum):
    CHECK_BALANCE = "check_balance"
    BLOCK_CARD = "block_card"
    TRANSACTION_HISTORY = "transaction_history"
    UNKNOWN = "unknown"
    GREETING = "greeting"
    HELP = "help"


@dataclass
class IntentResult:
    intent: IntentType
    confidence: float
    entities: Dict[str, str]


class IndonesianIntentClassifier:
    def __init__(self):
        self.intent_keywords = {
            IntentType.CHECK_BALANCE: [
                "cek saldo", "ceksaldo", "saldo", "jumlah uang", "uang saya",
                "berapa saldo", "saldo berapa", "sisa saldo", "lihat saldo",
                "balance", "checking balance", "saldo rekening"
            ],
            IntentType.BLOCK_CARD: [
                "blokir kartu", "blokir kartu atm", "blokir kartu debit",
                "blokir kartu kredit", "kartu hilang", "kartu dicuri",
                "kartu lost", "kartu stolen", "matikan kartu", "nonaktifkan kartu",
                "block card", "blokir", "kartu saya hilang"
            ],
            IntentType.TRANSACTION_HISTORY: [
                "riwayat transaksi", "history transaksi", "transaksi saya",
                "daftar transaksi", "mutasi rekening", "mutasi", "riwayat",
                "transaksi terakhir", "transaksi baru", "lihat transaksi"
            ],
            IntentType.GREETING: [
                "halo", "hai", "hello", "selamat pagi", "selamat siang",
                "selamat sore", "selamat malam", "apa kabar", "assalamualaikum",
                "permisi", "excuse me"
            ],
            IntentType.HELP: [
                "bantuan", "help", "tolong", "apa yang bisa kamu lakukan",
                "fitur apa saja", "menu", "cara pakai", "panduan"
            ],
        }

    def classify(self, text: str) -> IntentResult:
        text_lower = text.lower().strip()
        
        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        entities = {}
        
        for intent, keywords in self.intent_keywords.items():
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 1
                    if "rekening" in text_lower or "account" in text_lower:
                        entities["account_mentioned"] = "true"
                    if "kartu" in text_lower or "card" in text_lower:
                        entities["card_mentioned"] = "true"
            
            if score > best_score:
                best_score = score
                best_intent = intent
        
        confidence = min(best_score * 0.3, 1.0) if best_score > 0 else 0.0
        
        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            entities=entities
        )

    def extract_account_number(self, text: str) -> Optional[str]:
        import re
        patterns = [
            r"\b\d{10}\b",
            r"\b\d{12}\b",
            r"\b\d{16}\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group()
        return None

    def extract_card_number(self, text: str) -> Optional[str]:
        import re
        match = re.search(r"\b\d{16}\b", text)
        if match:
            return match.group()
        return None

    def extract_otp(self, text: str) -> Optional[str]:
        import re
        match = re.search(r"\b\d{6}\b", text)
        if match:
            return match.group()
        return None


intent_classifier = IndonesianIntentClassifier()