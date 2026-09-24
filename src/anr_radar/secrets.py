"""Read API keys from the environment locally, or from a Databricks secret scope in jobs."""

import os

from anr_radar.config import SECRET_SCOPE


def get_secret(env_var: str, scope_key: str) -> str:
    try:
        from dotenv import load_dotenv  # dev dependency; absent (and unneeded) on Databricks

        load_dotenv()
    except ImportError:
        pass
    value = os.environ.get(env_var)
    if value:
        return value
    try:
        from databricks.sdk.runtime import dbutils  # only available on Databricks
    except ImportError as exc:
        raise RuntimeError(f"Set {env_var} (e.g. in .env) or run on Databricks") from exc
    return dbutils.secrets.get(scope=SECRET_SCOPE, key=scope_key)
