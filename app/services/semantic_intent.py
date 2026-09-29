import re
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import logging

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    CHECK_BALANCE = "check_balance"
    BLOCK_CARD = "block_card"
    TRANSACTION_HISTORY = "transaction_history"
    GREETING = "greeting"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    intent: IntentType
    confidence: float
    entities: Dict[str, str]


ANCHOR_DATASET: Dict[IntentType, List[str]] = {
    IntentType.CHECK_BALANCE: [
        "cek saldo",
        "saldo saya tinggal berapa",
        "duitku di rekening ada berapa",
        "sisa uang di tabungan",
        "berapa saldo rekening",
        "lihat saldo",
        "saldo berapa sekarang",
        "cek saldo rekening",
        "jumlah uang di rekening",
        "sisa saldo berapa",
        "saldo berapa",
        "saldo saya",
        "uang saya berapa",
    ],
    IntentType.BLOCK_CARD: [
        "blokir kartu",
        "kartuku hilang",
        "kartu atm tertelan",
        "nonaktifkan kartu kredit",
        "blokir kartu debit",
        "kartu dicuri",
        "matikan kartu",
        "kartu lost",
        "kartu stolen",
        "blokir kartu saya",
        "blokir kartu atm",
        "kartu saya hilang",
    ],
    IntentType.TRANSACTION_HISTORY: [
        "riwayat transaksi",
        "mutasi rekening",
        "terakhir transfer kapan",
        "pengeluaran kemarin",
        "daftar transaksi",
        "lihat transaksi",
        "histori transaksi",
        "transaksi terakhir",
        "mutasi saldo",
        "riwayat pengeluaran",
        "mutasi bulan ini",
        "transaksi kemarin",
        "cek transaksi",
        "cek 5 transaksi terakhir",
        "tampilkan transaksi terakhir",
        "tampilkan transaksi terakhir rekening",
        "cek mutasi rekening",
        "lihat transaksi terakhir",
    ],
    IntentType.GREETING: [
        "halo",
        "hai",
        "selamat pagi",
        "selamat siang",
        "selamat sore",
        "selamat malam",
        "apa kabar",
        "assalamualaikum",
        "permisi",
        "hello",
        "halo apa kabar",
        "selamat malam pak",
    ],
    IntentType.HELP: [
        "bantuan",
        "help",
        "tolong",
        "apa yang bisa kamu lakukan",
        "fitur apa saja",
        "menu",
        "cara pakai",
        "panduan",
        "bantuan dong",
        "gimana caranya",
        "apa fiturnya",
    ],
}


INDONESIAN_MONTHS = {
    "januari": 1, "jan": 1,
    "februari": 2, "feb": 2,
    "maret": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mei": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "agustus": 8, "agt": 8, "agu": 8,
    "september": 9, "sep": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "desember": 12, "des": 12,
}


