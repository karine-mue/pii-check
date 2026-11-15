import argparse
import csv
import json
import os
import sys
from pii_check import classify
from pathlib import Path

DEFAULT_SAMPLE_LIMIT = 100000
CHUNK_SIZE = 65536

TIERS = {
    "my_number": "check_digit",
    "corporate_number": "check_digit",
    "credit_card": "check_digit",
    "imei": "check_digit",
    "drivers_license": "check_digit",
    "jp_phone": "format",
}

def _mask_value(val: str) -> str:
    n = len(val)
    if n == 0:
        return ""
    if n > 4:
        return val[:2] + "*" * (n - 4) + val[-2:]
    return "*" * (n - 1) + val[-1:]

def _get_tier(pii_type: str) -> str:
    if pii_type == "jp_phone":
        return "format"
    return TIERS.get(pii_type, "check_digit")

class _ColumnStats:
    __slots__ = ("values_seen", "empty", "matches", "first_examples")

    def __init__(self, initial_empty: int = 0):
        self.values_seen = 0
        self.empty = initial_empty
        self.matches: dict[str, int] = {}
        self.first_examples: dict[str, str] = {}

    def record_empty(self):
        self.empty += 1

    def record_non_string_scalar(self):
        self.values_seen += 1

    def record_string(self, val: str) -> None:
        if val == "":
            self.empty += 1
            return
        self.values_seen += 1
        for pii_type in classify(val):
            self.matches[pii_type] = self.matches.get(pii_type, 0) + 1
            if pii_type not in self.first_examples:
                self.first_examples[pii_type] = val

    def to_report(self) -> dict:
        if not self.matches:
            return {
                "values_seen": self.values_seen,
                "empty": self.empty,
                "matches": {},
                "top_type": None,
                "top_ratio": 0.0,
                "tier": None,
                "verdict": "none",
                "example_masked": None,
            }
        top_type: str | None = None
        top_count = -1
        for pii_type, count in self.matches.items():
            if count > top_count:
                top_type = pii_type
                top_count = count
        assert top_type is not None
        top_ratio = top_count / self.values_seen if self.values_seen else 0.0
        tier = _get_tier(top_type)
        if tier == "check_digit":
            verdict = "pii" if top_ratio >= 0.8 else "possible" if top_ratio >= 0.2 else "none"
        elif tier == "format":
            verdict = "possible" if top_ratio >= 0.2 else "none"
        else:
            verdict = "none"
        return {
            "values_seen": self.values_seen,
            "empty": self.empty,
            "matches": dict(self.matches),
            "top_type": top_type,
            "top_ratio": top_ratio,
            "tier": tier,
            "verdict": verdict,
            "example_masked": _mask_value(self.first_examples[top_type]),
        }

def _process_json_record(record: dict, columns: dict, rows_prior: int):
    for k in record.keys():
        if k not in columns:
            columns[k] = _ColumnStats(initial_empty=rows_prior)
    for col_name, stats in columns.items():
        if col_name not in record:
            stats.record_empty()
            continue
        val = record[col_name]
        if val is None or val == "":
            stats.record_empty()
        elif isinstance(val, (dict, list)):
            continue
        elif isinstance(val, str):
            stats.record_string(val)
        else:
            stats.record_non_string_scalar()

def _build_report(path, fmt: str, rows_scanned: int, reached_eof: bool, columns: dict) -> dict:
    return {
        "source": str(path),
        "format": fmt,
        "rows_scanned": rows_scanned,
        "reached_eof": reached_eof,
        "columns": {name: stats.to_report() for name, stats in columns.items()},
    }

