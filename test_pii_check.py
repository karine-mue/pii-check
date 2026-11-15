"""Tests for pii_check.py — run: python3 test_pii_check.py
Vectors were minted with the published official check-digit formulas.
Card numbers 4242.../3782... are public industry test numbers, not real cards.
"""
import sys

sys.path.insert(0, ".")
from pii_check import (is_my_number, is_corporate_number, is_credit_card, is_imei,
                       is_drivers_license, is_jp_phone_number, classify)

failures = []

def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        failures.append(name)

MY_OK = ["407925273107", "382218728553", "257528096051"]
MY_NG = ["407925273108", "382218728554", "257528096052"]
CO_OK = ["2407925273103", "8822187285525", "7752809605845"]
CO_NG = ["3407925273103", "9822187285525", "8752809605845"]
CC_OK = ["2882585187594899", "1969106722338466", "0614360181839396",
         "4242424242424242", "378282246310005"]
CC_NG = ["2882585187594890", "1969106722338467", "0614360181839397",
         "4242424242424241"]
IMEI_OK = ["042675612697051", "310817538957095", "364075174303349"]
IMEI_NG = ["042675612697052", "310817538957096", "364075174303340"]

# --- valid vectors ---
check("my_number valid", all(is_my_number(v) for v in MY_OK))
check("corporate valid", all(is_corporate_number(v) for v in CO_OK))
check("credit_card valid", all(is_credit_card(v) for v in CC_OK))
check("imei valid", all(is_imei(v) for v in IMEI_OK))

# --- corrupted check digits ---
check("my_number invalid", not any(is_my_number(v) for v in MY_NG))
check("corporate invalid", not any(is_corporate_number(v) for v in CO_NG))
check("credit_card invalid", not any(is_credit_card(v) for v in CC_NG))
check("imei invalid", not any(is_imei(v) for v in IMEI_NG))

# --- separators are accepted ---
check("my_number spaces", is_my_number("4079 2527 3107"))
check("corporate spaces", is_corporate_number("2407 9252 7310 3"))
check("credit_card hyphens", is_credit_card("4242-4242-4242-4242"))
check("credit_card spaces", is_credit_card("3782 822463 10005"))

# --- length and character rules ---
check("my_number 11 digits", not is_my_number("40792527310"))
check("my_number 13 digits", not is_my_number("4079252731080"))
check("corporate 12 digits", not is_corporate_number("845956513106"))
check("imei 14 digits", not is_imei("04267561269705"))
check("imei 16 digits", not is_imei("0426756126970511"))
check("credit_card 12 digits", not is_credit_card("424242424242"))
check("letters rejected", not is_my_number("40792527310a"))
check("fullwidth rejected", not is_my_number("４０７９２５２７３１０８"))
check("empty rejected", not is_my_number("") and not is_credit_card(""))

# --- non-string input never raises ---
check("none input", is_my_number(None) is False and classify(None) == [])
check("int input", is_credit_card(4242424242424242) is False)

# --- driver's license (check digit at position 11; weights 5,4,3,2,7,6,5,4,3,2) ---
LIC_OK = ["407925273120", "038221872840", "552575280990"]
LIC_NG = ["407925273130", "038221872850", "552575280900"]
check("license valid", all(is_drivers_license(v) for v in LIC_OK))
check("license invalid", not any(is_drivers_license(v) for v in LIC_NG))
check("license 11 digits", not is_drivers_license("40792527312"))
check("license worked example", is_drivers_license("122345678940"))  # body 1223456789 -> check 4, reissue 0

# --- Japanese phone numbers (format tier, no check digit) ---
check("phone mobile 090", is_jp_phone_number("09012345678"))
check("phone mobile hyphens", is_jp_phone_number("080-1234-5678"))
check("phone mobile 070", is_jp_phone_number("07012345678"))
check("phone ip 050", is_jp_phone_number("05012345678"))
check("phone +81 form", is_jp_phone_number("+819012345678"))
check("phone landline tokyo", is_jp_phone_number("0312345678"))
check("phone landline 4digit area", is_jp_phone_number("0467123456"))
check("phone tollfree 0120", is_jp_phone_number("0120123456"))
check("phone tollfree 0800 is 11 digits", is_jp_phone_number("08001234567"))
check("phone 10digit 0800 invalid", not is_jp_phone_number("0800123456"))
check("phone navi 0570", is_jp_phone_number("0570123456"))
check("phone 10digit mobile prefix", not is_jp_phone_number("0901234567"))
check("phone 10digit 050", not is_jp_phone_number("0501234567"))
check("phone 10digit 080 not 0800", not is_jp_phone_number("0801123456"))
check("phone no leading zero", not is_jp_phone_number("1234567890"))
check("phone 00 start", not is_jp_phone_number("0012345678"))
check("phone 12 digits", not is_jp_phone_number("090123456789"))
check("phone special 110", not is_jp_phone_number("110"))
check("phone none input", is_jp_phone_number(None) is False)

# --- classify ---
check("classify my_number", "my_number" in classify(MY_OK[0]))
check("classify corporate", "corporate_number" in classify(CO_OK[0]))
check("classify card", "credit_card" in classify("4242424242424242"))
check("classify imei", "imei" in classify(IMEI_OK[0]))
check("classify license", "drivers_license" in classify(LIC_OK[0]))
check("classify phone", "jp_phone" in classify("09012345678"))
check("classify garbage", classify("hello") == [])
check("classify corrupted", "my_number" not in classify(MY_NG[0]))

# --- all-zero values are never valid identifiers (2026-11-15 review fix) ---
check("zero my_number", not is_my_number("000000000000"))
check("zero corporate", not is_corporate_number("0000000000000"))
check("zero credit_card", not is_credit_card("0000000000000000"))
check("zero imei", not is_imei("000000000000000"))
check("zero drivers_license", not is_drivers_license("000000000000"))
check("zero with separators", not is_my_number("0000-0000-0000"))
check("zero classify empty", classify("000000000000") == [])

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL TESTS PASSED")
