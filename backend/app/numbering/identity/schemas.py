from pydantic import BaseModel, EmailStr, ConfigDict, Field

# Security-review fix: signup previously accepted any string, including a
# 1-character password. min_length=8 matches current NIST 800-63B guidance
# (length over composition rules); max_length=128 caps request size and
# avoids bcrypt's well-known 72-byte truncation footgun on very long input.
_PasswordField = Field(min_length=8, max_length=128)


class SignupRequest(BaseModel):
    account_name: str
    account_type: str  # "individual" or "business"
    email: EmailStr
    password: str = _PasswordField


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str  # the ID token from Google's Sign-In button


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    mfa_required: bool = False
    access_token: str | None = None
    token_type: str = "bearer"
    mfa_token: str | None = None  # only set when mfa_required=True


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    code: str


class MfaLoginRequest(BaseModel):
    mfa_token: str
    code: str


class SetPhoneNumberRequest(BaseModel):
    phone_number: str | None  # None clears it - opts back out of SMS notifications


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
    account_id: str
    mfa_enabled: bool
    phone_number: str | None


class TeamMemberAdd(BaseModel):
    email: EmailStr
    password: str = _PasswordField
    role: str  # "admin", "member", or "viewer" - never "owner", there is exactly one per account
