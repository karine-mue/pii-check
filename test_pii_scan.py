"""Tests for pii_scan.py — run: python3 test_pii_scan.py"""
import csv
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")
from pii_scan import scan_csv, scan_jsonl, scan_json_array, scan_file

failures = []

def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        failures.append(name)

# --- CSV fixture ---
rep = scan_csv("fixtures/sample.csv")
cols = rep["columns"]
check("csv rows", rep["rows_scanned"] == 10 and rep["reached_eof"] is True)
check("csv my_number column is pii",
      cols["individual_number"]["verdict"] == "pii"
      and cols["individual_number"]["matches"].get("my_number") == 10
      and cols["individual_number"]["tier"] == "check_digit")
check("csv phone column is possible (format tier cap)",
      cols["phone"]["verdict"] == "possible"
      and cols["phone"]["matches"].get("jp_phone") == 10
      and cols["phone"]["tier"] == "format")
check("csv mixed column is possible",
      cols["mixed"]["verdict"] == "possible"
      and cols["mixed"]["matches"].get("corporate_number") == 5
      and abs(cols["mixed"]["top_ratio"] - 0.5) < 1e-9)
check("csv plain columns are none",
      cols["name"]["verdict"] == "none" and cols["amount"]["verdict"] == "none"
      and cols["memo"]["verdict"] == "none" and cols["id"]["verdict"] == "none")
masked = cols["individual_number"]["example_masked"]
with open("fixtures/sample.csv", encoding="utf-8") as f:
    first_mn = list(csv.DictReader(f))[0]["individual_number"]
check("mask format", masked == first_mn[:2] + "*" * (len(first_mn) - 4) + first_mn[-2:]
      and "*" in masked and len(masked) == len(first_mn))

# --- JSONL fixture: same verdicts on the shared columns ---
rep_j = scan_jsonl("fixtures/sample.jsonl")
cj = rep_j["columns"]
check("jsonl rows", rep_j["rows_scanned"] == 5 and rep_j["reached_eof"] is True)
check("jsonl my_number pii", cj["individual_number"]["verdict"] == "pii")
check("jsonl phone possible", cj["phone"]["verdict"] == "possible")

# --- JSON array fixture ---
rep_a = scan_json_array("fixtures/sample.json")
ca = rep_a["columns"]
check("json array rows", rep_a["rows_scanned"] == 5 and rep_a["reached_eof"] is True)
check("json array my_number pii", ca["individual_number"]["verdict"] == "pii")

# --- scan_file auto-detect ---
rep_f = scan_file("fixtures/sample.csv")
check("scan_file detects csv", rep_f["format"] == "csv" and rep_f["rows_scanned"] == 10)
rep_f2 = scan_file("fixtures/sample.jsonl")
check("scan_file detects jsonl", rep_f2["format"] == "jsonl")

# --- JSON array with elements straddling the 64KB read-chunk boundary ---
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
    big_json_path = tf.name
    long_text = "あ" * 150000  # one element is ~450KB in UTF-8, spans several chunks
    data = [{"individual_number": "407925273107", "note": long_text},
            {"individual_number": "382218728553", "note": "short"}]
    json.dump(data, tf, ensure_ascii=False)
try:
    rep_big_json = scan_json_array(big_json_path)
    check("json array chunk boundary", rep_big_json["rows_scanned"] == 2
          and rep_big_json["columns"]["individual_number"]["verdict"] == "pii")
except Exception as e:
    check(f"json array chunk boundary (raised {type(e).__name__})", False)
os.unlink(big_json_path)

# --- sampling ---
rep_s = scan_csv("fixtures/sample.csv", sample_limit=4)
check("sample limit", rep_s["rows_scanned"] == 4 and rep_s["reached_eof"] is False)

# --- big file: 200k rows, full scan ---
def my_number_check_digit(base11):
    s = 0
    for n, ch in enumerate(reversed(base11), start=1):
        q = n + 1 if n <= 6 else n - 5
        s += int(ch) * q
    r = s % 11
    return 0 if r <= 1 else 11 - r

with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="",
                                 encoding="utf-8") as tf:
    big_path = tf.name
    w = csv.writer(tf)
    w.writerow(["row_id", "individual_number", "note"])
    for i in range(200_000):
        base = f"{i:011d}"[-11:]
        w.writerow([i, base + str(my_number_check_digit(base)), "test"])
t0 = time.time()
rep_b = scan_csv(big_path, sample_limit=None)
elapsed = time.time() - t0
os.unlink(big_path)
check("big rows", rep_b["rows_scanned"] == 200_000 and rep_b["reached_eof"] is True)
check("big verdict", rep_b["columns"]["individual_number"]["verdict"] == "pii"
      and rep_b["columns"]["individual_number"]["matches"].get("my_number") == 200_000)
print(f"big scan elapsed: {elapsed:.1f}s for 200k rows")
check("big scan under 120s", elapsed < 120)

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL TESTS PASSED")
