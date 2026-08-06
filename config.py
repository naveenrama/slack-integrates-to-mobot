from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str

    oauth_callback_url: str
    cimd_metadata_url: str

    # Storage backend: "sqlite" (local) or "dynamodb" (production)
    token_store_backend: str = "sqlite"

    # SQLite (local dev)
    sqlite_db_path: str = "./mobot.db"
    token_encryption_key: str = ""

    # DynamoDB + KMS (production)
    dynamodb_table: str = "mobot-connections"
    kms_key_arn: str = ""
    aws_region: str = "us-east-1"

    poll_interval_seconds: float = 1.0
    poll_timeout_seconds: float = 120.0

    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
