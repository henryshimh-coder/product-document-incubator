from __future__ import annotations

from enum import StrEnum


class KnowledgeStatus(StrEnum):
    AI_INFERRED = "ai_inferred"
    HUMAN_CONFIRMED = "human_confirmed"
    CANDIDATE = "candidate"
    CONFLICT = "conflict"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class AuthorityLevel(StrEnum):
    FORMAL_EFFECTIVE = "formal_effective"
    FORMAL_DECISION = "formal_decision"
    PROFESSIONAL_OPINION = "professional_opinion"
    DISCUSSION_REFERENCE = "discussion_reference"


class SecurityLevel(StrEnum):
    L1_PUBLIC_SIMULATED = "L1"
    L2_INTERNAL = "L2"
    L3_CONFIDENTIAL = "L3"
    L4_RESTRICTED = "L4"


class IssueSeverity(StrEnum):
    BLOCKING = "blocking"
    PENDING_DECISION = "pending_decision"
    PENDING_INFO = "pending_info"


class IssueStatus(StrEnum):
    OPEN = "open"
    DECIDED = "decided"
    DEFERRED = "deferred"
    FALSE_POSITIVE = "false_positive"
    CLOSED = "closed"


class EvidenceSide(StrEnum):
    CURRENT_BASELINE = "current_baseline"
    CHALLENGING_SOURCE = "challenging_source"


class DecisionAction(StrEnum):
    ACCEPT_CHANGE = "accept_change"
    KEEP_CURRENT = "keep_current"
    DEFER = "defer"
    FALSE_POSITIVE = "false_positive"


class ChangeStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    NEEDS_INFO = "needs_info"
    PUBLISHED = "published"


class ChangeReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"
    REQUEST_INFO = "request_info"


class BaselineStatus(StrEnum):
    DRAFT = "draft"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class CallResultMode(StrEnum):
    REALTIME = "realtime"
    CACHE = "cache"
    LOCAL_ONLY = "local_only"
