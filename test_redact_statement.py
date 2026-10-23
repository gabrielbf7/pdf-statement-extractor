"""Tests for redact_statement.py using fictional data only."""

from pathlib import Path

import pymupdf
import pytest

from redact_statement import (
    find_words_to_redact,
    is_card,
    is_transaction_line,
    leftover_check,
    load_private_values,
    main,
    normalize,
    redact,
)

ROOT = Path(__file__).parent
SAMPLE_PDF = ROOT / "samples" / "sample_statement.pdf"
EXAMPLE_PRIVATE = ROOT / "private_data.example.txt"


def make_words(*texts):
    """Fake PyMuPDF words for one line: (x0, y0, x1, y1, text, block, line, word)."""
    return [(0, 0, 0, 0, t, 0, 0, i) for i, t in enumerate(texts)]


# --- Helpers -----------------------------------------------------------------

def test_normalize_ignores_case_accents_spaces_and_punctuation():
    assert normalize("Rua São João, 100") == "ruasaojoao100"


def test_is_card():
    assert is_card("1234.XXXX.XXXX.0000")
    assert is_card("1234 XXXX XXXX 123")  # Amex style, 15 chars
    assert not is_card("123456789012")  # too short


def test_is_transaction_line():
    assert is_transaction_line("07/10 PadariaSolNasceCURI 84,90")
    assert not is_transaction_line("Cartao 1234.XXXX.XXXX.0000")


def test_load_private_values_skips_comments_and_normalizes():
    values = load_private_values(EXAMPLE_PRIVATE)
    assert "johnmeyerexample" in values
    assert "80000000" in values
    assert not any(v.startswith("#") for v in values)


def test_load_private_values_missing_file(tmp_path):
    assert load_private_values(tmp_path / "missing.txt") == []


# --- Matching on one line ----------------------------------------------------

def test_private_value_split_across_words():
    hits = find_words_to_redact(make_words("JOHN", "MEYER", "EXAMPLE"), ["johnmeyerexample"])
    assert set(hits) == {0, 1, 2}


def test_private_value_glued_to_other_text():
    hits = find_words_to_redact(make_words("JohnMeyerExample", "CURITIBA"), ["johnmeyerexample"])
    assert set(hits) == {0}


def test_short_private_value_matches_whole_word_only():
    hits = find_words_to_redact(make_words("ANA", "BANANA"), ["ana"])
    assert set(hits) == {0}


def test_generic_card_number():
    hits = find_words_to_redact(make_words("Cartao", "1234.XXXX.XXXX.0000"), [])
    assert hits == {1: "CARD"}


def test_generic_amex_card_in_separate_words():
    hits = find_words_to_redact(make_words("Cartao", "1234", "XXXX", "XXXX", "123"), [])
    assert set(hits) == {1, 2, 3, 4}


def test_generic_cpf_and_cep():
    assert 1 in find_words_to_redact(make_words("CPF", "123.456.789-00"), [])
    assert 0 in find_words_to_redact(make_words("80000-000", "CURITIBA"), [])


def test_long_code_redacted_outside_transaction_lines():
    hits = find_words_to_redact(make_words("Conta", "123456789012"), [])
    assert hits == {1: "ID"}


def test_long_code_kept_on_transaction_line():
    hits = find_words_to_redact(make_words("07/10", "LOJA", "123456789012", "10,00"), [])
    assert 2 not in hits


# --- Full PDF ----------------------------------------------------------------

@pytest.fixture(scope="module")
def redacted_pdf(tmp_path_factory):
    private_values = load_private_values(EXAMPLE_PRIVATE)
    output = tmp_path_factory.mktemp("out") / "statement_redacted.pdf"
    doc = pymupdf.open(SAMPLE_PDF)
    counts = redact(doc, private_values)
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    return output, counts, private_values


def read_text(path):
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def test_personal_data_removed(redacted_pdf):
    output, counts, _ = redacted_pdf
    text = read_text(output)
    for secret in ["JOHN MEYER", "1234.XXXX", "RUA DAS FLORES", "80000-000", "BAIRRO EXEMPLO"]:
        assert secret not in text
    assert counts.get("PRIVATE", 0) > 0


def test_transactions_kept(redacted_pdf):
    text = read_text(redacted_pdf[0])
    for kept in ["hujklz", "84,90", "STREAMCO", "Lancamentos no cartao", "673,31"]:
        assert kept in text


def test_leftover_check_ok(redacted_pdf):
    output, _, private_values = redacted_pdf
    assert leftover_check(output, private_values) == 0


def make_pdf(path, lines):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 20 * i), line)
    doc.save(path)
    doc.close()


@pytest.mark.parametrize(
    "line",
    [
        "CEP 80000-000",
        "Contato: john.meyer@example.com",
        "Protocolo 123456789012",
    ],
)
def test_leftover_check_finds_generic_patterns(tmp_path, line):
    pdf = tmp_path / "leak.pdf"
    make_pdf(pdf, [line])
    assert leftover_check(pdf, []) == 1


def test_leftover_check_short_private_value_whole_word_only(tmp_path):
    pdf = tmp_path / "leak.pdf"
    make_pdf(pdf, ["Titular ANA", "BANANA"])
    assert leftover_check(pdf, ["ana"]) == 1
    make_pdf(pdf, ["BANANA"])
    assert leftover_check(pdf, ["ana"]) == 0


def test_leftover_check_keeps_codes_on_transaction_lines(tmp_path):
    pdf = tmp_path / "ok.pdf"
    make_pdf(pdf, ["07/10 LOJA 123456789012 10,00"])
    assert leftover_check(pdf, []) == 0


def test_leftover_check_closes_the_document(tmp_path, monkeypatch):
    pdf = tmp_path / "ok.pdf"
    make_pdf(pdf, ["Nothing private here"])
    opened = []
    real_open = pymupdf.open
    monkeypatch.setattr(pymupdf, "open", lambda *a: opened.append(real_open(*a)) or opened[-1])
    leftover_check(pdf, [])
    assert opened and all(doc.is_closed for doc in opened)


def run_main(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["redact_statement.py", *map(str, args)])
    main()


def test_main_refuses_output_equal_to_input(tmp_path, monkeypatch):
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(SAMPLE_PDF.read_bytes())
    with pytest.raises(SystemExit, match="different file"):
        run_main(monkeypatch, pdf, "-o", pdf, "--private", EXAMPLE_PRIVATE)
    assert pdf.read_bytes() == SAMPLE_PDF.read_bytes()


def test_main_warns_before_overwriting(tmp_path, monkeypatch, capsys):
    output = tmp_path / "redacted.pdf"
    output.write_bytes(b"old")
    run_main(monkeypatch, SAMPLE_PDF, "-o", output, "--private", EXAMPLE_PRIVATE)
    assert "already exists and will be overwritten" in capsys.readouterr().out
    assert output.read_bytes() != b"old"


def test_metadata_removed(redacted_pdf):
    with pymupdf.open(redacted_pdf[0]) as doc:
        assert not doc.metadata.get("author")
        assert not doc.metadata.get("title")