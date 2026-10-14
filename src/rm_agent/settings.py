
from pydantic_settings import BaseSettings, SettingsConfigDict


class NeonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neon_database_url: str


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kafka_bootstrap_servers: str
    kafka_security_protocol: str = "SASL_SSL"
    kafka_sasl_mechanism: str = "SCRAM-SHA-256"
    kafka_sasl_username: str = "avnadmin"
    kafka_sasl_password: str
    kafka_ssl_ca_location: str = "./ca.pem"
    kafka_consumer_group: str = "rm-agent-consumer-group"


class CDCSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cdc_poll_interval_seconds: int = 10
    cdc_checkpoint_path: str = "./data/cdc_checkpoints.json"


class UpstashSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    upstash_redis_rest_url: str
    upstash_redis_rest_token: str


class AtlasSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    atlas_uri: str
    atlas_db: str = "risk_agent"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "groq"
    groq_api_key: str
    llm_model_name: str = "llama-3.3-70b-versatile"


class PhoenixSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    phoenix_enabled: bool = False
    phoenix_project_name: str = "rm-agent"
    phoenix_endpoint: str = "http://127.0.0.1:6006/v1/traces"
    phoenix_api_key: str = ""


class RiskScoringSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    risk_score_threshold: float = 0.65
    deposit_decline_weight: float = 0.35
    wire_decline_weight: float = 0.25
    days_since_contact_weight: float = 0.20
    cd_maturity_proximity_weight: float = 0.20


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gateway_jwt_secret: str
    gateway_token_expiry_minutes: int = 480
