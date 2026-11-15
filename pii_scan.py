import argparse
import csv
import json
import sys
from pathlib import Path

from pii_check import classify

DEFAULT_SAMPLE_LIMIT = 100000
CHUNK_SIZE = 65536

# Real-world cells can exceed the csv module's default 128KiB field limit.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

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

# Tie-break order for top_type when match counts are equal: narrower,
# length-exact definitions win over broader ones. In particular a 15-digit
# Luhn-valid value matches both imei (exactly 15 digits) and credit_card
# (13-19 digits); the column must be reported as imei, not credit_card.
_TIE_PRIORITY = [
    "my_number",
    "corporate_number",
    "drivers_license",
    "imei",
    "credit_card",
    "jp_phone",
]

def _get_tier(pii_type: str) -> str:
    # Single source of truth is TIERS. An unknown type is a bug, so fail
    # closed with KeyError instead of defaulting to the stronger tier.
    return TIERS[pii_type]

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
        if val.strip() == "":
            # Whitespace-only values can never match (classify strips
            # spaces), so count them as empty instead of inflating the
            # top_ratio denominator.
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
        top_rank = len(_TIE_PRIORITY)
        for pii_type, count in self.matches.items():
            rank = (
                _TIE_PRIORITY.index(pii_type)
                if pii_type in _TIE_PRIORITY
                else len(_TIE_PRIORITY)
            )
            if count > top_count or (count == top_count and rank < top_rank):
                top_type = pii_type
                top_count = count
                top_rank = rank
        assert top_type is not None
        top_ratio = top_count / self.values_seen if self.values_seen else 0.0
        tier = _get_tier(top_type)
        if tier == "check_digit":
            verdict = "pii" if top_ratio >= 0.8 else "possible" if top_ratio >= 0.2 else "none"
        else:  # "format" tier: shape alone never confirms PII
            verdict = "possible" if top_ratio >= 0.2 else "none"
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

def _build_report(
    path,
    fmt: str,
    rows_scanned: int,
    reached_eof: bool,
    columns: dict,
    skipped_non_object: int = 0,
) -> dict:
    return {
        "source": str(path),
        "format": fmt,
        "rows_scanned": rows_scanned,
        "reached_eof": reached_eof,
        "skipped_non_object": skipped_non_object,
        "columns": {name: stats.to_report() for name, stats in columns.items()},
    }

def scan_csv(path, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    if sample_limit is not None and sample_limit < 0:
        raise ValueError(f"sample_limit must be >= 0 or None, got {sample_limit}")
    columns = {}
    rows_scanned = 0
    reached_eof = True
    with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        named = [name for name in fieldnames if name is not None]
        if len(set(named)) != len(named):
            dupes = sorted({name for name in named if named.count(name) > 1})
            raise ValueError(f"Duplicate column names in CSV header: {dupes}")
        for name in named:
            columns[name] = _ColumnStats(initial_empty=0)
        rows = iter(reader)
        while True:
            if sample_limit is not None and rows_scanned >= sample_limit:
                # Peek one row so reached_eof is exact even when the file
                # happens to contain precisely sample_limit records.
                reached_eof = next(rows, None) is None
                break
            row = next(rows, None)
            if row is None:
                reached_eof = True
                break
            for col_name, stats in columns.items():
                val = row.get(col_name)
                if val is None or val == "":
                    stats.record_empty()
                else:
                    stats.record_string(val)
            rows_scanned += 1
    return _build_report(path, "csv", rows_scanned, reached_eof, columns)

def scan_jsonl(path, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    if sample_limit is not None and sample_limit < 0:
        raise ValueError(f"sample_limit must be >= 0 or None, got {sample_limit}")
    columns = {}
    rows_scanned = 0
    skipped_non_object = 0
    reached_eof = True
    with open(path, mode="r", encoding="utf-8-sig") as f:
        lines = iter(f)
        line_no = 0
        pending: str | None = None

        def next_record_line() -> str | None:
            nonlocal line_no, pending
            if pending is not None:
                line = pending
                pending = None
                return line
            for line in lines:
                line_no += 1
                if line.strip():
                    return line
            return None

        while True:
            if sample_limit is not None and rows_scanned >= sample_limit:
                pending = next_record_line()
                reached_eof = pending is None
                break
            line = next_record_line()
            if line is None:
                reached_eof = True
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_no} of {path}: {exc.msg}"
                ) from exc
            if isinstance(record, dict):
                _process_json_record(record, columns, rows_scanned)
            else:
                # Valid JSON but not an object: counted, not silently lost.
                skipped_non_object += 1
            rows_scanned += 1
    return _build_report(
        path, "jsonl", rows_scanned, reached_eof, columns, skipped_non_object
    )

