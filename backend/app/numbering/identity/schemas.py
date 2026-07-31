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
