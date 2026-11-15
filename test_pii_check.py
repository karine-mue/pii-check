"""Tests for pii_check.py — run: python3 test_pii_check.py
Vectors were minted with the published official check-digit formulas.
Card numbers 4242.../3782... are public industry test numbers, not real cards.
"""
import sys

sys.path.insert(0, ".")
from pii_check import is_my_number, is_corporate_number, is_credit_card, is_imei, classify

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

# --- classify ---
check("classify my_number", "my_number" in classify(MY_OK[0]))
check("classify corporate", "corporate_number" in classify(CO_OK[0]))
check("classify card", "credit_card" in classify("4242424242424242"))
check("classify imei", "imei" in classify(IMEI_OK[0]))
check("classify garbage", classify("hello") == [])
check("classify corrupted", "my_number" not in classify(MY_NG[0]))

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL TESTS PASSED")
