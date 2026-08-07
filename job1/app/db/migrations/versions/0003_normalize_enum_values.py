"""normalize PostgreSQL enum labels to application values

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""
from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


RENAMES = {
    "userrole": {
        "CANDIDATE": "candidate",
        "ADMIN": "admin",
    },
    "resumestatus": {
        "UPLOADED": "uploaded",
        "PARSING": "parsing",
        "PARSED": "parsed",
        "SCORED": "scored",
        "FAILED": "failed",
    },
    "applicationstatus": {
        "SAVED": "saved",
        "APPLIED": "applied",
        "INTERVIEWING": "interviewing",
        "OFFER": "offer",
        "REJECTED": "rejected",
        "WITHDRAWN": "withdrawn",
    },
    "messagerole": {
        "USER": "user",
        "ASSISTANT": "assistant",
        "SYSTEM": "system",
    },
}


def _rename_values(pairs: dict[str, dict[str, str]]) -> None:
    for enum_name, values in pairs.items():
        for old, new in values.items():
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM pg_type t
                        JOIN pg_enum e ON e.enumtypid = t.oid
                        JOIN pg_namespace n ON n.oid = t.typnamespace
                        WHERE n.nspname = current_schema()
                          AND t.typname = '{enum_name}'
                          AND e.enumlabel = '{old}'
                    ) THEN
                        ALTER TYPE {enum_name} RENAME VALUE '{old}' TO '{new}';
                    END IF;
                END $$;
                """
            )


def upgrade() -> None:
    _rename_values(RENAMES)


def downgrade() -> None:
    reverse = {
        enum_name: {new: old for old, new in values.items()}
        for enum_name, values in RENAMES.items()
    }
    _rename_values(reverse)