class SemanticIntentClassifier:
    def __init__(
        self,
        model_name: str = "LazarusNLP/all-indo-e5-small-v4",
        similarity_threshold: float = 0.40,
        use_gpu: bool = False,
    ):
        self.model_name = model_name
        self.similarity_threshold = similarity_threshold
        self.use_gpu = use_gpu
        self.model: Optional[SentenceTransformer] = None
        self.anchor_embeddings: Dict[IntentType, np.ndarray] = {}
        self._initialized = False

    def initialize(self) -> bool:
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("sentence-transformers not available, falling back to keyword matching")
            return False

        try:
            device = "cuda" if self.use_gpu else "cpu"
            print(f"Loading model '{self.model_name}' on {device} (first run downloads weights from HuggingFace Hub)...")
            self.model = SentenceTransformer(self.model_name, device=device)
            print("Model loaded successfully. Computing anchor embeddings...")
            self._compute_anchor_embeddings()
            self._initialized = True
            logger.info(f"Semantic intent classifier initialized with {self.model_name} on {device}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize semantic intent classifier: {e}")
            return False

    def _compute_anchor_embeddings(self):
        for intent, phrases in ANCHOR_DATASET.items():
            embeddings = self.model.encode(phrases, convert_to_numpy=True, normalize_embeddings=True)
            self.anchor_embeddings[intent] = embeddings

    def classify(self, text: str) -> IntentResult:
        text_lower = text.lower().strip()
        entities = self._extract_entities(text)

        # Pure numbers / digits (e.g. card number, account number, OTP) are not standalone domain intents
        if re.fullmatch(r"[\d\s\-\.,]+", text_lower):
            return IntentResult(intent=IntentType.UNKNOWN, confidence=0.0, entities=entities)

        # Keyword heuristics for high-precision domain matching
        if any(kw in text_lower for kw in ["transaksi", "mutasi", "riwayat", "histori"]):
            return IntentResult(intent=IntentType.TRANSACTION_HISTORY, confidence=0.98, entities=entities)
        if any(kw in text_lower for kw in ["blokir", "kartu hilang", "kartu dicuri", "nonaktifkan kartu"]):
            return IntentResult(intent=IntentType.BLOCK_CARD, confidence=0.98, entities=entities)
        if any(kw in text_lower for kw in ["saldo", "sisa uang", "duitku", "jumlah uang"]):
            return IntentResult(intent=IntentType.CHECK_BALANCE, confidence=0.98, entities=entities)

        if not self._initialized or self.model is None:
            return self._fallback_classify(text)

        query_embedding = self.model.encode([text_lower], convert_to_numpy=True, normalize_embeddings=True)[0]

        best_intent = IntentType.UNKNOWN
        best_score = 0.0

        for intent, anchor_embeddings in self.anchor_embeddings.items():
            similarities = np.dot(anchor_embeddings, query_embedding)
            max_similarity = float(np.max(similarities))
            if max_similarity > best_score:
                best_score = max_similarity
                best_intent = intent

        if best_score < self.similarity_threshold:
            best_intent = IntentType.UNKNOWN
            best_score = 0.0

        return IntentResult(
            intent=best_intent,
            confidence=best_score,
            entities=entities,
        )

    def _extract_entities(self, text: str) -> Dict[str, Any]:
        from datetime import datetime, timedelta, timezone
        entities = {}
        text_lower = text.lower().strip()

        account_match = re.search(r"\b\d{10,16}\b", text)
        if account_match:
            entities["account_number"] = account_match.group()

        card_match = re.search(r"\b\d{16}\b", text)
        if card_match:
            entities["card_number"] = card_match.group()

        otp_match = re.search(r"\b\d{6}\b", text)
        if otp_match:
            entities["otp"] = otp_match.group()

        # Extract transaction limit (e.g. "5 transaksi" or "3 transaksi")
        limit_match = re.search(r"\b(\d+)\s+transaksi\b", text_lower)
        if limit_match:
            entities["limit"] = int(limit_match.group(1))

        # Extract date range filters for transaction history
        now = datetime.now(timezone.utc)

        days_match = re.search(r"\b(\d+)\s*hari\b", text_lower)
        if days_match:
            num_days = int(days_match.group(1))
            start_dt = (now - timedelta(days=num_days)).replace(hour=0, minute=0, second=0, microsecond=0)
            entities["start_date"] = start_dt.isoformat()
            if "limit" not in entities:
                entities["limit"] = 20
        elif "kemarin" in text_lower:
            yesterday = now - timedelta(days=1)
            start_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
            entities["start_date"] = start_dt.isoformat()
            entities["end_date"] = end_dt.isoformat()
            if "limit" not in entities:
                entities["limit"] = 20
        elif "seminggu" in text_lower or "minggu ini" in text_lower:
            start_dt = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            entities["start_date"] = start_dt.isoformat()
            if "limit" not in entities:
                entities["limit"] = 20
        else:
            # Check specific date like "27 september 2026" or "tanggal 27 september"
            date_match = re.search(r"(?:tanggal\s+)?(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?", text_lower)
            if date_match:
                day = int(date_match.group(1))
                month_str = date_match.group(2)
                year_str = date_match.group(3)

                if month_str in INDONESIAN_MONTHS:
                    month = INDONESIAN_MONTHS[month_str]
                    year = int(year_str) if year_str else now.year
                    try:
                        target_dt = datetime(year, month, day, tzinfo=timezone.utc)
                        start_dt = target_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                        end_dt = target_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                        entities["start_date"] = start_dt.isoformat()
                        entities["end_date"] = end_dt.isoformat()
                        if "limit" not in entities:
                            entities["limit"] = 20
                    except ValueError:
                        pass

        return entities

    def _fallback_classify(self, text: str) -> IntentResult:
        text_lower = text.lower().strip()
        entities = self._extract_entities(text)

        keyword_map = {
            IntentType.CHECK_BALANCE: ["cek saldo", "saldo", "duitku", "sisa uang", "jumlah uang", "lihat saldo"],
            IntentType.BLOCK_CARD: ["blokir kartu", "kartu hilang", "kartu dicuri", "nonaktifkan kartu", "matikan kartu"],
            IntentType.TRANSACTION_HISTORY: ["riwayat transaksi", "mutasi", "transaksi terakhir", "daftar transaksi", "pengeluaran"],
            IntentType.GREETING: ["halo", "hai", "selamat pagi", "selamat siang", "selamat sore", "selamat malam", "apa kabar", "assalamualaikum"],
            IntentType.HELP: ["bantuan", "help", "tolong", "fitur apa", "menu", "cara pakai", "panduan"],
        }

        best_intent = IntentType.UNKNOWN
        best_score = 0

        for intent, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                best_intent = intent

        confidence = min(best_score * 0.3, 1.0) if best_score > 0 else 0.0

        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            entities=entities,
        )


semantic_intent_classifier = SemanticIntentClassifier()