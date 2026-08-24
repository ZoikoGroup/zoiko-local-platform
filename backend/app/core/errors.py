"""Shared base for customer-facing commercial/entitlement errors that carry
a machine-readable code the frontend can branch on, instead of just a
free-text message - see docs/Zoiko_Local_Plan_Entitlement_Subscription_
Lifecycle_Engineering.docx. Caught by a single global handler
(app.main:entitlement_error_handler) rather than per-route try/except."""


class EntitlementError(Exception):
    code: str = "ENTITLEMENT_ERROR"
    status_code: int = 403

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