def scan_csv(path, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    columns = {}
    rows_scanned = 0
    reached_eof = True
    with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for name in fieldnames:
            if name is not None and name not in columns:
                columns[name] = _ColumnStats(initial_empty=0)
        if sample_limit is not None and sample_limit <= 0:
            try:
                next(reader)
                reached_eof = False
            except StopIteration:
                reached_eof = True
            return _build_report(path, "csv", rows_scanned, reached_eof, columns)
        for row in reader:
            for k in row.keys():
                if k is not None and k not in columns:
                    columns[k] = _ColumnStats(initial_empty=rows_scanned)
            for col_name, stats in columns.items():
                val = row.get(col_name)
                if val is None or val == "":
                    stats.record_empty()
                else:
                    stats.record_string(val)
            rows_scanned += 1
            if sample_limit is not None and rows_scanned >= sample_limit:
                reached_eof = False
                break
    return _build_report(path, "csv", rows_scanned, reached_eof, columns)

def scan_jsonl(path, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    columns = {}
    rows_scanned = 0
    reached_eof = True
    with open(path, mode="r", encoding="utf-8-sig") as f:
        if sample_limit is not None and sample_limit <= 0:
            for line in f:
                if line.strip():
                    reached_eof = False
                    break
            else:
                reached_eof = True
            return _build_report(path, "jsonl", rows_scanned, reached_eof, columns)
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            _process_json_record(record, columns, rows_scanned)
            rows_scanned += 1
            if sample_limit is not None and rows_scanned >= sample_limit:
                reached_eof = False
                break
    return _build_report(path, "jsonl", rows_scanned, reached_eof, columns)

def _iter_json_array(f, chunk_size=CHUNK_SIZE):
    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    eof = False

    def fill_buffer():
        nonlocal buf, pos, eof
        if pos > 0:
            buf = buf[pos:]
            pos = 0
        if eof:
            return
        chunk = f.read(chunk_size)
        if chunk == "":
            eof = True
        else:
            buf += chunk

    def skip_ws():
        nonlocal pos
        while True:
            fill_buffer()
            if pos >= len(buf):
                if eof:
                    return
                continue
            if buf[pos] in " \t\r\n":
                pos += 1
                continue
            return

    skip_ws()
    if pos >= len(buf):
        if eof:
            return
        raise ValueError("Invalid JSON: expected '['")
    if buf[pos] != "[":
        raise ValueError("Invalid JSON: expected '['")
    pos += 1
    while True:
        skip_ws()
        if pos >= len(buf):
            if eof:
                return
            raise ValueError("Invalid JSON: missing ']'")
        if buf[pos] == "]":
            pos += 1
            return
        while True:
            try:
                obj, end = decoder.raw_decode(buf, pos)
                break
            except json.JSONDecodeError:
                if eof:
                    raise
                fill_buffer()
        yield obj
        pos = end
        skip_ws()
        if pos >= len(buf):
            if eof:
                return
            raise ValueError("Invalid JSON: missing ',' or ']'")
        if buf[pos] == ",":
            pos += 1
            continue
        if buf[pos] == "]":
            pos += 1
            return
        raise ValueError(f"Invalid JSON: expected ',' or ']', got {buf[pos]!r}")

def scan_json_array(path, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    columns = {}
    rows_scanned = 0
    reached_eof = True
    with open(path, mode="r", encoding="utf-8-sig") as f:
        gen = _iter_json_array(f)
        if sample_limit is not None and sample_limit <= 0:
            try:
                next(gen)
                reached_eof = False
            except StopIteration:
                reached_eof = True
            return _build_report(path, "json", rows_scanned, reached_eof, columns)
        for record in gen:
            _process_json_record(record, columns, rows_scanned)
            rows_scanned += 1
            if sample_limit is not None and rows_scanned >= sample_limit:
                reached_eof = False
                break
    return _build_report(path, "json", rows_scanned, reached_eof, columns)

def scan_file(path, format=None, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    fmt = format
    if fmt is None:
        suffix = Path(path).suffix.lower()
        if suffix == ".csv":
            fmt = "csv"
        elif suffix in (".jsonl", ".ndjson"):
            fmt = "jsonl"
        elif suffix == ".json":
            fmt = "json"
        else:
            raise ValueError(f"Unsupported file extension: {suffix!r}")
    if fmt == "csv":
        return scan_csv(path, sample_limit=sample_limit)
    elif fmt == "jsonl":
        return scan_jsonl(path, sample_limit=sample_limit)
    elif fmt == "json":
        return scan_json_array(path, sample_limit=sample_limit)
    else:
        raise ValueError(f"Unsupported format: {fmt!r}")

def _print_summary(report: dict):
    print(f"source: {report['source']}")
    print(f"format: {report['format']}")
    print(f"rows_scanned: {report['rows_scanned']}")
    print(f"reached_eof: {report['reached_eof']}")
    for col_name, col_data in report["columns"].items():
        verdict = col_data["verdict"]
        top_type = col_data["top_type"]
        top_ratio = col_data["top_ratio"]
        example = col_data["example_masked"]
        print(
            f"{col_name}: verdict={verdict} top_type={top_type} "
            f"top_ratio={top_ratio:.4f} example={example}"
        )

def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan files for PII column by column.")
    parser.add_argument("file", metavar="FILE", help="Path to input file")
    parser.add_argument(
        "--format",
        choices=["csv", "jsonl", "json"],
        default=None,
        help="Input file format (csv, jsonl, json)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help=f"Maximum number of rows to scan (default: {DEFAULT_SAMPLE_LIMIT})",
    )
    group.add_argument(
        "--full",
        action="store_true",
        help="Scan the entire file without row limit",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        metavar="PATH",
        help="Write full report dict as JSON to PATH",
    )

    args = parser.parse_args(argv)

    if args.full:
        sample_limit = None
    elif args.sample is not None:
        sample_limit = args.sample
    else:
        sample_limit = DEFAULT_SAMPLE_LIMIT

    report = scan_file(args.file, format=args.format, sample_limit=sample_limit)

    _print_summary(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
