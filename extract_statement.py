"""Extract transactions from a Brazilian credit card statement PDF into Excel.

Usage:
    python extract_statement.py samples/sample_statement.pdf
    python extract_statement.py statement.pdf -o output/result.xlsx
"""

import argparse
import re
import sys
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pdfplumber

# Brazilian number format: 1.234,56 (negative values: -22,40)
AMOUNT = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
DOMESTIC_RE = re.compile(rf"^(\d{{2}})/(\d{{2}})\s+(.+?)\s+({AMOUNT})$")
INTERNATIONAL_RE = re.compile(rf"^(\d{{2}})/(\d{{2}})\s+(.+?)\s+({AMOUNT})\s+({AMOUNT})$")
DATE_START_RE = re.compile(r"^\d{2}/\d{2}\s")
AMOUNT_END_RE = re.compile(rf"({AMOUNT})$")
INSTALLMENT_RE = re.compile(r"\s(\d{2}/\d{2})$")
ISSUE_DATE_RE = re.compile(r"(?:emissao|postagem):\s*(\d{2})/(\d{2})/(\d{4})")

# Total lines printed by the bank (compared with our own sums), matched after fold()
TOTAL_PREFIXES = {
    "domestic": "lancamentos no cartao",
    "international": "total lancamentos inter",
    "grand": "total dos lancamentos atuais",
}

COLUMNS = [
    "Date",
    "Description",
    "Category",
    "City",
    "Installment",
    "Amount (BRL)",
    "Amount (USD)",
    "Type",
]


def fold(text):
    """Lowercase and remove accents, so matching works with or without them."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def parse_amount(text):
    """'1.234,56' -> Decimal('1234.56')."""
    return Decimal(text.replace(".", "").replace(",", "."))


def format_brl(value):
    """Decimal -> '1.234,56'."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def find_issue_date(lines):
    for line in lines:
        match = ISSUE_DATE_RE.search(fold(line))
        if match:
            day, month, year = (int(g) for g in match.groups())
            return date(year, month, day)
    raise ValueError("Could not find the statement issue date (Emissao/Postagem).")


def resolve_date(day, month, issue_date):
    """Statements show DD/MM only; a month after the issue month belongs to last year."""
    year = issue_date.year - 1 if month > issue_date.month else issue_date.year
    return date(year, month, day)


def split_category_city(line):
    """'eletrodomestico SAO PAULO' -> ('eletrodomestico', 'SAO PAULO')."""
    tokens = line.split()
    first_upper = next((i for i, t in enumerate(tokens) if t.isupper()), None)
    if first_upper is None:
        return line, ""
    if first_upper == 0:
        return tokens[0], " ".join(tokens[1:])
    return " ".join(tokens[:first_upper]), " ".join(tokens[first_upper:])


