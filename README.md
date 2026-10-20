# PDF Statement Extractor

Python command-line tool that reads a Brazilian credit card statement (PDF with selectable text) and exports the transactions to a clean Excel file, with totals per merchant and a list of descriptions that are hard to recognize.

The problem it solves: statement descriptions are truncated, glued to the city, or are just a code (e.g. `hujklz`), so it is hard to tell where the money went.

## What you get

The Excel file has three sheets:

| Sheet | Content |
|---|---|
| **Transactions** | Date, description, category, city, installment, amount in BRL, amount in USD, type (purchase / refund / international / fee) |
| **By merchant** | One row per merchant (truncated names and glued cities are cleaned and grouped): number of transactions, total, first and last date |
| **Unknown** | Descriptions that look like a code or go through a payment intermediary (`*`), with hints from the statement (category, city, repeats) and an empty "Your note" column |

The terminal also prints a **validation**: the sum of the extracted transactions is compared with the totals printed on the statement (domestic, international, grand total). If anything does not match, the program warns you and exits with an error code.

## Setup

Python 3.10+ is required.

```bash
python -m venv venv
venv\Scripts\activate        # Windows (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

## Usage

```bash
python extract_statement.py samples/sample_statement.pdf
python extract_statement.py statement.pdf -o output/result.xlsx
```

By default the Excel file is saved in `output/`.

**Password-protected PDF:** the program asks for the password in the terminal (hidden input). For automation, set the `STATEMENT_PDF_PASSWORD` environment variable. The password is never accepted as a command-line flag.

Run the tests with `pytest`.

## Example

`samples/sample_statement.pdf` is a fictional statement ("Banco Exemplo"), and `samples/sample_output.xlsx` is the Excel file generated from it. In the sample, `hujklz` shows up in the **Unknown** sheet with the hint `Statement category: servicos | City: CURITIBA`.

## Limitations

- Works with PDFs that have selectable text. Scanned PDFs (images) are not supported (no OCR).
- The parser follows the layout of one bank's statement (dates as DD/MM, amounts as 1.234,56, category and city on the second line of each entry). Other banks may need adjustments.
- The **Unknown** sheet is a heuristic based only on the statement itself. It does not look anything up online, so it points out suspicious descriptions but does not identify them.
- Real statements contain personal data: never commit them. Only the fictional sample is in this repository.

## Author

Gabriel Fantin
