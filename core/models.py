from dataclasses import dataclass


@dataclass
class InvoiceData:
    invoice_code: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    amount_without_tax: str = ""
    tax_amount: str = ""
    total_amount: str = ""
    buyer_name: str = ""
    buyer_tax_id: str = ""
    seller_name: str = ""
    seller_tax_id: str = ""
    remark: str = ""
    raw_text: str = ""


@dataclass
class CompareRow:
    field_name: str
    user_value: str
    official_value: str
    is_match: bool
    message: str
