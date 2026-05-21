"""String enums shared across the data model.

We store these as plain strings in the database (sa.String columns) and validate
at the Pydantic layer. This avoids the migration pain of Postgres ENUM types.
"""

from enum import StrEnum


class OrgPlan(StrEnum):
    TRIAL = "trial"
    PAID = "paid"


class UserRole(StrEnum):
    FOUNDER = "founder"
    ACCOUNTANT = "accountant"
    OPS = "ops"
    VIEWER = "viewer"


class DocumentSource(StrEnum):
    UPLOAD = "upload"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    BANK_API = "bank_api"


class FileType(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    CSV = "csv"
    XLSX = "xlsx"


class DocumentType(StrEnum):
    BANK_STATEMENT = "bank_statement"
    SALES_INVOICE = "sales_invoice"
    PURCHASE_INVOICE = "purchase_invoice"
    RECEIPT = "receipt"
    UNKNOWN = "unknown"


class DocumentStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    UNDERSTOOD = "understood"
    INDEXED = "indexed"
    ERROR = "error"


class TxnDirection(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


class InvoiceType(StrEnum):
    SALES = "sales"
    PURCHASE = "purchase"


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"


class PaymentMode(StrEnum):
    CASH = "cash"
    CARD = "card"
    UPI = "upi"
    BANK_TRANSFER = "bank_transfer"
    UNKNOWN = "unknown"


class InsightSeverity(StrEnum):
    INFO = "info"
    ATTENTION = "attention"
    URGENT = "urgent"
