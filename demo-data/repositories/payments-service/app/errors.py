"""Domain errors. Services raise these; app/api/errors.py maps them to HTTP responses."""


class DomainError(Exception):
    code = "domain_error"


class NotFound(DomainError):
    code = "not_found"


class Conflict(DomainError):
    code = "conflict"


class PaymentDeclined(DomainError):
    code = "payment_declined"


class RefundExceedsCapture(DomainError):
    code = "refund_exceeds_capture"


class ProviderUnavailable(DomainError):
    code = "provider_unavailable"


class InvalidCurrency(DomainError):
    code = "invalid_currency"
