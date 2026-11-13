"""Every setting and every judgment number, in one place.

Page 07 lists them. A builder that needs a new number adds it here and adds a row to the settings table on 07 in
the same commit. Nothing else in `lab/` reads the environment.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # The master graph
    neo4j_uri: str = Field("", alias="NEO4J_URI")  # empty: the bundled Neo4j on LAB_NEO4J_BOLT_PORT
    neo4j_username: str = Field("neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field("subgraph-lab", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field("neo4j", alias="NEO4J_DATABASE")
    neo4j_query_uri: str | None = Field(None, alias="NEO4J_QUERY_URI")

    # Summaries
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    model: str = Field("claude-sonnet-5-5", alias="LAB_MODEL")

    # Judgment numbers (page 07)
    search_k: int = Field(50, alias="LAB_SEARCH_K")
    search_hops: int = Field(2, alias="LAB_SEARCH_HOPS")
    slice_cap: int = Field(200_000, alias="LAB_SLICE_CAP")
    push_batch: int = Field(5_000, alias="LAB_PUSH_BATCH")
    load_start_rows: int = Field(20_000, alias="LAB_LOAD_START_ROWS")
    summary_communities: int = Field(20, alias="LAB_SUMMARY_COMMUNITIES")
    summary_alerts: int = Field(50, alias="LAB_SUMMARY_ALERTS")
    summary_words: int = Field(120, alias="LAB_SUMMARY_WORDS")
    louvain_resolution: float = Field(1.0, alias="LAB_LOUVAIN_RESOLUTION")
    component_min: int = Field(30, alias="LAB_COMPONENT_MIN")
    weight_device: float = Field(3.0, alias="LAB_WEIGHT_DEVICE")
    weight_email: float = Field(2.0, alias="LAB_WEIGHT_EMAIL")
    weight_address: float = Field(1.5, alias="LAB_WEIGHT_ADDRESS")
    weight_payment: float = Field(1.0, alias="LAB_WEIGHT_PAYMENT")
    seed: int = Field(42, alias="LAB_SEED")
    delta_sample: int = Field(1_000, alias="LAB_DELTA_SAMPLE")
    embedding_dim: int = Field(64, alias="LAB_EMBEDDING_DIM")
    shared_max: int = Field(50, alias="LAB_SHARED_MAX")

    # Where things live
    data_dir: Path = Field(Path("data"), alias="LAB_DATA_DIR")
    relate_bin: str = Field("relate", alias="LAB_RELATE_BIN")
    factors_file: Path = Field(Path("factors/factors.yaml"), alias="LAB_FACTORS_FILE")
    policy_file: Path = Field(Path("factors/policy.yaml"), alias="LAB_POLICY_FILE")

    # Ports
    port: int = Field(8000, alias="LAB_PORT")
    relate_bolt_port: int = Field(7687, alias="LAB_RELATE_BOLT_PORT")
    relate_http_port: int = Field(7474, alias="LAB_RELATE_HTTP_PORT")
    relate_mcp_port: int = Field(7475, alias="LAB_RELATE_MCP_PORT")
    relate_host: str = Field("127.0.0.1", alias="LAB_RELATE_HOST")
    neo4j_bolt_port: int = Field(17687, alias="LAB_NEO4J_BOLT_PORT")
    neo4j_http_port: int = Field(17474, alias="LAB_NEO4J_HTTP_PORT")

    @model_validator(mode="after")
    def _bundled_default(self) -> Settings:
        if not self.neo4j_uri:
            self.neo4j_uri = f"bolt://localhost:{self.neo4j_bolt_port}"
        return self

    @property
    def query_uri(self) -> str:
        """The http(s) Query API address Relate needs (decision 25)."""
        if self.neo4j_query_uri:
            return self.neo4j_query_uri.rstrip("/")
        return derive_query_uri(self.neo4j_uri, {self.neo4j_bolt_port: self.neo4j_http_port})

    @property
    def plaintext(self) -> bool:
        """True when Relate must be told --allow-plaintext: an http:// Query API that is not loopback."""
        u = urlparse(self.query_uri)
        return u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1")


def derive_query_uri(bolt_uri: str, port_map: dict[int, int] | None = None) -> str:
    """neo4j+s://host -> https://host; bolt://host:7687 -> http://host:7474.

    `port_map` adds known Bolt-to-HTTP pairs, such as the bundled Neo4j's host ports. Any other port must be set
    with NEO4J_QUERY_URI.
    """
    u = urlparse(bolt_uri)
    if u.scheme in ("http", "https"):
        return bolt_uri.rstrip("/")
    secure = u.scheme.endswith("+s") or u.scheme.endswith("+ssc")
    host = u.hostname or "localhost"
    if secure:
        return f"https://{host}" + (f":{u.port}" if u.port and u.port != 7687 else "")
    port = u.port or 7687
    pairs = {7687: 7474, **(port_map or {})}
    if port in pairs:
        return f"http://{host}:{pairs[port]}"
    raise ValueError(
        f"NEO4J_URI {bolt_uri} uses Bolt port {port}; set NEO4J_QUERY_URI to the http(s) Query API address"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    os.environ.setdefault("DO_NOT_TRACK", "1")
    return Settings()


def reload_settings() -> Settings:
    """For tests: forget the cached settings and read the environment again."""
    get_settings.cache_clear()
    return get_settings()
