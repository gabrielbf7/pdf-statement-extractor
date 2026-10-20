"""Merchant grouping and "unknown merchant" detection for statement transactions.

Statement descriptions are truncated and glued to the (also truncated) city, e.g.
"PadariaSolNasceCURI" + city "CURITIBA". This module cleans them, groups the
transactions by merchant and flags descriptions that look like codes or
payment intermediaries, with hints taken only from the statement itself.
"""

import re
import unicodedata

import pandas as pd

INSTALLMENT_SUFFIX_RE = re.compile(r"\s+\d{2}/\d{2}$")
REFUND_PREFIX_RE = re.compile(r"^estorno\s+", re.IGNORECASE)
MIN_CITY_FRAGMENT = 2  # shortest city prefix we trust when stripping it from a name
MIN_VOWEL_RATIO = 0.25  # words with fewer vowels look like codes (e.g. "hujklz")
MIN_CODE_WORD_LENGTH = 5

UNKNOWN_COLUMNS = [
    "Date",
    "Description",
    "Amount (BRL)",
    "City",
    "Category",
    "Reason",
    "Hints",
    "Your note",
]
MERCHANT_COLUMNS = [
    "Merchant",
    "Transactions",
    "Total (BRL)",
    "Category",
    "First date",
    "Last date",
]


def _fold(text):
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _alnum(text):
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def _strip_city(name, city):
    """Remove a (possibly truncated) city glued to the end of the name.

    'PadariaSolNasceCURI' with city 'CURITIBA' -> 'PadariaSolNasce'. The longest
    tail of the name that is a prefix of the city wins.
    """
    city_key = _alnum(city)
    if not city_key:
        return name
    for size in range(len(name) - 3, MIN_CITY_FRAGMENT - 1, -1):
        tail = _alnum(name[-size:])
        if len(tail) >= MIN_CITY_FRAGMENT and city_key.startswith(tail):
            return name[:-size].rstrip()
    return name


def clean_merchant(description, city=""):
    """Readable merchant name: no installment, refund prefix, city or '*'."""
    name = INSTALLMENT_SUFFIX_RE.sub("", description).strip()
    name = REFUND_PREFIX_RE.sub("", name)
    name = _strip_city(name, city)
    name = name.replace("*", " ")
    return " ".join(name.split())


def merchant_key(name):
    """Grouping key: ignores case, accents, spaces and punctuation."""
    return _alnum(name)


def code_like_reason(name):
    """Return why a cleaned name looks like a code, or '' if it looks like a name."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    for word in words:
        has_letter = any(c.isalpha() for c in word)
        if len(word) >= 4 and has_letter and any(c.isdigit() for c in word):
            return "code-like name (letters mixed with digits)"
    # Vowel check only for one-word names: in longer names a vowel-less word is
    # usually just a truncated word (e.g. "RESTLTD"), not a code.
    if len(words) == 1:
        letters = [c for c in _fold(words[0]) if c.isalpha()]
        if len(letters) >= MIN_CODE_WORD_LENGTH:
            vowels = sum(1 for c in letters if c in "aeiou")
            if vowels / len(letters) < MIN_VOWEL_RATIO:
                return "code-like name (almost no vowels)"
    return ""


def _with_merchants(df):
    work = df.copy()
    work["Date"] = pd.to_datetime(work["Date"])
    work["Merchant"] = [clean_merchant(d, c) for d, c in zip(work["Description"], work["City"])]
    work["_key"] = work["Merchant"].map(merchant_key)
    return work


def _first_non_empty(series):
    for value in series:
        if value:
            return value
    return ""


def merchant_summary(df):
    """One row per merchant: count, total, category, first/last date. Biggest total first."""
    work = _with_merchants(df)
    summary = (
        work.groupby("_key", sort=False)
        .agg(
            Merchant=("Merchant", "first"),
            Transactions=("Amount (BRL)", "size"),
            **{"Total (BRL)": ("Amount (BRL)", "sum")},
            Category=("Category", _first_non_empty),
            **{"First date": ("Date", "min"), "Last date": ("Date", "max")},
        )
        .reset_index(drop=True)
    )
    summary["Total (BRL)"] = summary["Total (BRL)"].round(2)
    summary = summary.sort_values("Total (BRL)", ascending=False, kind="stable")
    return summary[MERCHANT_COLUMNS].reset_index(drop=True)


def unknown_merchants(df):
    """Transactions whose name looks like a code or goes through an intermediary ('*').

    Hints come only from the statement (category, city, repeats, installment, foreign
    amount). Nothing is looked up online, so nothing here is confirmed.
    """
    work = _with_merchants(df)
    work = work[work["Type"] != "fee"]
    counts = work["_key"].value_counts()
    rows = []
    for _, row in work.iterrows():
        reasons = []
        code_reason = code_like_reason(row["Merchant"])
        if code_reason:
            reasons.append(code_reason)
        if "*" in row["Description"]:
            reasons.append("has '*' (payment intermediary or gateway)")
        if not reasons:
            continue
        hints = []
        if row["Category"] and row["Type"] != "international":
            hints.append(f"Statement category: {row['Category']}")
        if row["City"]:
            hints.append(f"City: {row['City']}")
        if row["Installment"]:
            hints.append(f"Installment {row['Installment']}")
        if pd.notna(row["Amount (USD)"]):
            hints.append(f"Foreign amount: {row['Amount (USD)']:.2f}")
        if counts[row["_key"]] > 1:
            hints.append(f"Appears {counts[row['_key']]}x on this statement")
        rows.append(
            {
                "Date": row["Date"],
                "Description": row["Description"],
                "Amount (BRL)": row["Amount (BRL)"],
                "City": row["City"],
                "Category": row["Category"],
                "Reason": "; ".join(reasons),
                "Hints": " | ".join(hints),
                "Your note": "",
            }
        )
    return pd.DataFrame(rows, columns=UNKNOWN_COLUMNS)
