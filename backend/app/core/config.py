from pydantic_settings import BaseSettings, SettingsConfigDict

# The literal default below - never a real secret to guard against, since it's
# only ever compared against itself. Exists so app/main.py can refuse to boot
# with it outside development, instead of silently signing real JWTs with a
# value anyone can read in this repo's own .env.example.
PLACEHOLDER_JWT_SECRET_KEY = "change-me-in-real-env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env")

    database_url: str = "postgresql+psycopg2://zoiko:zoiko@localhost:5433/zoiko_local"
    # loadtest.py's actual finding: Postgres itself was NOT the bottleneck
    # under 50 concurrent users (pg_stat_activity showed 1-2 active queries,
    # <2% container CPU, mostly idle connections) - so this is deliberately
    # a modest bump over SQLAlchemy's bare default (pool_size=5,
    # max_overflow=10), not an aggressive one. The real ceiling found was
    # request concurrency in a single uvicorn process (see Dockerfile's
    # WEB_CONCURRENCY). Multiplied by however many worker processes run per
    # machine, so keep this conservative - it's per-process, not per-machine.
    db_pool_size: int = 10
    db_max_overflow: int = 10
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
    # The customer-facing web app's origin - used to build links inside
    # emails (password reset, etc.) that must point at the frontend, not
    # this API. Defaults to the local Next.js dev server.
    frontend_base_url: str = "http://localhost:3000"

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

    # Cohere (integrations/embeddings) - real semantic search over AI
    # summaries. Free trial key, rate-limited, no billing account needed
    # (unlike enabling Google's Generative Language API via Cloud Console,
    # which requires linking billing to the project even for free usage).
    cohere_api_key: str = ""

    stripe_secret_key: str = ""
    stripe_identity_webhook_secret: str = ""

    # Stripe Payments (integrations/billing/stripe_checkout.py) - separate
    # key/account scope from stripe_secret_key above (Stripe Identity).
    # Real Stripe Checkout for number purchases, test mode only for now -
    # see docs/pci-scope-assessment.md for why hosted Checkout (never a
    # custom card form) is the locked architectural decision here.
    stripe_payments_secret_key: str = ""
    stripe_payments_webhook_secret: str = ""

    # Where Stripe Checkout redirects the customer back to after payment
    # (success or cancel) - the deployed Next.js frontend origin, or
    # localhost:3000 in dev. Distinct from public_base_url above, which is
    # this API's own address for provider webhooks to call back into.
    frontend_base_url: str = "http://localhost:3000"

    # ZoikoNex (integrations/billing) - shared-secret HMAC for the inbound
    # payment-event webhook. Empty until a real ZoikoNex connection issues
    # one; see app.integrations.billing.zoikonex's docstring.
    zoikonex_webhook_secret: str = ""

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

    # Multi-provider failover (integrations/_shared/circuit_breaker.py) - per
    # category, a circuit breaker wraps the primary vendor call and falls
    # back to a secondary provider if one is enabled. Every secondary below
    # is a real vendor client (not a mock) that activates once its
    # credentials are set; each flag still defaults off because no real
    # second-vendor account exists yet for any category - flipping a flag on
    # without the matching credentials below fails loudly via the same
    # "not configured" error as an unconfigured primary, rather than
    # silently no-opping.
    telecom_failover_enabled: bool = False
    video_failover_enabled: bool = False
    llm_failover_enabled: bool = False
    transcription_failover_enabled: bool = False
    kyc_failover_enabled: bool = False
    storage_failover_enabled: bool = False
    email_failover_enabled: bool = False
    webpush_failover_enabled: bool = False

    # Secondary telecom provider (integrations/telecom/_secondary_stub.py) -
    # Vonage SMS/Voice/Number Insight REST API. Voice calls are placed via a
    # JWT-signed request; vonage_private_key is the PEM contents copied from
    # a Vonage Application's generated private key file.
    vonage_api_key: str = ""
    vonage_api_secret: str = ""
    vonage_application_id: str = ""
    vonage_private_key: str = ""
    vonage_sms_from: str = ""

    # Secondary video provider (integrations/video/_secondary_stub.py) -
    # Daily.co REST API.
    daily_api_key: str = ""

    # Secondary LLM provider (integrations/llm/_secondary_stub.py) - OpenAI
    # chat completions API.
    openai_api_key: str = ""

    # Secondary transcription provider (integrations/transcription/_secondary_stub.py) - Deepgram.
    deepgram_api_key: str = ""

    # Secondary KYC provider (integrations/kyc/_secondary_stub.py) - Sumsub,
    # HMAC-signed REST API (app token + secret key, not a bearer token).
    sumsub_app_token: str = ""
    sumsub_secret_key: str = ""
    sumsub_level_name: str = "basic-kyc-level"

    # Secondary object storage (integrations/storage/_secondary_stub.py) -
    # any second S3-compatible bucket (Backblaze B2, a different-region R2
    # bucket, ...), reusing the same boto3 client as the primary - only the
    # endpoint/credentials/bucket differ.
    storage_secondary_access_key_id: str = ""
    storage_secondary_secret_access_key: str = ""
    storage_secondary_bucket: str = ""
    storage_secondary_endpoint: str = ""
    storage_secondary_region: str = "auto"

    # Secondary email provider (integrations/notifications/_email_secondary_stub.py) - SendGrid.
    sendgrid_api_key: str = ""

    # Secondary web push relay (integrations/notifications/_webpush_secondary_stub.py) -
    # OneSignal, used as an alternate delivery path when raw Web Push
    # (pywebpush straight to the browser's push service) is unavailable.
    onesignal_app_id: str = ""
    onesignal_api_key: str = ""

    # Observability (core/telemetry.py, core/logging.py) - OpenTelemetry
    # tracing/metrics + structured JSON logging. Off by default so pytest and
    # local dev are unaffected unless explicitly opted in, same "blank/false
    # disables" pattern as Kafka above. A blank otel_exporter_otlp_endpoint
    # with otel_enabled=true prints spans/metrics to the console instead of
    # requiring a real OTel collector to exist.
    otel_enabled: bool = False
    otel_service_name: str = "zoiko-backend"
    otel_exporter_otlp_endpoint: str = ""
    log_level: str = "INFO"
    # Periodic health_check() sweep across every integration
    # (ops/synthetic.py), logged + emitted as an OTel gauge. 0 disables it.
    synthetic_check_interval_seconds: int = 0

    # HubSpot (integrations/crm) - real OAuth app credentials, from a
    # HubSpot developer account's app settings. Empty until one is created;
    # see app.integrations.crm.hubspot's docstring. hubspot_redirect_uri
    # must exactly match the "Redirect URL" configured in that HubSpot app
    # (typically {public_base_url}/crm/hubspot/callback).
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    hubspot_redirect_uri: str = ""

    # Symmetric key (Fernet, base64-encoded - generate with
    # cryptography.fernet.Fernet.generate_key()) for encrypting OAuth
    # tokens at rest, e.g. CrmConnection's HubSpot/Salesforce tokens - see
    # app.core.crypto. Empty in dev is fine for the mock-only providers;
    # required before any real OAuth connection can be stored.
    token_encryption_key: str = ""

    # Salesforce (integrations/crm) - real OAuth "Connected App" credentials
    # from a Salesforce org (a free Developer Edition org works). Empty
    # until one is created; see app.integrations.crm.salesforce's
    # docstring. salesforce_redirect_uri must exactly match the Connected
    # App's configured Callback URL (typically
    # {public_base_url}/crm/salesforce/callback). salesforce_login_base_url
    # is the login domain to authenticate against - login.salesforce.com
    # for production/Developer Edition orgs, test.salesforce.com for
    # sandboxes.
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    salesforce_redirect_uri: str = ""
    salesforce_login_base_url: str = "https://login.salesforce.com"

    # Pipedrive (integrations/crm) - real OAuth app credentials, from a
    # Pipedrive Developer Hub app. Empty until one is created; see
    # app.integrations.crm.pipedrive's docstring. pipedrive_redirect_uri
    # must exactly match that app's configured Callback URL (typically
    # {public_base_url}/crm/pipedrive/callback).
    pipedrive_client_id: str = ""
    pipedrive_client_secret: str = ""
    pipedrive_redirect_uri: str = ""

settings = Settings()
