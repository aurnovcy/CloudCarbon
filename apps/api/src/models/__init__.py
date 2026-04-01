"""
SQLAlchemy 2.0 declarative models for CloudCarbon.
Import all models here so Alembic autogenerate can discover them.
"""
from src.models.tenant import Tenant
from src.models.user import User
from src.models.cloud_account import CloudAccount
from src.models.focus_record import FocusRecord
from src.models.enriched_record import EnrichedRecord
from src.models.recommendation import Recommendation
from src.models.agent_run import AgentRun
from src.models.audit_log import AuditLog
from src.models.policy_rule import PolicyRule
from src.models.api_key import ApiKey

__all__ = [
    "Tenant",
    "User",
    "CloudAccount",
    "FocusRecord",
    "EnrichedRecord",
    "Recommendation",
    "AgentRun",
    "AuditLog",
    "PolicyRule",
    "ApiKey",
]
