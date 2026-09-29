import re
from datetime import datetime
from typing import Union


def format_rupiah_for_tts(amount: Union[int, float, str]) -> str:
    """Format angka rupiah menjadi teks untuk TTS (Bahasa Indonesia)"""
    if isinstance(amount, str):
        amount = int(amount.replace(".", "").replace(",", "").replace("Rp", "").strip())
    
    if amount < 0:
        return "minus " + format_rupiah_for_tts(abs(amount))
    
    if amount == 0:
        return "nol rupiah"
    
    units = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan"]
    teens = ["sepuluh", "sebelas", "dua belas", "tiga belas", "empat belas", "lima belas", "enam belas", "tujuh belas", "delapan belas", "sembilan belas"]
    tens = ["", "", "dua puluh", "tiga puluh", "empat puluh", "lima puluh", "enam puluh", "tujuh puluh", "delapan puluh", "sembilan puluh"]
    
    def convert_hundreds(n: int) -> str:
        if n == 0:
            return ""
        elif n < 10:
            return units[n]
        elif n < 20:
            return teens[n - 10]
        elif n < 100:
            return tens[n // 10] + (" " + units[n % 10] if n % 10 else "")
        else:
            return units[n // 100] + " ratus" + (" " + convert_hundreds(n % 100) if n % 100 else "")
    
    def convert_thousands(n: int) -> str:
        if n == 0:
            return ""
        elif n < 1000:
            return convert_hundreds(n)
        elif n < 1_000_000:
            return convert_thousands(n // 1000) + " ribu" + (" " + convert_thousands(n % 1000) if n % 1000 else "")
        elif n < 1_000_000_000:
            return convert_thousands(n // 1_000_000) + " juta" + (" " + convert_thousands(n % 1_000_000) if n % 1_000_000 else "")
        else:
            return convert_thousands(n // 1_000_000_000) + " miliar" + (" " + convert_thousands(n % 1_000_000_000) if n % 1_000_000_000 else "")
    
    result = convert_thousands(amount).strip()
    return f"{result} rupiah" if result else "nol rupiah"


def format_number_for_tts(number: str) -> str:
    """Format nomor (rekening/kartu/OTP) digit per digit untuk TTS"""
    digit_map = {
        "0": "nol", "1": "satu", "2": "dua", "3": "tiga", "4": "empat",
        "5": "lima", "6": "enam", "7": "tujuh", "8": "delapan", "9": "sembilan"
    }
    return " ".join(digit_map.get(d, d) for d in number if d.isdigit())


def format_date_for_tts(date_str: str) -> str:
    """Format tanggal ISO untuk TTS Bahasa Indonesia"""
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = date_str
        
        months = [
            "Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember"
        ]
        
        day = dt.day
        month = months[dt.month - 1]
        year = dt.year
        
        return f"{day} {month} {year}"
    except Exception:
        return date_str


def format_datetime_for_tts(date_str: str) -> str:
    """Format datetime ISO untuk TTS Bahasa Indonesia dengan jam"""
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = date_str
        
        months = [
            "Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember"
        ]
        
        day = dt.day
        month = months[dt.month - 1]
        year = dt.year
        hour = dt.hour
        minute = dt.minute
        
        time_str = f"pukul {hour}:{minute:02d}" if minute > 0 else f"pukul {hour}"
        return f"{day} {month} {year} {time_str}"
    except Exception:
        return date_str


def format_transaction_for_tts(transaction: dict) -> str:
    """Format single transaction untuk TTS"""
    date_str = format_date_for_tts(transaction.get("date", ""))
    txn_type = transaction.get("type", "")
    amount = format_rupiah_for_tts(transaction.get("amount", 0))
    description = transaction.get("description", "")
    balance_after = format_rupiah_for_tts(transaction.get("balance_after", 0))
    
    type_word = "keluar" if txn_type == "debit" else "masuk"
    
    return f"Tanggal {date_str}, {description}, {type_word} {amount}, sisa saldo {balance_after}"


def clean_text_for_tts(text: str) -> str:
    """Bersihkan teks untuk TTS - hapus karakter khusus, normalisasi"""
    text = re.sub(r"[^\w\s\.,\-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()