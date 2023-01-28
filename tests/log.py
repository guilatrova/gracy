import logging

logger = logging.getLogger("test")

logger.warning(
    "test {v1} [v1] %v1s",
    extra=dict(v1="REPLACEDV1", v2="REPLACEDV2"),
)
