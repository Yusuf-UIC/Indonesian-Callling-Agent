import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from app.schemas.banking import (
    BalanceEnquiryRequest,
    BalanceEnquiryResponse,
    TransactionListRequest,
    TransactionListResponse,
    TransactionItem,
    TransactionType,
    CardBlockRequest,
    CardBlockResponse,
    AccountType,
    ErrorResponse,
)


class MockBankingService:
    def __init__(self):
        self.accounts: Dict[str, Dict] = {
            "1234567890": {
                "account_type": AccountType.SAVINGS,
                "balance": 5_250_000,
                "name": "Budi Santoso",
            },
            "9876543210": {
                "account_type": AccountType.CHECKING,
                "balance": 12_750_000,
                "name": "Siti Rahayu",
            },
            "1122334455": {
                "account_type": AccountType.SAVINGS,
                "balance": 850_000,
                "name": "Ahmad Wijaya",
            },
        }
        self.cards: Dict[str, Dict] = {
            "4567890123456789": {"account_number": "1234567890", "blocked": False, "name": "Budi Santoso"},
            "4567890123456790": {"account_number": "9876543210", "blocked": False, "name": "Siti Rahayu"},
            "4567890123456791": {"account_number": "1122334455", "blocked": False, "name": "Ahmad Wijaya"},
        }
        self.transactions: Dict[str, List[TransactionItem]] = {}
        self._generate_mock_transactions()

    def _generate_mock_transactions(self):
        merchants = ["Alfamart", "Indomaret", "Gojek", "Grab", "Tokopedia", "Shopee", "PLN", "PDAM", "Telkomsel", "XL Axiata"]
        for acc_num in self.accounts:
            txns = []
            base_balance = self.accounts[acc_num]["balance"]
            for i in range(15):
                txn_type = random.choice([TransactionType.DEBIT, TransactionType.CREDIT])
                amount = random.randint(50_000, 2_000_000)
                if txn_type == TransactionType.DEBIT:
                    base_balance = max(50_000, base_balance - amount)
                else:
                    base_balance += amount
                txns.append(TransactionItem(
                    transaction_id=f"TXN{acc_num[-4:]}{i:04d}",
                    date=datetime.now(timezone.utc) - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23)),
                    type=txn_type,
                    amount=amount,
                    description=f"{random.choice(merchants)} - {'Pembayaran' if txn_type == TransactionType.DEBIT else 'Penerimaan'}",
                    balance_after=base_balance,
                ))
            self.transactions[acc_num] = sorted(txns, key=lambda x: x.date, reverse=True)

    def get_balance(self, request: BalanceEnquiryRequest) -> BalanceEnquiryResponse:
        account = self.accounts.get(request.account_number)
        if not account:
            raise ValueError(f"Rekening {request.account_number} tidak ditemukan")
        
        return BalanceEnquiryResponse(
            account_number=request.account_number,
            account_type=account["account_type"],
            balance=account["balance"],
            currency="IDR",
            last_updated=datetime.now(timezone.utc),
            status="success",
        )

    def get_transactions(self, request: TransactionListRequest) -> TransactionListResponse:
        account = self.accounts.get(request.account_number)
        if not account:
            raise ValueError(f"Rekening {request.account_number} tidak ditemukan")
        
        txns = self.transactions.get(request.account_number, [])
        
        if request.start_date:
            txns = [t for t in txns if t.date >= request.start_date]
        if request.end_date:
            txns = [t for t in txns if t.date <= request.end_date]
        
        txns = txns[:request.limit]
        
        return TransactionListResponse(
            account_number=request.account_number,
            transactions=txns,
            total_count=len(self.transactions.get(request.account_number, [])),
            status="success",
        )

    def block_card(self, request: CardBlockRequest) -> CardBlockResponse:
        card = self.cards.get(request.card_number)
        if not card:
            raise ValueError(f"Kartu {request.card_number} tidak ditemukan")
        
        if card["blocked"]:
            raise ValueError(f"Kartu {request.card_number} sudah diblokir sebelumnya")
        
        if request.identity_otp != "123456":
            raise ValueError("Kode OTP tidak valid.")
        
        card["blocked"] = True
        block_ref = f"BLK{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"
        
        return CardBlockResponse(
            card_number=request.card_number,
            blocked=True,
            block_reference=block_ref,
            blocked_at=datetime.now(timezone.utc),
            status="success",
            message=f"Kartu {request.card_number[-4:]} berhasil diblokir. Nomor referensi: {block_ref}",
        )


mock_banking_service = MockBankingService()