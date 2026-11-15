def _normalize(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.replace(" ", "").replace("-", "")
    if not cleaned or not cleaned.isascii() or not cleaned.isdigit():
        return None
    return cleaned

def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
        if d > 9:
            d -= 9
        total += d
    return total % 10 == 0

def is_my_number(value: object) -> bool:
    digits = _normalize(value)
    if digits is None or len(digits) != 12:
        return False
    body = digits[:11]
    check_digit = ord(digits[11]) - 48
    total = 0
    for n, ch in enumerate(reversed(body), start=1):
        p_n = ord(ch) - 48
        q_n = n + 1 if n <= 6 else n - 5
        total += p_n * q_n
    remainder = total % 11
    expected = 0 if remainder <= 1 else 11 - remainder
    return check_digit == expected

def is_corporate_number(value: object) -> bool:
    digits = _normalize(value)
    if digits is None or len(digits) != 13:
        return False
    check_digit = ord(digits[0]) - 48
    body = digits[1:]
    total = 0
    for pos, ch in enumerate(reversed(body), start=1):
        d = ord(ch) - 48
        prod = d * (1 if pos % 2 == 1 else 2)
        total += (prod // 10) + (prod % 10)
    rem = total % 9
    expected = 9 if rem == 0 else 9 - rem
    return check_digit == expected

def is_credit_card(value: object) -> bool:
    digits = _normalize(value)
    if digits is None or not (13 <= len(digits) <= 19):
        return False
    return _luhn_valid(digits)

def is_imei(value: object) -> bool:
    digits = _normalize(value)
    if digits is None or len(digits) != 15:
        return False
    return _luhn_valid(digits)

def classify(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    result: list[str] = []
    if is_my_number(value):
        result.append("my_number")
    if is_corporate_number(value):
        result.append("corporate_number")
    if is_credit_card(value):
        result.append("credit_card")
    if is_imei(value):
        result.append("imei")
    return result
