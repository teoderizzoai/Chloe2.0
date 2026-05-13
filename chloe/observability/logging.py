import structlog

# Keys whose values may contain message text — truncated to prevent PII leaks in logs.
_PII_KEYS = frozenset({
    "text", "user_text", "reply", "message", "body", "preview",
    "raw", "exchange", "content", "observation",
})
_PII_MAX_CHARS = 120


def _pii_redact(logger, method, event_dict):
    for key in _PII_KEYS:
        val = event_dict.get(key)
        if isinstance(val, str) and len(val) > _PII_MAX_CHARS:
            event_dict[key] = val[:_PII_MAX_CHARS] + f"…[+{len(val) - _PII_MAX_CHARS}]"
    return event_dict


def _add_level(logger, method, event_dict):
    event_dict.setdefault("level", method)
    return event_dict


structlog.configure(
    processors=[
        _add_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _pii_redact,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO+
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


def get_logger(name: str):
    return structlog.get_logger(name).bind(logger=name)
