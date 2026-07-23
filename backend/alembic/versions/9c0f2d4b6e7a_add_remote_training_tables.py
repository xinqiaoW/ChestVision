"""add remote training tables

Revision ID: 9c0f2d4b6e7a
Revises: 1a6ce5b49d0e
Create Date: 2026-07-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c0f2d4b6e7a"
down_revision: Union[str, None] = "1a6ce5b49d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_uploads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("upload_uuid", sa.String(length=100), nullable=False, comment="上传会话 ID"),
        sa.Column("dataset_uuid", sa.String(length=100), nullable=True, comment="处理后数据集 ID"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="上传用户"),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("dataset_name", sa.String(length=100), nullable=False, comment="数据集名称"),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            comment="INITIATED/UPLOADING/CLIENT_COMPLETED/UPLOADED/FAILED/EXPIRED/CANCELLED；READY 仅兼容旧测试接口",
        ),
        sa.Column("bucket", sa.String(length=128), nullable=False, comment="OSS bucket"),
        sa.Column("raw_object_key", sa.String(length=500), nullable=False, comment="原始 ZIP 对象 key"),
        sa.Column("processed_prefix", sa.String(length=500), nullable=True, comment="处理后数据集前缀"),
        sa.Column("manifest_key", sa.String(length=500), nullable=True, comment="manifest.json key"),
        sa.Column("success_key", sa.String(length=500), nullable=True, comment="_SUCCESS key"),
        sa.Column("original_filename", sa.String(length=255), nullable=False, comment="原始文件名"),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("expected_size", sa.BigInteger(), nullable=True, comment="客户端声明大小"),
        sa.Column("actual_size", sa.BigInteger(), nullable=True, comment="OSS HeadObject 大小"),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=128), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True, comment="OSS metadata"),
        sa.Column("client_progress", sa.Integer(), nullable=True, comment="非可信上传进度"),
        sa.Column("client_completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "server_verified_at",
            sa.DateTime(),
            nullable=True,
            comment="服务端收到 OSS 完成事件或后续 HeadObject 校验通过时间",
        ),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("preprocessing_job_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["scene_id"], ["detection_scenes.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket", "raw_object_key", name="uq_dataset_uploads_bucket_raw_key"),
    )
    op.create_index(op.f("ix_dataset_uploads_dataset_uuid"), "dataset_uploads", ["dataset_uuid"], unique=True)
    op.create_index(op.f("ix_dataset_uploads_preprocessing_job_id"), "dataset_uploads", ["preprocessing_job_id"], unique=False)
    op.create_index(op.f("ix_dataset_uploads_scene_id"), "dataset_uploads", ["scene_id"], unique=False)
    op.create_index(op.f("ix_dataset_uploads_status"), "dataset_uploads", ["status"], unique=False)
    op.create_index(op.f("ix_dataset_uploads_upload_uuid"), "dataset_uploads", ["upload_uuid"], unique=True)
    op.create_index(op.f("ix_dataset_uploads_user_id"), "dataset_uploads", ["user_id"], unique=False)

    op.create_table(
        "remote_training_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False, comment="本地业务训练任务 ID"),
        sa.Column("dataset_upload_id", sa.Integer(), nullable=False, comment="已由 OSS 完成事件确认的 UPLOADED 数据集上传记录"),
        sa.Column("provider", sa.String(length=20), nullable=True, comment="远程训练服务商"),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("dlc_job_id", sa.String(length=128), nullable=True),
        sa.Column("remote_status", sa.String(length=30), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("image_uri", sa.String(length=500), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=True),
        sa.Column("ecs_spec", sa.String(length=100), nullable=True),
        sa.Column("pod_count", sa.Integer(), nullable=True),
        sa.Column("user_command", sa.Text(), nullable=False),
        sa.Column("envs", sa.JSON(), nullable=True),
        sa.Column("input_dataset_prefix", sa.String(length=500), nullable=False),
        sa.Column("output_prefix", sa.String(length=500), nullable=False),
        sa.Column("results_csv_key", sa.String(length=500), nullable=True),
        sa.Column("best_weight_key", sa.String(length=500), nullable=True),
        sa.Column("success_key", sa.String(length=500), nullable=True),
        sa.Column("callback_token_hash", sa.String(length=128), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_upload_id"], ["dataset_uploads.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["training_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_training_jobs_dataset_upload_id"), "remote_training_jobs", ["dataset_upload_id"], unique=False)
    op.create_index(op.f("ix_remote_training_jobs_dlc_job_id"), "remote_training_jobs", ["dlc_job_id"], unique=True)
    op.create_index(op.f("ix_remote_training_jobs_remote_status"), "remote_training_jobs", ["remote_status"], unique=False)
    op.create_index(op.f("ix_remote_training_jobs_task_id"), "remote_training_jobs", ["task_id"], unique=True)

    op.create_table(
        "model_artifact_locations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=True, comment="关联模型版本，可在导出前为空"),
        sa.Column("training_task_id", sa.Integer(), nullable=True, comment="来源训练任务"),
        sa.Column(
            "artifact_type",
            sa.String(length=50),
            nullable=False,
            comment="best_weight/results_csv/eval_report/metrics/success/default_cache",
        ),
        sa.Column("storage_backend", sa.String(length=20), nullable=False, comment="oss/local/minio/http"),
        sa.Column("bucket", sa.String(length=128), nullable=True),
        sa.Column("object_key", sa.String(length=500), nullable=True),
        sa.Column("local_path", sa.String(length=500), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=128), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=True, comment="是否权威副本"),
        sa.Column("lifecycle_state", sa.String(length=30), nullable=True, comment="active/cache/expired/deleted"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["training_task_id"], ["training_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_artifact_locations_artifact_type"), "model_artifact_locations", ["artifact_type"], unique=False)
    op.create_index(op.f("ix_model_artifact_locations_model_version_id"), "model_artifact_locations", ["model_version_id"], unique=False)
    op.create_index(op.f("ix_model_artifact_locations_training_task_id"), "model_artifact_locations", ["training_task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_artifact_locations_training_task_id"), table_name="model_artifact_locations")
    op.drop_index(op.f("ix_model_artifact_locations_model_version_id"), table_name="model_artifact_locations")
    op.drop_index(op.f("ix_model_artifact_locations_artifact_type"), table_name="model_artifact_locations")
    op.drop_table("model_artifact_locations")

    op.drop_index(op.f("ix_remote_training_jobs_task_id"), table_name="remote_training_jobs")
    op.drop_index(op.f("ix_remote_training_jobs_remote_status"), table_name="remote_training_jobs")
    op.drop_index(op.f("ix_remote_training_jobs_dlc_job_id"), table_name="remote_training_jobs")
    op.drop_index(op.f("ix_remote_training_jobs_dataset_upload_id"), table_name="remote_training_jobs")
    op.drop_table("remote_training_jobs")

    op.drop_index(op.f("ix_dataset_uploads_user_id"), table_name="dataset_uploads")
    op.drop_index(op.f("ix_dataset_uploads_upload_uuid"), table_name="dataset_uploads")
    op.drop_index(op.f("ix_dataset_uploads_status"), table_name="dataset_uploads")
    op.drop_index(op.f("ix_dataset_uploads_scene_id"), table_name="dataset_uploads")
    op.drop_index(op.f("ix_dataset_uploads_preprocessing_job_id"), table_name="dataset_uploads")
    op.drop_index(op.f("ix_dataset_uploads_dataset_uuid"), table_name="dataset_uploads")
    op.drop_table("dataset_uploads")
