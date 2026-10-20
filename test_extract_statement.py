from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from extract_statement import (
    format_brl,
    parse_amount,
    parse_statement,
    read_pdf_text,
    split_category_city,
    validate,
)

SAMPLE = Path(__file__).parent / "samples" / "sample_statement.pdf"


@pytest.fixture(scope="module")
def result():
    return parse_statement(read_pdf_text(SAMPLE))


def test_parse_amount_brazilian_format():
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("-22,40") == Decimal("-22.40")


def test_format_brl():
    assert format_brl(Decimal("1234.5")) == "1.234,50"


def test_split_category_city():
    assert split_category_city("eletrodomestico SAO PAULO") == ("eletrodomestico", "SAO PAULO")


def test_issue_date(result):
    assert result["issue_date"] == date(2026, 10, 14)


def test_transaction_count(result):
    # 9 domestic + 1 international + 1 IOF fee
    assert len(result["transactions"]) == 11


def test_refund_is_negative(result):
    refund = next(t for t in result["transactions"] if t["Type"] == "refund")
    assert refund["Amount (BRL)"] == Decimal("-22.40")
    assert refund["Category"] == "farmacia"


def test_installment_detected(result):
    item = next(t for t in result["transactions"] if t["Installment"])
    assert item["Installment"] == "02/10"


def test_international_has_usd(result):
    item = next(t for t in result["transactions"] if t["Type"] == "international")
    assert item["Amount (USD)"] == Decimal("12.00")
    assert item["Amount (BRL)"] == Decimal("63.36")


def test_payments_are_not_transactions(result):
    assert not any("PAGAMENTO" in t["Description"] for t in result["transactions"])


def test_totals_match_statement(result):
    checks = validate(result["transactions"], result["totals"])
    assert all(c["ok"] is True for c in checks)
    assert checks[-1]["calculated"] == Decimal("673.31")


def test_validation_detects_mismatch(result):
    wrong_totals = dict(result["totals"], grand=Decimal("700.00"))
    checks = validate(result["transactions"], wrong_totals)
    assert checks[-1]["ok"] is False


ALT_LAYOUT_TEXT = """Banco Exemplo
Emissao: 14/10/2026
Lancamentos: compras e saques
07/10 PadariaSolNasceCURI 84,90
supermercado CURITIBA
Lancamentos no cartao 84,90
Lancamentos internacionais
DATA ESTABELECIMENTO US$ R$
09/10 STREAMCO* PLANO MENSAL 63,36
63,36 USD 12,00
Dolar de Conversao R$ 5,28
Total transacoes inter. em R$ 63,36
Repasse de IOF em R$ 2,22
Total lancamentos inter. em R$ 65,58
* Total dos lancamentos atuais 150,48
"""


def test_international_on_two_lines_and_noisy_total():
    result = parse_statement(ALT_LAYOUT_TEXT)
    international = [t for t in result["transactions"] if t["Type"] == "international"]
    assert len(international) == 1
    assert international[0]["Amount (BRL)"] == Decimal("63.36")
    assert international[0]["Amount (USD)"] == Decimal("12.00")
    assert result["totals"]["grand"] == Decimal("150.48")
    checks = validate(result["transactions"], result["totals"])
    assert all(c["ok"] is True for c in checks)
