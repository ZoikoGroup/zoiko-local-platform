from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env")

    database_url: str = "postgresql+psycopg2://zoiko:zoiko@localhost:5433/zoiko_local"
    jwt_secret_key: str = "change-me-in-real-env"
    environment: str = "development"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

settings = Settings()
