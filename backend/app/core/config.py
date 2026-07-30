from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://zoiko:zoiko@localhost:5432/zoiko_local"
    jwt_secret_key: str = "change-me-in-real-env"
    environment: str = "development"

    class Config:
        env_file = "../.env"


settings = Settings()
