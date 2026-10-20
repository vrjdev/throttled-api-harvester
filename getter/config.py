import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str
    auth_header: str
    param_name: str
    total: int
    step: int
    worker_index: int
    worker_count: int
    qps: float
    bucket: str
    prefix: str
    batch_size: int

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        cfg = cls(
            api_url=env["API_URL"],
            api_key=env.get("API_KEY", ""),
            auth_header=env.get("API_AUTH_HEADER", "Authorization"),
            param_name=env.get("API_PARAM", "offset"),
            total=int(env.get("TOTAL_REQUESTS", "10000000")),
            step=int(env.get("STEP", "100")),
            worker_index=int(env["WORKER_INDEX"]),
            worker_count=int(env.get("WORKER_COUNT", "2")),
            qps=float(env.get("QPS", "0.95")),
            bucket=env["BUCKET"],
            prefix=env.get("PREFIX", "results"),
            batch_size=int(env.get("BATCH_SIZE", "1000")),
        )
        if not 0 <= cfg.worker_index < cfg.worker_count:
            raise ValueError("WORKER_INDEX must be in [0, WORKER_COUNT)")
        if cfg.qps <= 0:
            raise ValueError("QPS must be positive")
        return cfg
