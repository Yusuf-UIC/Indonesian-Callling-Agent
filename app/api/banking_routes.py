from fastapi import APIRouter, HTTPException, status
from app.schemas.banking import (
    BalanceEnquiryRequest,
    BalanceEnquiryResponse,
    TransactionListRequest,
    TransactionListResponse,
    CardBlockRequest,
    CardBlockResponse,
    ErrorResponse,
)
from app.services.mock_bank import mock_banking_service

router = APIRouter(prefix="/api/v1/banking", tags=["Banking"])


@router.post(
    "/balance",
    response_model=BalanceEnquiryResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Cek Saldo Rekening",
    description="Mengembalikan saldo rekening nasabah dalam Rupiah",
)
async def check_balance(request: BalanceEnquiryRequest):
    try:
        return mock_banking_service.get_balance(request)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/transactions",
    response_model=TransactionListResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Daftar Transaksi",
    description="Mengembalikan daftar transaksi rekening nasabah",
)
async def list_transactions(request: TransactionListRequest):
    try:
        return mock_banking_service.get_transactions(request)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/block-card",
    response_model=CardBlockResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Blokir Kartu",
    description="Memblokir kartu ATM/Debit/Kredit nasabah",
)
async def block_card(request: CardBlockRequest):
    try:
        return mock_banking_service.block_card(request)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))