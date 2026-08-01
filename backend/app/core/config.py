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

    groq_api_key: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "no-reply@zoikolocal.test"

settings = Settings()
