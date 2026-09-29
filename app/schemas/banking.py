from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


class AccountType(str, Enum):
    SAVINGS = "savings"
    CHECKING = "checking"
    CREDIT = "credit"


class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class BalanceEnquiryRequest(BaseModel):
    account_number: str = Field(..., min_length=10, max_length=20, description="Nomor rekening nasabah")
    account_type: AccountType = Field(default=AccountType.SAVINGS, description="Tipe rekening")

    @model_validator(mode="before")
    @classmethod
    def strip_account_number(cls, data: dict) -> dict:
        if "account_number" in data and isinstance(data["account_number"], str):
            data["account_number"] = data["account_number"].strip()
        return data


class BalanceEnquiryResponse(BaseModel):
    account_number: str
    account_type: AccountType
    balance: int = Field(..., description="Saldo dalam satuan Rupiah (tanpa desimal)")
    currency: str = Field(default="IDR", description="Mata uang")
    last_updated: datetime = Field(default_factory=datetime.now, description="Waktu terakhir update saldo")
    status: str = Field(default="success", description="Status response")


class TransactionItem(BaseModel):
    transaction_id: str
    date: datetime
    type: TransactionType
    amount: int = Field(..., description="Jumlah transaksi dalam Rupiah")
    description: str
    balance_after: int = Field(..., description="Saldo setelah transaksi")


class TransactionListRequest(BaseModel):
    account_number: str = Field(..., min_length=10, max_length=20)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="before")
    @classmethod
    def strip_account_number(cls, data: dict) -> dict:
        if "account_number" in data and isinstance(data["account_number"], str):
            data["account_number"] = data["account_number"].strip()
        return data


class TransactionListResponse(BaseModel):
    account_number: str
    transactions: List[TransactionItem]
    total_count: int
    status: str = Field(default="success")


class CardBlockRequest(BaseModel):
    card_number: str = Field(..., min_length=16, max_length=16, description="Nomor kartu 16 digit")
    identity_otp: str = Field(..., min_length=6, max_length=6, description="OTP verifikasi 6 digit")
    reason: str = Field(default="lost_stolen", description="Alasan pemblokiran: lost_stolen, fraud, damaged, other")

    @model_validator(mode="before")
    @classmethod
    def strip_strings(cls, data: dict) -> dict:
        for key in ["card_number", "identity_otp", "reason"]:
            if key in data and isinstance(data[key], str):
                data[key] = data[key].strip()
        return data


class CardBlockResponse(BaseModel):
    card_number: str
    blocked: bool
    block_reference: str = Field(..., description="Nomor referensi pemblokiran")
    blocked_at: datetime = Field(default_factory=datetime.now)
    status: str = Field(default="success")
    message: str


class ErrorResponse(BaseModel):
    status: str = Field(default="error")
    error_code: str
    message: str
    detail: Optional[str] = None