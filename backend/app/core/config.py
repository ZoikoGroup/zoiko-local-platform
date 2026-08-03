from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env")

    database_url: str = "postgresql+psycopg2://zoiko:zoiko@localhost:5433/zoiko_local"
    jwt_secret_key: str = "change-me-in-real-env"
    environment: str = "development"
    google_client_id: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # public HTTPS URL this API is reachable at (e.g. an ngrok tunnel in dev,
    # or the real deployed origin) — used to register Twilio webhook URLs
    # (call status callbacks) that can't be constructed from a request object
    public_base_url: str = ""

    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # S3-compatible storage for video call recordings (LiveKit Egress has no
    # free built-in storage - every recording request must specify a real
    # bucket). Works with real AWS S3 (leave s3_endpoint empty) or any
    # S3-compatible provider like Cloudflare R2 (set s3_endpoint + s3_region).
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = "auto"

    groq_api_key: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "no-reply@zoikolocal.test"

settings = Settings()
