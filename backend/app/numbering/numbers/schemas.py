from datetime import datetime, time, timezone

from pydantic import BaseModel, ConfigDict, model_validator


class RoutingConfigRequest(BaseModel):
    forwarding_number: str | None = None
    business_hours_start: time | None = None
    business_hours_end: time | None = None
    business_hours_timezone: str = "UTC"
    ai_receptionist_enabled: bool = False
    escalation_user_id: str | None = None
    whatsapp_enabled: bool = False
    sms_enabled: bool = False


class SuspendNumberRequest(BaseModel):
    reason: str | None = None


class SetRingGroupRequest(BaseModel):
    destinations: list[str]


class RingGroupDestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    destination_number: str
    ring_order: int


class SetIVRMenuRequest(BaseModel):
    greeting: str
    options: dict[str, str]


class IVROptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    digit: str
    destination_number: str


class IVRMenuResponse(BaseModel):
    greeting: str | None
    options: list[IVROptionResponse]


class NumberSearchResult(BaseModel):
    phone_number: str
    locality: str | None = None
    region: str | None = None
    capabilities: dict | None = None


class ReserveNumberRequest(BaseModel):
    e164: str
    country: str
    number_type: str = "local"


class PurchaseNumberRequest(BaseModel):
    e164: str


class CheckoutSessionResponse(BaseModel):
    id: str
    url: str


class AssignNumberRequest(BaseModel):
    user_id: str | None = None  # None unassigns the number


class PhoneNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    e164: str
    country: str
    number_type: str
    status: str
    account_id: str
    assigned_user_id: str | None
    reserved_until: datetime | None
    forwarding_number: str | None
    business_hours_start: time | None
    business_hours_end: time | None
    business_hours_timezone: str
    ai_receptionist_enabled: bool
    escalation_user_id: str | None
    call_flow_id: str | None
    whatsapp_enabled: bool
    sms_enabled: bool
    next_renewal_at: datetime | None

    @model_validator(mode="after")
    def _show_expired_reservations_honestly(self):
        """A reservation is only ever supposed to hold for
        RESERVATION_TTL_MINUTES (see app.numbering.numbers.service) - past
        that, purchase_number already rejects it ("Reservation expired -
        reserve it again"), but nothing ever told the CUSTOMER that: the
        row just sits at status="reserved" forever with no visual
        difference from a fresh, still-valid hold. Read-path only - this
        does not touch the database, so a number can still be re-reserved
        (by this account or another) the normal way; it only fixes what
        gets displayed."""
        if self.status == "reserved" and self.reserved_until is not None and self.reserved_until < datetime.now(timezone.utc):
            self.status = "expired"
        return self


class SupportedCountryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    emergency_calling_supported: bool
    activation_state: str
    activation_notes: str | None = None
    activation_changed_by: str | None = None
    activation_changed_at: datetime | None = None


class UpsertSupportedCountryRequest(BaseModel):
    code: str
    name: str
    sort_order: int = 0
    emergency_calling_supported: bool = False


class SetMarketActivationStateRequest(BaseModel):
    state: str
    notes: str | None = None


class NumberEligibilityRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    country: str
    number_type: str
    required_evidence: list
    is_active: bool
    emergency_calling_supported: bool
    recording_supported: bool
    allowed_calling_directions: str


class UpsertNumberEligibilityRuleRequest(BaseModel):
    country: str
    number_type: str
    required_evidence: list[str] = []
    is_active: bool = True
    emergency_calling_supported: bool = False
    recording_supported: bool = True
    allowed_calling_directions: str = "both"


class NumberEligibilityCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    phone_number_id: str
    account_id: str
    country: str
    number_type: str
    status: str
    evidence: list
    review_notes: str | None
    expires_at: datetime | None
    created_at: datetime
    resolved_at: datetime | None


class SubmitNumberEligibilityEvidenceRequest(BaseModel):
    evidence: list[dict]


class ResolveNumberEligibilityCaseRequest(BaseModel):
    notes: str | None = None
