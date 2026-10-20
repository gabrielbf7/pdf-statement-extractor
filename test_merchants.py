from pathlib import Path

import pytest

from extract_statement import build_dataframe, parse_statement, read_pdf_text, write_excel
from merchants import (
    clean_merchant,
    code_like_reason,
    merchant_summary,
    unknown_merchants,
)

SAMPLE = Path(__file__).parent / "samples" / "sample_statement.pdf"


@pytest.fixture(scope="module")
def df():
    result = parse_statement(read_pdf_text(SAMPLE))
    return build_dataframe(result["transactions"])


def test_city_glued_to_name_is_removed():
    assert clean_merchant("PadariaSolNasceCURI", "CURITIBA") == "PadariaSolNasce"
    assert clean_merchant("CASA DO LAGO RESTLTDCU", "CURITIBA") == "CASA DO LAGO RESTLTD"
    assert clean_merchant("TransExp *RecargaSAO", "SAO PAULO") == "TransExp Recarga"
    assert clean_merchant("hujklz CURITIBA", "CURITIBA") == "hujklz"


def test_name_without_city_is_kept():
    assert clean_merchant("LOJAXPTO *MLX COMERCIO", "BARUERI") == "LOJAXPTO MLX COMERCIO"
    assert clean_merchant("STREAMCO* PLANO MENSAL", "") == "STREAMCO PLANO MENSAL"


def test_installment_and_refund_prefix_are_removed():
    assert clean_merchant("ELETROMODELO 02/10", "SAO PAULO") == "ELETROMODELO"
    assert clean_merchant("ESTORNO FARMACIAMODELO", "CURITIBA") == "FARMACIAMODELO"


def test_same_merchant_with_different_truncation_is_grouped(df):
    rows = [
        {**df.iloc[0].to_dict(), "Description": "PadariaSolNasceCURI", "City": "CURITIBA", "Amount (BRL)": 10.0},
        {**df.iloc[0].to_dict(), "Description": "PadariaSolNasceCU", "City": "CURITIBA", "Amount (BRL)": 5.5},
    ]
    import pandas as pd

    summary = merchant_summary(pd.DataFrame(rows))
    assert len(summary) == 1
    assert summary.iloc[0]["Transactions"] == 2
    assert summary.iloc[0]["Total (BRL)"] == 15.5


def test_merchant_totals_match_statement_total(df):
    summary = merchant_summary(df)
    assert round(summary["Total (BRL)"].sum(), 2) == 673.31
    assert summary["Transactions"].sum() == len(df)


def test_code_like_names():
    assert code_like_reason("hujklz")
    assert code_like_reason("PG A8F2KD")
    assert not code_like_reason("PadariaSolNasce")
    assert not code_like_reason("CASA DO LAGO RESTLTD")  # truncated word in a longer name
    assert not code_like_reason("MERCADO ESTRELA")


def test_unknown_sheet_lists_code_and_intermediary_names(df):
    unknown = unknown_merchants(df)
    descriptions = set(unknown["Description"])
    assert "hujklz CURITIBA" in descriptions
    assert "TransExp *RecargaSAO" in descriptions
    assert "PadariaSolNasceCURI" not in descriptions
    assert "CASA DO LAGO RESTLTDCU" not in descriptions
    assert not any("IOF" in d for d in descriptions)


def test_unknown_hints_use_only_statement_data(df):
    unknown = unknown_merchants(df)
    row = unknown[unknown["Description"] == "hujklz CURITIBA"].iloc[0]
    assert "servicos" in row["Hints"]
    assert "CURITIBA" in row["Hints"]


def test_excel_has_three_sheets(df, tmp_path):
    import openpyxl

    out = tmp_path / "result.xlsx"
    write_excel(df, out)
    assert openpyxl.load_workbook(out).sheetnames == ["Transactions", "By merchant", "Unknown"]
