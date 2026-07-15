"""Logging estructurado con structlog y enmascaramiento de datos sensibles."""

import logging

import structlog


def configurar_logging(nivel: str = "INFO") -> None:
    logging.basicConfig(level=nivel.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(nivel.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def enmascarar_user_id(user_id: str) -> str:
    """Deja visibles solo los últimos 4 dígitos del teléfono en los logs."""
    if not user_id:
        return ""
    if len(user_id) <= 4:
        return "*" * len(user_id)
    return "*" * (len(user_id) - 4) + user_id[-4:]