def _iter_json_array(f, chunk_size=CHUNK_SIZE):
    """Yield the elements of a top-level JSON array, streaming.

    The buffer is refilled only when the parser actually needs more input,
    so memory stays bounded by the largest single element plus one chunk.
    Truncated or malformed input raises ValueError; it never ends the
    iteration silently.
    """
    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    eof = False

    def fill() -> bool:
        """Append one chunk to the buffer. Returns False at EOF."""
        nonlocal buf, pos, eof
        if eof:
            return False
        chunk = f.read(chunk_size)
        if chunk == "":
            eof = True
            return False
        if pos > 0:
            buf = buf[pos:]
            pos = 0
        buf += chunk
        return True

    def peek() -> str | None:
        """Return the next non-whitespace char without consuming it.

        Fills the buffer only when it is exhausted, so whitespace never
        triggers a read by itself.
        """
        nonlocal pos
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n":
                pos += 1
            if pos < len(buf):
                return buf[pos]
            if not fill():
                return None

    def parse_element():
        """Parse one element starting at pos (a non-whitespace char)."""
        nonlocal pos
        while True:
            try:
                obj, end = decoder.raw_decode(buf, pos)
            except json.JSONDecodeError as exc:
                if eof:
                    raise ValueError(f"Invalid JSON array: {exc.msg}") from exc
                # Retry with more input only when the failure can be caused
                # by a truncated buffer: an unterminated string, or an error
                # at the very end of what has been read so far. Anything
                # else is a genuine syntax error; report it immediately.
                if exc.msg.startswith("Unterminated string") or exc.pos >= len(buf):
                    if fill():
                        continue
                    raise ValueError(f"Invalid JSON array: {exc.msg}") from exc
                raise ValueError(f"Invalid JSON array: {exc.msg}") from exc
            if end == len(buf) and not eof:
                # A scalar value may continue in the next chunk (e.g. a
                # number split as 123|45). Refill and reparse to be sure
                # the value is complete before yielding it.
                if fill():
                    continue
            pos = end
            return obj

    if peek() is None:
        raise ValueError("Invalid JSON: empty input, expected '['")
    if buf[pos] != "[":
        raise ValueError("Invalid JSON: expected '['")
    pos += 1

    if peek() == "]":
        pos += 1
        if peek() is not None:
            raise ValueError("Invalid JSON: data after closing ']'")
        return

    while True:
        ch = peek()
        if ch is None:
            raise ValueError("Invalid JSON: truncated array, missing ']'")
        yield parse_element()
        ch = peek()
        if ch == ",":
            pos += 1
            # A trailing comma is invalid JSON: after ',' an element must
            # follow, so ']' here is an error, not the end of the array.
            if peek() == "]":
                raise ValueError("Invalid JSON: trailing comma in array")
            continue
        if ch == "]":
            pos += 1
            if peek() is not None:
                raise ValueError("Invalid JSON: data after closing ']'")
            return
        if ch is None:
            raise ValueError("Invalid JSON: truncated array, missing ',' or ']'")
        raise ValueError(f"Invalid JSON: expected ',' or ']', got {ch!r}")

def scan_json_array(path, sample_limit=DEFAULT_SAMPLE_LIMIT) -> dict:
    if sample_limit is not None and sample_limit < 0:
        raise ValueError(f"sample_limit must be >= 0 or None, got {sample_limit}")
    columns = {}
    rows_scanned = 0
    skipped_non_object = 0
    reached_eof = True
    with open(path, mode="r", encoding="utf-8-sig") as f:
        gen = _iter_json_array(f)
        while True:
            if sample_limit is not None and rows_scanned >= sample_limit:
                try:
                    next(gen)
                    reached_eof = False
                except StopIteration:
                    reached_eof = True
                break
            try:
                record = next(gen)
            except StopIteration:
                reached_eof = True
                break
            if isinstance(record, dict):
                _process_json_record(record, columns, rows_scanned)
            else:
                skipped_non_object += 1
            rows_scanned += 1
    return _build_report(
        path, "json", rows_scanned, reached_eof, columns, skipped_non_object
    )

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
    if report.get("skipped_non_object"):
        print(f"skipped_non_object: {report['skipped_non_object']}")
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

    if args.sample is not None and args.sample < 0:
        parser.error("--sample must be >= 0")

    if args.full:
        sample_limit = None
    elif args.sample is not None:
        sample_limit = args.sample
    else:
        sample_limit = DEFAULT_SAMPLE_LIMIT

    try:
        report = scan_file(args.file, format=args.format, sample_limit=sample_limit)
    except (ValueError, OSError, UnicodeDecodeError, csv.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_summary(report)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=2)
    return 0

if __name__ == "__main__":
    sys.exit(main())
