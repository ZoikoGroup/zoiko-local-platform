from pydantic_settings import BaseSettings, SettingsConfigDict

# The literal default below - never a real secret to guard against, since it's
# only ever compared against itself. Exists so app/main.py can refuse to boot
# with it outside development, instead of silently signing real JWTs with a
# value anyone can read in this repo's own .env.example.
PLACEHOLDER_JWT_SECRET_KEY = "change-me-in-real-env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env")

    database_url: str = "postgresql+psycopg2://zoiko:zoiko@localhost:5433/zoiko_local"
    jwt_secret_key: str = PLACEHOLDER_JWT_SECRET_KEY
    environment: str = "development"
    # Comma-separated allowed CORS origins - the deployed frontend's real
    # origin(s) in production, localhost for dev. A literal "*" is rejected in
    # main.py's startup check when allow_credentials=True is also set (the
    # browser forbids that combination anyway - wildcard + credentials is
    # never valid CORS, so failing fast beats a confusing browser-side error).
    allowed_origins: str = "http://localhost:3000"
    google_client_id: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # The number system SMS notifications (not customer calls) are sent
    # from - a Zoiko-owned number, distinct from any customer's purchased
    # number. Blank until a real number is provisioned for this purpose.
    twilio_trial_number: str = ""
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

    stripe_secret_key: str = ""
    stripe_identity_webhook_secret: str = ""

    # Resend (integrations/notifications) - real transactional email sending.
    # email_from_address must be on a domain verified in Resend once one is
    # set up; Resend's shared onboarding@resend.dev address works
    # immediately with no domain verification, for testing before that.
    resend_api_key: str = ""
    email_from_address: str = "onboarding@resend.dev"

    # Web Push (integrations/notifications/webpush.py) - browser push
    # notifications, since no native iOS/Android app exists. Self-generated
    # VAPID keypair (not a third-party vendor credential) identifying this
    # server to browser push services; vapid_claim_email is the contact
    # address push services may use to reach the sender if a key is abused.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_claim_email: str = "admin@zoikogroup.com"

    # Event bus (integrations/eventbus/kafka.py, app/events/) - single-node
    # Kafka in KRaft mode via docker-compose.yml. Approved exception to the
    # original "no Kafka" Phase 1 scope - see CLAUDE.md. Blank disables
    # publishing (falls back to logging), same pattern as the other providers.
    kafka_bootstrap_servers: str = ""

settings = Settings()
