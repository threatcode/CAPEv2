# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

"""Database migration from Cuckoo 0.6 to Cuckoo 1.1.

Revision ID: 263a45963c72
Revises: None
Create Date: 2014-03-23 23:30:36.756792

"""

# Revision identifiers, used by Alembic.
revision = "263a45963c72"
mongo_revision = "1"
down_revision = None

import os
import sys
from datetime import datetime

import sqlalchemy as sa

try:
    from dateutil.parser import parse
except ImportError:
    print("Unable to import dateutil.parser", end=" ")
    print("(install with `poetry run pip install python-dateutil`)")
    sys.exit()

try:
    from alembic import op
except ImportError:
    print("Unable to import alembic (install with `poetry run pip install alembic`)")
    sys.exit()

sys.path.append(os.path.join("..", "..", ".."))

import lib.cuckoo.core.database as db
from lib.cuckoo.common.config import Config


def upgrade():
    # BEWARE: be prepared to really spaghetti code. To deal with SQLite limitations in Alembic we coded some workarounds.

    # Migrations are supported starting form Cuckoo 0.6 and Cuckoo 1.0; I need a way to figure out if from which release
    # it will start because both schema are missing alembic release versioning.
    # I check for tags table to distinguish between Cuckoo 0.6 and 1.0.
    conn = op.get_bind()

    if conn.engine.dialect.has_table(conn.engine.connect(), "machines_tags"):
        # If this table exist we are on Cuckoo 1.0 or above.
        # So skip SQL migration.
        pass
    else:
        # We are on Cuckoo < 1.0, hopefully 0.6.
        # So run SQL migration.

        # Create table used by Tag.
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        )

        # Create secondary table used in association Machine - Tag.
        op.create_table(
            "machines_tags",
            sa.Column("machine_id", sa.Integer, sa.ForeignKey("machines.id")),
            sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id")),
        )

        # Add columns to Machine.
        op.add_column("machines", sa.Column("interface", sa.String(length=255), nullable=True))
        op.add_column("machines", sa.Column("snapshot", sa.String(length=255), nullable=True))
        # TODO: change default value, be aware sqlite doesn't support that kind of ALTER statement.
        op.add_column(
            "machines", sa.Column("resultserver_ip", sa.String(length=255), server_default="192.168.56.1", nullable=False)
        )
        # TODO: change default value, be aware sqlite doesn't support that kind of ALTER statement.
        op.add_column("machines", sa.Column("resultserver_port", sa.String(length=255), server_default="2042", nullable=False))

        # Deal with Alembic shit.
        # Alembic is so ORMish that it was impossible to write code which works on different DBMS.
        if conn.engine.driver == "psycopg2":
            # We don"t provide a default value and leave the column as nullable because o further data migration.
            op.add_column("tasks", sa.Column("clock", sa.DateTime(timezone=False), nullable=True))
            # NOTE: We added this new column so we force clock time to the added_on for old analyses.
            conn.execute("update tasks set clock=added_on")
            # Add the not null constraint.
            op.alter_column("tasks", "clock", nullable=False, existing_nullable=True)
            # Altering status ENUM.
            # This shit of raw SQL is here because alembic doesn't deal well with alter_colum of ENUM type.
            op.execute("COMMIT")  # Commit because SQLAlchemy doesn't support ALTER TYPE in a transaction.
            conn.execute("ALTER TYPE status_type ADD VALUE 'completed'")
            conn.execute("ALTER TYPE status_type ADD VALUE 'reported'")
            conn.execute("ALTER TYPE status_type ADD VALUE 'recovered'")
            conn.execute("ALTER TYPE status_type ADD VALUE 'running'")
            conn.execute("ALTER TYPE status_type RENAME ATTRIBUTE success TO completed")
            conn.execute("ALTER TYPE status_type DROP ATTRIBUTE IF EXISTS failure")
        elif conn.engine.driver == "mysqldb":
            # We don"t provide a default value and leave the column as nullable because o further data migration.
            op.add_column("tasks", sa.Column("clock", sa.DateTime(timezone=False), nullable=True))
            # NOTE: We added this new column so we force clock time to the added_on for old analyses.
            conn.execute("update tasks set clock=added_on")
            # Add the not null constraint.
            op.alter_column("tasks", "clock", nullable=False, existing_nullable=True, existing_type=sa.DateTime(timezone=False))
            # NOTE: To workaround limitations in Alembic and MySQL ALTER statement (cannot remove item from ENUM).
            # Read data.
            tasks_data = []
            old_tasks = conn.execute(
                "select id, target, category, timeout, priority, custom, machine, package, options, platform, memory, enforce_timeout, added_on, started_on, completed_on, status, sample_id from tasks"
            ).fetchall()
            for item in old_tasks:
                d = {}
                d["id"] = item[0]
                d["target"] = item[1]
                d["category"] = item[2]
                d["timeout"] = item[3]
                d["priority"] = item[4]
                d["custom"] = item[5]
                d["machine"] = item[6]
                d["package"] = item[7]
                d["options"] = item[8]
                d["platform"] = item[9]
                d["memory"] = item[10]
                d["enforce_timeout"] = item[11]
                if isinstance(item[12], datetime):
                    d["added_on"] = item[12]
                else:
                    d["added_on"] = parse(item[12])
                if isinstance(item[13], datetime):
                    d["started_on"] = item[13]
                else:
                    d["started_on"] = parse(item[13])
                if isinstance(item[14], datetime):
                    d["completed_on"] = item[14]
                else:
                    d["completed_on"] = parse(item[14])
                d["status"] = item[15]
                d["sample_id"] = item[16]

                # Force clock.
                # NOTE: We added this new column so we force clock time to the added_on for old analyses.
                d["clock"] = d["added_on"]
                # Enum migration, "success" isn"t a valid state now.
                if d["status"] == "success":
                    d["status"] = "completed"
                tasks_data.append(d)

            # Rename original table.
            op.rename_table("tasks", "old_tasks")
            # Drop old table.
            op.drop_table("old_tasks")
            # Drop old Enum.
            sa.Enum(name="status_type").drop(op.get_bind(), checkfirst=False)
            # Create new table with 1.0 schema.
            op.create_table(
                "tasks",
                sa.Column("id", sa.Integer(), nullable=False),
                sa.Column("target", sa.String(length=255), nullable=False),
                sa.Column("category", sa.String(length=255), nullable=False),
                sa.Column("timeout", sa.Integer(), server_default="0", nullable=False),
                sa.Column("priority", sa.Integer(), server_default="1", nullable=False),
                sa.Column("custom", sa.String(length=255), nullable=True),
                sa.Column("machine", sa.String(length=255), nullable=True),
                sa.Column("package", sa.String(length=255), nullable=True),
                sa.Column("options", sa.String(length=255), nullable=True),
                sa.Column("platform", sa.String(length=255), nullable=True),
                sa.Column("memory", sa.Boolean(), nullable=False, default=False),
                sa.Column("enforce_timeout", sa.Boolean(), nullable=False, default=False),
                sa.Column("clock", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
                sa.Column("added_on", sa.DateTime(timezone=False), nullable=False),
                sa.Column("started_on", sa.DateTime(timezone=False), nullable=True),
                sa.Column("completed_on", sa.DateTime(timezone=False), nullable=True),
                sa.Column(
                    "status",
                    sa.Enum("pending", "running", "completed", "reported", "recovered", name="status_type"),
                    server_default="pending",
                    nullable=False,
                ),
                sa.Column("sample_id", sa.Integer, sa.ForeignKey("samples.id"), nullable=True),
                sa.PrimaryKeyConstraint("id"),
            )

            # Insert data.
            op.bulk_insert(db.Task.__table__, tasks_data)
        elif conn.engine.driver == "pysqlite":
            # Edit task status enumeration in Task.
            # NOTE: To workaround limitations in SQLite we have to create a temporary table, create the new schema and copy data.
            # Read data.
            tasks_data = []
            old_tasks = conn.execute(
                "select id, target, category, timeout, priority, custom, machine, package, options, platform, memory, enforce_timeout, added_on, started_on, completed_on, status, sample_id from tasks"
            ).fetchall()
            for item in old_tasks:
                d = {}
                d["id"] = item[0]
                d["target"] = item[1]
                d["category"] = item[2]
                d["timeout"] = item[3]
                d["priority"] = item[4]
                d["custom"] = item[5]
                d["machine"] = item[6]
                d["package"] = item[7]
                d["options"] = item[8]
                d["platform"] = item[9]
                d["memory"] = item[10]
                d["enforce_timeout"] = item[11]
                if isinstance(item[12], datetime):
                    d["added_on"] = item[12]
                else:
                    d["added_on"] = parse(item[12])
                if isinstance(item[13], datetime):
                    d["started_on"] = item[13]
                else:
                    d["started_on"] = parse(item[13])
                if isinstance(item[14], datetime):
                    d["completed_on"] = item[14]
                else:
                    d["completed_on"] = parse(item[14])
                d["status"] = item[15]
                d["sample_id"] = item[16]

                # Force clock.
                # NOTE: We added this new column so we force clock time to the added_on for old analyses.
                d["clock"] = d["added_on"]
                # Enum migration, "success" isn"t a valid state now.
                if d["status"] == "success":
                    d["status"] = "completed"
                tasks_data.append(d)

            # Rename original table.
            op.rename_table("tasks", "old_tasks")
            # Drop old table.
            op.drop_table("old_tasks")
            # Drop old Enum.
            sa.Enum(name="status_type").drop(op.get_bind(), checkfirst=False)
            # Create new table with 1.0 schema.
            op.create_table(
                "tasks",
                sa.Column("id", sa.Integer(), nullable=False),
                sa.Column("target", sa.Text(), nullable=False),
                sa.Column("category", sa.String(length=255), nullable=False),
                sa.Column("timeout", sa.Integer(), server_default="0", nullable=False),
                sa.Column("priority", sa.Integer(), server_default="1", nullable=False),
                sa.Column("custom", sa.String(length=255), nullable=True),
                sa.Column("machine", sa.String(length=255), nullable=True),
                sa.Column("package", sa.String(length=255), nullable=True),
                sa.Column("options", sa.String(length=255), nullable=True),
                sa.Column("platform", sa.String(length=255), nullable=True),
                sa.Column("memory", sa.Boolean(), nullable=False, default=False),
                sa.Column("enforce_timeout", sa.Boolean(), nullable=False, default=False),
                sa.Column("clock", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
                sa.Column("added_on", sa.DateTime(timezone=False), nullable=False),
                sa.Column("started_on", sa.DateTime(timezone=False), nullable=True),
                sa.Column("completed_on", sa.DateTime(timezone=False), nullable=True),
                sa.Column(
                    "status",
                    sa.Enum("pending", "running", "completed", "reported", "recovered", name="status_type"),
                    server_default="pending",
                    nullable=False,
                ),
                sa.Column("sample_id", sa.Integer, sa.ForeignKey("samples.id"), nullable=True),
                sa.PrimaryKeyConstraint("id"),
            )

            # Insert data.
            op.bulk_insert(db.Task.__table__, tasks_data)

    # Migrate mongo.
    mongo_upgrade()


def mongo_upgrade():
    """Migrate mongodb schema and data."""
    # Read reporting.conf to fetch mongo configuration.
    config = Config("reporting")
    # Run migration only if mongo is enabled as reporting module.
    if config.mongodb.enabled:
        from dev_utils.mongodb import mongo_collection_names

        # Check for schema version and create it.
        if "cuckoo_schema" in mongo_collection_names:
            print("Mongo schema version not expected")
            sys.exit()
        else:
            db.cuckoo_schema.save({"version": mongo_revision})

    else:
        print("Mongo reporting module not enabled, skipping mongo migration.")


def downgrade():
    # We don"t support downgrade.
    pass
