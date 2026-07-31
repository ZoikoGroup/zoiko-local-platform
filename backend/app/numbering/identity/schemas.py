from pydantic import BaseModel, EmailStr, ConfigDict


class SignupRequest(BaseModel):
    account_name: str
    account_type: str  # "individual" or "business"
    email: EmailStr
    password: str


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


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
    account_id: str


class TeamMemberAdd(BaseModel):
    email: EmailStr
    password: str
    role: str  # "admin" or "member" - never "owner", there is exactly one per account
