"""Create a redacted copy of a statement PDF: personal data is blacked out
and the text underneath is deleted.

The redacted PDF keeps the original layout, so an AI can read any bank's
statement visually. This script runs locally and sends nothing anywhere.

Usage:
    python redact_statement.py statement.pdf [-o redacted.pdf] [--private private_data.txt]

private_data.txt (never commit it) lists YOUR data, one value per line:
name variants, card/account numbers, CPF, street, district, CEP.
Matching ignores spaces, punctuation, accents and case.
Generic patterns (card numbers, CPF, CEP, barcodes...) are a safety net.

Environment variable (optional):
    STATEMENT_PDF_PASSWORD  PDF password (otherwise asked in the terminal)
"""

import argparse
import getpass
import os
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

# --- Generic patterns (safety net for data not in private_data.txt) ---------

# Card or account numbers: 13-19 digits/X/*, grouped (4-4-4-4, Amex 4-4-4-3)
# or in one run. Never ends right before ",00"/".00" (part of an amount).
CARD_RE = re.compile(r"(?<![\w*])[\dX*]{4}(?:[ .-]?[\dX*]{2,6}){1,4}(?![\w*]|[.,]\d)", re.I)
CARD_END_RE = re.compile(r"\b(final|término|termino|ending in)\s*\d{4}\b", re.I)
# Document/account codes: any word with 8+ digits in a row.
ID_RE = re.compile(r"\b(?=\w*\d{8})\w{10,}\b")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
CEP_RE = re.compile(r"\b\d{5}-\d{3}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
BARCODE_RE = re.compile(r"(?:\d[ .]?){20,}\d")
ADDRESS_LINE_RE = re.compile(
    r"^\s*(rua|r\.|av\.?|avenida|alameda|al\.|travessa|tv\.|rodovia|rod\.|"
    r"estrada|praça|praca|apto|apartamento)\b.*$",
    re.I,
)

# A transaction line has a date (07/10, 07 OUT) and an amount (84,90 / 84.90).
DATE_RE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}\b|\b\d{1,2}\s?[A-Za-z]{3}\b")
AMOUNT_RE = re.compile(r"\d[,.]\d{2}\b")

# Private values shorter than this only match whole words (avoids hits
# inside merchant names).
MIN_SUBSTRING_LEN = 5


def normalize(text):
    """Lowercase, remove accents, keep only letters and digits."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text.lower() if c.isalnum())


def is_card(s):
    chars = [c for c in s if c.isdigit() or c in "Xx*"]
    return 13 <= len(chars) <= 19 and sum(c.isdigit() for c in chars) >= 4


def is_transaction_line(line):
    return bool(DATE_RE.search(line) and AMOUNT_RE.search(line))


def load_private_values(path):
    if not path.exists():
        print(f"Warning: {path} not found. Only generic patterns will be used.")
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and normalize(line):
            values.append(normalize(line))
    return values


def find_words_to_redact(words, private_values):
    """Return {word index: label} for one text line of the PDF."""
    hits = {}
    texts = [w[4] for w in words]

    # 1. Private values: search the line with spaces/punctuation removed,
    #    so "1234 56XX" and "JohnMeyer" glued together still match.
    norm_words = [normalize(t) for t in texts]
    joined, owner = "", []
    for i, nw in enumerate(norm_words):
        joined += nw
        owner += [i] * len(nw)
    for value in private_values:
        if len(value) < MIN_SUBSTRING_LEN:
            for i, nw in enumerate(norm_words):
                if nw == value:
                    hits.setdefault(i, "PRIVATE")
            continue
        start = joined.find(value)
        while start != -1:
            for i in set(owner[start:start + len(value)]):
                hits.setdefault(i, "PRIVATE")
            start = joined.find(value, start + 1)

    # 2. Generic patterns on the line text (words joined by spaces).
    line, spans, pos = "", [], 0
    for t in texts:
        spans.append((pos, pos + len(t)))
        line += t + " "
        pos += len(t) + 1

    rules = [
        (BARCODE_RE, "BARCODE", None),
        (CARD_RE, "CARD", is_card),
        (CARD_END_RE, "CARD", None),
        (CPF_RE, "CPF", None),
        (CEP_RE, "CEP", None),
        (EMAIL_RE, "EMAIL", None),
        (ADDRESS_LINE_RE, "ADDRESS", None),
    ]
    # Codes with many digits are kept on transaction lines (e.g. a merchant ID).
    if not is_transaction_line(line):
        rules.append((ID_RE, "ID", None))

    for pattern, label, check in rules:
        for m in pattern.finditer(line):
            if check and not check(m.group(0)):
                continue
            for i, (a, b) in enumerate(spans):
                if a < m.end() and b > m.start():
                    hits.setdefault(i, label)
    return hits


def open_pdf(path):
    doc = pymupdf.open(path)
    if doc.needs_pass:
        password = os.environ.get("STATEMENT_PDF_PASSWORD") or getpass.getpass(
            "PDF password (hidden): "
        )
        if not doc.authenticate(password):
            sys.exit("Wrong PDF password.")
    return doc


def redact(doc, private_values):
    """Black out matches on every page. Return counts per label."""
    counts = {}
    for page in doc:
        lines = {}
        for w in page.get_text("words"):
            lines.setdefault((w[5], w[6]), []).append(w)  # (block, line)
        for words in lines.values():
            for i, label in find_words_to_redact(words, private_values).items():
                page.add_redact_annot(pymupdf.Rect(words[i][:4]), fill=(0, 0, 0))
                counts[label] = counts.get(label, 0) + 1
        page.apply_redactions()  # deletes the text under the black boxes
    doc.set_metadata({})  # author/title may contain the holder's name
    doc.del_xml_metadata()
    return counts


def leftover_check(path, private_values):
    """Reopen the redacted file and look for anything that should be gone."""
    with pymupdf.open(path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    norm = normalize(text)
    norm_words = {normalize(w) for w in text.split()}
    # Same rules as find_words_to_redact: short values only as whole words.
    left = sum(
        1
        for v in private_values
        if (v in norm if len(v) >= MIN_SUBSTRING_LEN else v in norm_words)
    )
    left += sum(1 for m in CARD_RE.finditer(text) if is_card(m.group(0)))
    for pattern in (CPF_RE, CEP_RE, EMAIL_RE, BARCODE_RE):
        left += len(pattern.findall(text))
    # Codes with many digits are kept on transaction lines.
    left += sum(
        len(ID_RE.findall(line)) for line in text.splitlines() if not is_transaction_line(line)
    )
    return left


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--private", type=Path, default=Path("private_data.txt"))
    args = parser.parse_args()

    if not args.pdf.exists():
        sys.exit(f"File not found: {args.pdf}")

    # Generic output name: bank file names may contain the account number.
    output = args.output or args.pdf.parent / "statement_redacted.pdf"
    private_values = load_private_values(args.private)

    doc = open_pdf(args.pdf)
    if not doc[0].get_text().strip():
        print("Warning: no text found. The PDF may be scanned; redact it manually.")
    counts = redact(doc, private_values)
    doc.save(output, garbage=4, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
    doc.close()

    print(f"Saved: {output}")
    print("Redacted words:", ", ".join(f"{k} {v}" for k, v in counts.items()) or "none")
    left = leftover_check(output, private_values)
    print(f"Leftover check: {'OK' if left == 0 else f'{left} possible leak(s), check!'}")
    print("Open the redacted PDF and check it visually before sending it anywhere.")


if __name__ == "__main__":
    main()