"""Shared enumerations for task status and priority."""


class Status:
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"  # reserved for Phase 5
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ALL = {QUEUED, RUNNING, AWAITING_APPROVAL, COMPLETED, FAILED, CANCELLED}
    TERMINAL = {COMPLETED, FAILED, CANCELLED}
    ACTIVE = {QUEUED, RUNNING, AWAITING_APPROVAL}


class Priority:
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    ALL = {LOW, NORMAL, HIGH, URGENT}
    # Lower number = dispatched first.
    ORDER = {URGENT: 0, HIGH: 1, NORMAL: 2, LOW: 3}


VALID_MODELS = {"sonnet", "opus", "haiku"}