def parse_statement(text):
    """Parse the statement text. Returns issue date, transactions and the bank's totals."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    issue_date = find_issue_date(lines)
    transactions = []
    totals = {}
    section = None

    i = 0
    while i < len(lines):
        line = lines[i]
        folded = fold(line)

        if folded.startswith("pagamentos efetuados"):
            section = "payments"
        elif folded.startswith("lancamentos: compras e saques"):
            section = "domestic"
        elif folded.startswith("lancamentos internacionais"):
            section = "international"
        elif folded.startswith("limites de credito"):
            section = None

        for key, prefix in TOTAL_PREFIXES.items():
            if folded.startswith(prefix):
                match = AMOUNT_END_RE.search(line)
                if match:
                    totals[key] = parse_amount(match.group(1))
                if key == "grand":
                    section = None

        if section == "domestic":
            match = DOMESTIC_RE.match(line)
            if match:
                day, month, description, amount_text = match.groups()
                category, city = "", ""
                # Line 2 of each entry: category + city (no date, no amount)
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    if not DATE_START_RE.match(nxt) and not AMOUNT_END_RE.search(nxt):
                        category, city = split_category_city(nxt)
                        i += 1
                amount = parse_amount(amount_text)
                installment = INSTALLMENT_RE.search(description)
                transactions.append(
                    {
                        "Date": resolve_date(int(day), int(month), issue_date),
                        "Description": description,
                        "Category": category,
                        "City": city,
                        "Installment": installment.group(1) if installment else "",
                        "Amount (BRL)": amount,
                        "Amount (USD)": None,
                        "Type": "refund" if amount < 0 else "purchase",
                    }
                )

        elif section == "international":
            match = INTERNATIONAL_RE.match(line)
            if match:
                day, month, description, usd_text, brl_text = match.groups()
                transactions.append(
                    {
                        "Date": resolve_date(int(day), int(month), issue_date),
                        "Description": description,
                        "Category": "international",
                        "City": "",
                        "Installment": "",
                        "Amount (BRL)": parse_amount(brl_text),
                        "Amount (USD)": parse_amount(usd_text),
                        "Type": "international",
                    }
                )
            elif folded.startswith("repasse de iof"):
                match = AMOUNT_END_RE.search(line)
                if match:
                    transactions.append(
                        {
                            "Date": None,
                            "Description": "Repasse de IOF",
                            "Category": "iof",
                            "City": "",
                            "Installment": "",
                            "Amount (BRL)": parse_amount(match.group(1)),
                            "Amount (USD)": None,
                            "Type": "fee",
                        }
                    )

        i += 1

    return {"issue_date": issue_date, "transactions": transactions, "totals": totals}


def validate(transactions, totals):
    """Compare our sums with the totals printed on the statement."""

    def total_of(*types):
        values = [t["Amount (BRL)"] for t in transactions if t["Type"] in types]
        return sum(values, Decimal("0"))

    calculated = {
        "domestic": total_of("purchase", "refund"),
        "international": total_of("international", "fee"),
        "grand": total_of("purchase", "refund", "international", "fee"),
    }
    labels = {
        "domestic": "Domestic purchases",
        "international": "International (incl. IOF)",
        "grand": "Total of the statement",
    }
    checks = []
    for key in ("domestic", "international", "grand"):
        expected = totals.get(key)
        checks.append(
            {
                "name": labels[key],
                "calculated": calculated[key],
                "expected": expected,
                "ok": None if expected is None else calculated[key] == expected,
            }
        )
    return checks


def main_column(page):
    """Drop the right-hand sidebar (credit limits, charges), which would otherwise
    be interleaved with the transaction lines in the extracted text."""
    starts = [
        w["x0"]
        for w in page.extract_words()
        if fold(w["text"]) in ("limites", "encargos") and w["x0"] > page.width * 0.4
    ]
    if not starts:
        return page
    return page.crop((0, 0, min(starts) - 2, page.height))


def read_pdf_text(path):
    with pdfplumber.open(path) as pdf:
        text = "\n".join((main_column(page).extract_text() or "") for page in pdf.pages)
    if not text.strip():
        raise ValueError("No selectable text found (scanned PDF? OCR is not supported).")
    return text


def build_dataframe(transactions):
    df = pd.DataFrame(transactions, columns=COLUMNS)
    df["Amount (BRL)"] = df["Amount (BRL)"].map(float)
    df["Amount (USD)"] = df["Amount (USD)"].map(lambda v: None if v is None else float(v))
    return df


def write_excel(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet = "Transactions"
    with pd.ExcelWriter(path, engine="openpyxl", date_format="DD/MM/YYYY") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]
        ws.freeze_panes = "A2"
        widths = {"A": 12, "B": 34, "C": 18, "D": 16, "E": 12, "F": 14, "G": 14, "H": 14}
        for column, width in widths.items():
            ws.column_dimensions[column].width = width
        for column in ("F", "G"):
            for cell in ws[column][1:]:
                cell.number_format = "#,##0.00"


def print_summary(pdf_path, output_path, result, df, checks):
    transactions = result["transactions"]
    print(f"File: {pdf_path}")
    print(f"Statement issued: {result['issue_date']:%d/%m/%Y}")
    print(f"Transactions extracted: {len(transactions)}")
    print()
    print("Validation (our sum vs. total printed on the statement):")
    for check in checks:
        status = {True: "OK", False: "MISMATCH", None: "NOT FOUND"}[check["ok"]]
        expected = "-" if check["expected"] is None else format_brl(check["expected"])
        print(
            f"  [{status}] {check['name']}: "
            f"calculated {format_brl(check['calculated'])} | statement {expected}"
        )
    print()
    print("Total by category (BRL):")
    by_category = (
        df.assign(Category=df["Category"].replace("", "(none)"))
        .groupby("Category")["Amount (BRL)"]
        .sum()
        .sort_values(ascending=False)
    )
    for category, value in by_category.items():
        print(f"  {category:<18}{format_brl(Decimal(str(round(value, 2)))):>12}")
    print()
    print(f"Excel saved to: {output_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract transactions from a credit card statement PDF into Excel."
    )
    parser.add_argument("pdf", help="path to the statement PDF (selectable text)")
    parser.add_argument("-o", "--output", help="output .xlsx path (default: output/<name>.xlsx)")
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"Error: file not found: {pdf_path}", file=sys.stderr)
        return 2
    output_path = Path(args.output) if args.output else Path("output") / f"{pdf_path.stem}.xlsx"

    try:
        result = parse_statement(read_pdf_text(pdf_path))
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    checks = validate(result["transactions"], result["totals"])
    df = build_dataframe(result["transactions"])
    write_excel(df, output_path)
    print_summary(pdf_path, output_path, result, df, checks)

    if any(check["ok"] is not True for check in checks):
        print("\nWARNING: validation failed. Check the Excel against the PDF.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
