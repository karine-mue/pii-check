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
# Row i=0 mints 000000000000, the all-zero dummy that is never a valid
# identifier (2026-11-15 rule), so matches are 199,999 of 200,000.
check("big verdict", rep_b["columns"]["individual_number"]["verdict"] == "pii"
      and rep_b["columns"]["individual_number"]["matches"].get("my_number") == 199_999)
print(f"big scan elapsed: {elapsed:.1f}s for 200k rows")
check("big scan under 120s", elapsed < 120)

# --- review fixes (2026-11-15): JSON array streaming & error handling ---
def _write_tmp(text, suffix):
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                     encoding="utf-8") as tf:
        tf.write(text)
        return tf.name

def _raises_value_error(fn, path):
    try:
        fn(path)
        return False
    except ValueError:
        return True
    finally:
        os.unlink(path)

# truncated array (missing ']') must raise, not end silently
p = _write_tmp('[{"a": "407925273107"}, {"a": "382218728553"}', ".json")
check("json truncated raises", _raises_value_error(scan_json_array, p))
# trailing comma must raise
p = _write_tmp('[{"a": 1},]', ".json")
check("json trailing comma raises", _raises_value_error(scan_json_array, p))
# data after the closing bracket must raise
p = _write_tmp('[{"a": 1}] xyz', ".json")
check("json trailing data raises", _raises_value_error(scan_json_array, p))
# empty input must raise
p = _write_tmp('', ".json")
check("json empty raises", _raises_value_error(scan_json_array, p))
# a syntax error early in the file raises without needing EOF
p = _write_tmp('[{"a": tru}, {"b": 2}]', ".json")
check("json bad literal raises", _raises_value_error(scan_json_array, p))

# reached_eof is exact when the record count equals the sample limit
p = _write_tmp("\n".join(json.dumps({"a": "407925273107"}) for _ in range(5)) + "\n", ".jsonl")
rep = scan_jsonl(p, sample_limit=5)
os.unlink(p)
check("jsonl exact limit eof", rep["rows_scanned"] == 5 and rep["reached_eof"] is True)
p = _write_tmp(json.dumps([{"a": "407925273107"}] * 5), ".json")
rep = scan_json_array(p, sample_limit=5)
os.unlink(p)
check("json exact limit eof", rep["rows_scanned"] == 5 and rep["reached_eof"] is True)

# non-object JSONL records are skipped and counted, not fatal
p = _write_tmp('{"a": "407925273107"}\n[1, 2]\n"str"\n{"a": "382218728553"}\n', ".jsonl")
rep = scan_jsonl(p)
os.unlink(p)
check("jsonl non-object skipped", rep["rows_scanned"] == 4 and rep["skipped_non_object"] == 2
      and rep["columns"]["a"]["matches"].get("my_number") == 2)
# malformed JSONL line raises with the line number
p = _write_tmp('{"a": 1}\n{bad}\n', ".jsonl")
try:
    scan_jsonl(p)
    check("jsonl malformed raises", False)
except ValueError as exc:
    check("jsonl malformed raises", "line 2" in str(exc))
finally:
    os.unlink(p)

# IMEI column: top_type must be imei, not credit_card (tie-break by specificity)
def _luhn_complete(body):
    s = 0
    for i, ch in enumerate(reversed(body + "0")):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        s += d
    return body + str((10 - s % 10) % 10)

imeis = [_luhn_complete(b) for b in ("49015420323751", "35693803564380", "85985806325661")]
p = _write_tmp("\n".join(json.dumps({"device": v}) for v in imeis) + "\n", ".jsonl")
rep = scan_jsonl(p)
os.unlink(p)
check("imei top_type", rep["columns"]["device"]["top_type"] == "imei"
      and rep["columns"]["device"]["matches"].get("credit_card") == 3)

# whitespace-only values count as empty, not as denominator
p = _write_tmp('a,b\n" ",407925273107\n"  ",382218728553\n', ".csv")
rep = scan_csv(p)
os.unlink(p)
check("whitespace-only is empty", rep["columns"]["a"]["empty"] == 2
      and rep["columns"]["a"]["values_seen"] == 0)

# duplicate CSV header names are an error, not a silent overwrite
p = _write_tmp('a,a\n1,2\n', ".csv")
check("csv duplicate header raises", _raises_value_error(scan_csv, p))

# negative sample limits are rejected
for fn in (scan_csv, scan_jsonl, scan_json_array):
    try:
        fn("nonexistent", sample_limit=-1)
        check(f"{fn.__name__} negative sample raises", False)
    except ValueError:
        check(f"{fn.__name__} negative sample raises", True)

# --- Argon re-review fixes (2026-11-15): chunk-boundary token splits ---
import io as _io
from pii_scan import _iter_json_array as _ija

def _ija_run(text, chunk):
    try:
        return list(_ija(_io.StringIO(text), chunk_size=chunk))
    except ValueError:
        return None

check("chunk split unicode escape", _ija_run('[{"a": "' + "x" * 20 + '\\u3042"}]', 16) is not None
      and _ija_run('[{"a": "' + "x" * 20 + '\\u3042"}]', 30) is not None)
check("chunk split literal", _ija_run('[{"a": true}]', 8) == [{"a": True}]
      and _ija_run('[{"a": null}]', 8) == [{"a": None}])
check("chunk split fraction", _ija_run('[123.45, 678]', 5) == [123.45, 678])
check("chunk split exponent", _ija_run('[1e5, 2]', 3) == [100000.0, 2])
check("chunk split negative", _ija_run('[-123, 5]', 2) == [-123, 5])
check("chunk genuine error still raises", _ija_run('[{"a": tru}, {"b": 2}]', 65536) is None
      and _ija_run('[{"a": 1}, {"b": @}]', 65536) is None)
check("chunk malformed number raises", _ija_run('[123.x]', 65536) is None)

# skipped non-objects must not inflate initial_empty of later columns
p = _write_tmp('[1, 2]\n"s"\n{"a": "407925273107"}\n{"a": "382218728553", "b": "x"}\n', ".jsonl")
rep = scan_jsonl(p)
os.unlink(p)
check("skipped rows not in initial_empty", rep["skipped_non_object"] == 2
      and rep["columns"]["a"]["empty"] == 0 and rep["columns"]["b"]["empty"] == 1)

# example_masked is computed over the separator-stripped identifier
p = _write_tmp('{"n": "4079-2527-3107"}\n', ".jsonl")
rep = scan_jsonl(p)
os.unlink(p)
check("example masked on stripped form", rep["columns"]["n"]["example_masked"] == "40********07")

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL TESTS PASSED")
