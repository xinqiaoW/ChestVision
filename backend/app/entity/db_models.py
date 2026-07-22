"""
数据库模型定义

表结构总览：
  用户权限：users, roles, permissions, user_roles, role_permissions
  检测业务：detection_scenes, detection_tasks, detection_results
  模型管理：training_tasks, training_metrics, model_versions
  智能体：  chat_sessions, chat_messages
  系统运维：operation_logs
"""

from datetime import datetime

from app.database.session import Base
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship

# ══════════════════════════════════════════════════════════════
# 一、用户与权限（RBAC）
# ══════════════════════════════════════════════════════════════


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(
        String(50), unique=True, nullable=False, index=True, comment="用户名"
    )
    email = Column(String(100), unique=True, nullable=False, index=True, comment="邮箱")
    hashed_password = Column(String(255), nullable=False, comment="加密密码")
    phone = Column(String(20), nullable=True, comment="手机号")
    avatar = Column(String(500), nullable=True, comment="头像 URL")
    is_active = Column(Boolean, default=True, comment="是否启用")
    is_superuser = Column(Boolean, default=False, comment="是否超级管理员")
    user_type = Column(
        String(20),
        nullable=False,
        default="patient",
        comment="用户类型：admin / doctor / patient",
    )
    last_login_at = Column(DateTime, nullable=True, comment="最后登录时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )

    # 关联
    user_roles = relationship(
        "UserRole", back_populates="user", cascade="all, delete-orphan"
    )
    detection_tasks = relationship("DetectionTask", back_populates="user")
    training_tasks = relationship("TrainingTask", back_populates="user")
    chat_sessions = relationship("ChatSession", back_populates="user")
    operation_logs = relationship("OperationLog", back_populates="user")
    # v3.0 新增
    patient_profile = relationship(
        "PatientProfile",
        back_populates="user",
        uselist=False,
        foreign_keys="PatientProfile.user_id",
    )
    doctor_patients = relationship(
        "DoctorPatientRelation",
        back_populates="doctor",
        foreign_keys="DoctorPatientRelation.doctor_id",
    )
    assigned_doctors = relationship(
        "DoctorPatientRelation",
        back_populates="patient",
        foreign_keys="DoctorPatientRelation.patient_id",
    )


class EmailVerificationCode(Base):
    """邮箱验证码审计表；仅保存 HMAC 哈希，不保存验证码明文。"""

    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(100), nullable=False, index=True)
    purpose = Column(String(30), nullable=False, default="register", index=True)
    code_hash = Column(String(64), nullable=False)
    request_ip = Column(String(50), nullable=True, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)


class Role(Base):
    """角色表"""

    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(
        String(50),
        unique=True,
        nullable=False,
        comment="角色标识，如 admin/operator/viewer",
    )
    display_name = Column(
        String(100), nullable=False, comment="角色显示名，如 管理员/操作员/访客"
    )
    description = Column(String(500), nullable=True, comment="角色描述")
    is_system = Column(Boolean, default=False, comment="是否系统内置角色（不可删除）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关联
    user_roles = relationship(
        "UserRole", back_populates="role", cascade="all, delete-orphan"
    )
    role_permissions = relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan"
    )


class Permission(Base):
    """权限表"""

    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(
        String(100),
        unique=True,
        nullable=False,
        comment="权限编码，如 detection:task:create",
    )
    name = Column(String(100), nullable=False, comment="权限名称")
    module = Column(
        String(50),
        nullable=False,
        comment="所属模块：auth/detection/training/agent/system",
    )
    description = Column(String(500), nullable=True, comment="权限描述")

    # 关联
    role_permissions = relationship(
        "RolePermission", back_populates="permission", cascade="all, delete-orphan"
    )


class UserRole(Base):
    """用户-角色关联表（多对多）"""

    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")


class RolePermission(Base):
    """角色-权限关联表（多对多）"""

    __tablename__ = "role_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)
    permission_id = Column(
        Integer, ForeignKey("permissions.id"), nullable=False, index=True
    )

    role = relationship("Role", back_populates="role_permissions")
    permission = relationship("Permission", back_populates="role_permissions")


# ══════════════════════════════════════════════════════════════
# 二、检测业务
# ══════════════════════════════════════════════════════════════


class DetectionScene(Base):
    """检测场景配置表
    每个小组/业务方向一个场景，如：遥感检测、工业缺陷、农业病害等
    场景决定了使用哪个模型、检测哪些类别
    """

    __tablename__ = "detection_scenes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(
        String(100), unique=True, nullable=False, comment="场景标识，如 remote_sensing"
    )
    display_name = Column(
        String(100), nullable=False, comment="场景显示名，如 遥感目标检测"
    )
    description = Column(Text, nullable=True, comment="场景描述")
    category = Column(
        String(50),
        nullable=False,
        comment="场景分类：agriculture/industry/remote_sensing/medical/traffic",
    )
    class_names = Column(
        JSON, nullable=False, comment='类别列表，如 ["airplane","storage-tank"]'
    )
    class_names_cn = Column(
        JSON, nullable=True, comment='类别中文名映射，如 {"airplane":"飞机"}'
    )
    is_active = Column(Boolean, default=True, comment="是否启用")
    created_by = Column(
        Integer, ForeignKey("users.id"), nullable=True, comment="创建人"
    )
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联
    detection_tasks = relationship("DetectionTask", back_populates="scene")
    model_versions = relationship("ModelVersion", back_populates="scene")
    training_tasks = relationship("TrainingTask", back_populates="scene")


class DetectionTask(Base):
    """检测任务表 — 每次检测操作生成一条任务记录"""

    __tablename__ = "detection_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True, comment="操作用户"
    )
    scene_id = Column(
        Integer,
        ForeignKey("detection_scenes.id"),
        nullable=False,
        index=True,
        comment="使用的检测场景",
    )
    model_version_id = Column(
        Integer,
        ForeignKey("model_versions.id"),
        nullable=True,
        comment="使用的模型版本",
    )
    # v3.0 新增：关联患者与影像
    patient_profile_id = Column(
        Integer,
        ForeignKey("patient_profiles.id"),
        nullable=True,
        index=True,
        comment="关联患者档案",
    )
    cxr_image_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="关联胸片影像记录（cxr_images 表待建）",
    )
    task_type = Column(
        String(20), nullable=False, comment="检测类型：single/batch/folder/video/camera"
    )
    status = Column(
        String(20),
        default="pending",
        comment="状态：pending/processing/completed/failed",
    )

    # 检测统计
    total_images = Column(Integer, default=0, comment="处理图像总数")
    total_objects = Column(Integer, default=0, comment="检测到目标总数")
    total_inference_time = Column(Float, default=0, comment="总推理耗时（ms）")

    # 检测参数
    conf_threshold = Column(Float, default=0.25, comment="置信度阈值")
    iou_threshold = Column(Float, default=0.45, comment="NMS IoU 阈值")
    image_size = Column(Integer, default=640, comment="推理图像尺寸")

    # 错误信息
    error_message = Column(Text, nullable=True, comment="失败时的错误信息")

    # 分析与建议（AI 生成）
    analysis_report = Column(Text, nullable=True, comment="分析报告（Markdown 格式）")
    analysis_suggestion = Column(Text, nullable=True, comment="专业建议")
    risk_level = Column(
        String(20), nullable=True, comment="风险等级：low/medium/high/critical"
    )
    referenced_record_ids = Column(
        JSON, nullable=True, comment="LLM 分析时引用的历史病例 ID 列表"
    )
    analyzed_at = Column(DateTime, nullable=True, comment="分析完成时间")

    created_at = Column(DateTime, default=datetime.now, index=True, comment="创建时间")
    completed_at = Column(DateTime, nullable=True, comment="完成时间")

    # 关联
    user = relationship("User", back_populates="detection_tasks")
    scene = relationship("DetectionScene", back_populates="detection_tasks")
    model_version = relationship("ModelVersion", back_populates="detection_tasks")
    patient_profile = relationship(
        "PatientProfile",
        back_populates="detection_tasks",
        foreign_keys=[patient_profile_id],
    )
    results = relationship(
        "DetectionResult", back_populates="task", cascade="all, delete-orphan"
    )


class DetectionResult(Base):
    """检测结果表 — 每张图像中每个检测到的目标一条记录"""

    __tablename__ = "detection_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        Integer,
        ForeignKey("detection_tasks.id"),
        nullable=False,
        index=True,
        comment="所属检测任务",
    )
    image_path = Column(String(500), nullable=False, comment="原始图像路径")
    annotated_image_url = Column(
        String(500), nullable=True, comment="标注图像 MinIO URL"
    )

    # 单个目标信息
    class_name = Column(String(50), nullable=False, index=True, comment="类别名称")
    class_name_cn = Column(String(50), nullable=True, comment="类别中文名")
    class_id = Column(Integer, nullable=False, comment="类别 ID")
    confidence = Column(Float, nullable=False, comment="置信度 0~1")
    bbox = Column(JSON, nullable=False, comment="边界框 [x1, y1, x2, y2]")

    # 图像级信息（冗余存储，方便查询）
    inference_time = Column(Float, nullable=True, comment="该图推理耗时（ms）")
    image_width = Column(Integer, nullable=True, comment="图像宽度")
    image_height = Column(Integer, nullable=True, comment="图像高度")

    created_at = Column(DateTime, default=datetime.now)

    # 关联
    task = relationship("DetectionTask", back_populates="results")


# ══════════════════════════════════════════════════════════════
# 三、模型管理
# ══════════════════════════════════════════════════════════════


class TrainingTask(Base):
    """模型训练任务表"""

    __tablename__ = "training_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True, comment="操作用户"
    )
    scene_id = Column(
        Integer,
        ForeignKey("detection_scenes.id"),
        nullable=False,
        index=True,
        comment="关联场景",
    )
    task_uuid = Column(
        String(100), unique=True, nullable=False, index=True, comment="任务唯一标识"
    )
    status = Column(
        String(20),
        default="pending",
        comment="状态：pending/running/completed/failed/cancelled",
    )

    # 训练配置
    model_name = Column(
        String(50), default="yolo11n", comment="基础模型：yolo11n/s/m/l/x"
    )
    epochs = Column(Integer, default=100, comment="训练轮数")
    img_size = Column(Integer, default=640, comment="图像尺寸")
    batch_size = Column(Integer, default=16, comment="批次大小")
    device = Column(String(20), default="0", comment="训练设备：0/1/cpu")
    optimizer = Column(String(20), default="SGD", comment="优化器：SGD/Adam/AdamW")
    lr0 = Column(Float, default=0.01, comment="初始学习率")
    augment_config = Column(JSON, nullable=True, comment="数据增强配置")

    # 训练进度
    current_epoch = Column(Integer, default=0, comment="当前轮数")
    progress = Column(Integer, default=0, comment="进度百分比 0~100")

    # 数据集信息
    dataset_path = Column(String(500), nullable=True, comment="数据集路径")
    dataset_size = Column(Integer, nullable=True, comment="数据集图像数量")
    data_yaml = Column(String(500), nullable=True, comment="data.yaml 路径")

    # 错误信息
    error_message = Column(Text, nullable=True, comment="失败错误信息")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
    started_at = Column(DateTime, nullable=True, comment="开始训练时间")
    completed_at = Column(DateTime, nullable=True, comment="训练完成时间")

    # 关联
    user = relationship("User", back_populates="training_tasks")
    scene = relationship("DetectionScene", back_populates="training_tasks")
    metrics = relationship(
        "TrainingMetric", back_populates="task", cascade="all, delete-orphan"
    )
    model_versions = relationship("ModelVersion", back_populates="training_task")


class TrainingMetric(Base):
    """训练指标表 — 每个 epoch 记录一条，用于绘制训练曲线"""

    __tablename__ = "training_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        Integer,
        ForeignKey("training_tasks.id"),
        nullable=False,
        index=True,
        comment="所属训练任务",
    )
    epoch = Column(Integer, nullable=False, comment="当前轮数")

    # 损失值
    box_loss = Column(Float, nullable=True, comment="边界框损失")
    cls_loss = Column(Float, nullable=True, comment="分类损失")
    dfl_loss = Column(Float, nullable=True, comment="DFL 损失")

    # 评估指标
    precision = Column(Float, nullable=True, comment="精确率")
    recall = Column(Float, nullable=True, comment="召回率")
    map50 = Column(Float, nullable=True, comment="mAP@0.50")
    map50_95 = Column(Float, nullable=True, comment="mAP@0.50:0.95")

    # 学习率
    lr = Column(Float, nullable=True, comment="当前学习率")

    created_at = Column(DateTime, default=datetime.now)

    # 关联
    task = relationship("TrainingTask", back_populates="metrics")


class ModelVersion(Base):
    """模型版本管理表 — 每次训练产出或手动上传的模型版本"""

    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene_id = Column(
        Integer,
        ForeignKey("detection_scenes.id"),
        nullable=False,
        index=True,
        comment="所属场景",
    )
    training_task_id = Column(
        Integer,
        ForeignKey("training_tasks.id"),
        nullable=True,
        comment="来源训练任务（可为空，支持手动上传）",
    )

    version = Column(String(50), nullable=False, comment="版本号，如 v1.0.0")
    model_name = Column(String(100), nullable=False, comment="模型名称")
    model_type = Column(
        String(50), default="yolo11n", comment="模型类型：yolo11n/s/m/l/x"
    )
    status = Column(
        String(20), default="active", comment="状态：active/archived/deleted"
    )

    # 模型文件
    model_path = Column(String(500), nullable=False, comment="本地模型文件路径")
    minio_url = Column(String(500), nullable=True, comment="MinIO 存储 URL")

    # 评估指标（训练完成后写入）
    map50 = Column(Float, nullable=True, comment="mAP@0.50")
    map50_95 = Column(Float, nullable=True, comment="mAP@0.50:0.95")
    precision = Column(Float, nullable=True, comment="精确率")
    recall = Column(Float, nullable=True, comment="召回率")
    per_class_ap = Column(
        JSON, nullable=True, comment='各类别 AP，如 {"airplane":0.85,"tank":0.72}'
    )

    # 元信息
    description = Column(Text, nullable=True, comment="版本描述/变更说明")
    file_size = Column(BigInteger, nullable=True, comment="模型文件大小（字节）")
    is_default = Column(Boolean, default=False, comment="是否为该场景的默认模型")

    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关联
    scene = relationship("DetectionScene", back_populates="model_versions")
    training_task = relationship("TrainingTask", back_populates="model_versions")
    detection_tasks = relationship("DetectionTask", back_populates="model_version")


# ══════════════════════════════════════════════════════════════
# 四、智能体对话
# ══════════════════════════════════════════════════════════════


class ChatSession(Base):
    """对话会话表 — 每次对话创建一个会话"""

    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True, comment="所属用户"
    )
    session_uuid = Column(
        String(100), unique=True, nullable=False, index=True, comment="会话唯一标识"
    )
    title = Column(String(200), nullable=True, comment="会话标题（取第一条消息摘要）")
    status = Column(String(20), default="active", comment="状态：active/archived")
    message_count = Column(Integer, default=0, comment="消息数量")
    last_message_at = Column(DateTime, nullable=True, comment="最后消息时间")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联
    user = relationship("User", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """对话消息表 — 每条消息（用户/AI/工具调用）一条记录"""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer,
        ForeignKey("chat_sessions.id"),
        nullable=False,
        index=True,
        comment="所属会话",
    )
    role = Column(
        String(20), nullable=False, comment="消息角色：user/assistant/tool/system"
    )
    content = Column(Text, nullable=False, comment="消息内容")

    # 智能体路由信息
    agent_used = Column(
        String(50),
        nullable=True,
        comment="处理的 Agent：supervisor/detection/analysis/qa",
    )
    tool_calls = Column(
        JSON,
        nullable=True,
        comment='工具调用记录，如 [{"tool":"detect_objects","args":{...}}]',
    )
    tool_result = Column(Text, nullable=True, comment="工具调用返回结果")

    # 元信息
    tokens_used = Column(Integer, nullable=True, comment="Token 消耗量")
    latency_ms = Column(Integer, nullable=True, comment="响应耗时（毫秒）")

    created_at = Column(DateTime, default=datetime.now, index=True, comment="创建时间")

    # 关联
    session = relationship("ChatSession", back_populates="messages")


class DoctorRecommendation(Base):
    """AI 医生推荐审计记录 — 保存模型当时的排序与推荐依据。"""

    __tablename__ = "doctor_recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    detection_task_id = Column(
        Integer, ForeignKey("detection_tasks.id"), nullable=False, index=True
    )
    patient_profile_id = Column(
        Integer, ForeignKey("patient_profiles.id"), nullable=True, index=True
    )
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    match_score = Column(Float, nullable=False, default=0)
    display_name = Column(String(100), nullable=False)
    specialty = Column(String(200), nullable=True)
    matched_lesions = Column(JSON, nullable=True)
    reasons = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    context_snapshot = Column(JSON, nullable=True)
    model_name = Column(String(100), nullable=True)
    selection_method = Column(String(20), default="ai", comment="ai / fallback")
    status = Column(
        String(20), default="recommended", comment="recommended / selected / dismissed"
    )
    selected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    selected_at = Column(DateTime, nullable=True)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    review_note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)


# ══════════════════════════════════════════════════════════════
# 五、系统运维
# ══════════════════════════════════════════════════════════════


class OperationLog(Base):
    """操作审计日志表 — 记录用户关键操作"""

    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment="操作用户（可为空表示系统操作）",
    )
    username = Column(String(50), nullable=True, comment="冗余用户名，方便查询")

    # 操作信息
    module = Column(
        String(50),
        nullable=False,
        comment="操作模块：auth/detection/training/agent/system",
    )
    action = Column(
        String(50),
        nullable=False,
        comment="操作类型：create/update/delete/login/export",
    )
    target_type = Column(
        String(50), nullable=True, comment="操作对象类型：user/task/model/session"
    )
    target_id = Column(String(100), nullable=True, comment="操作对象 ID")
    description = Column(String(500), nullable=True, comment="操作描述")

    # 请求信息
    ip_address = Column(String(50), nullable=True, comment="客户端 IP")
    user_agent = Column(String(500), nullable=True, comment="客户端 User-Agent")
    request_method = Column(String(10), nullable=True, comment="HTTP 方法")
    request_path = Column(String(500), nullable=True, comment="请求路径")

    # 结果
    status = Column(String(20), default="success", comment="操作结果：success/failure")
    error_message = Column(Text, nullable=True, comment="失败时的错误信息")

    created_at = Column(DateTime, default=datetime.now, index=True, comment="创建时间")

    # 关联
    user = relationship("User", back_populates="operation_logs")


# ══════════════════════════════════════════════════════════════
# 八、患者与病例（v3.0 新增）
# ══════════════════════════════════════════════════════════════


class PatientProfile(Base):
    """患者档案表 — 病人用户的扩展信息"""

    __tablename__ = "patient_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
        index=True,
        comment="关联的用户账号",
    )
    patient_code = Column(
        String(50), unique=True, nullable=False, index=True, comment="患者编号"
    )
    real_name = Column(String(50), nullable=True, comment="真实姓名")
    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True, comment="Male / Female / Unknown")
    birth_date = Column(DateTime, nullable=True, comment="出生日期")
    id_card_hash = Column(String(64), nullable=True, comment="身份证号哈希")
    blood_type = Column(String(5), nullable=True, comment="血型")
    height_cm = Column(Float, nullable=True, comment="身高 cm")
    weight_kg = Column(Float, nullable=True, comment="体重 kg")
    allergies = Column(Text, nullable=True, comment="过敏史")
    department = Column(String(100), nullable=True, comment="就诊科室")
    emergency_contact = Column(String(50), nullable=True, comment="紧急联系人")
    emergency_phone = Column(String(20), nullable=True, comment="紧急联系电话")
    notes = Column(Text, nullable=True, comment="备注")
    is_active = Column(Boolean, default=True, comment="是否启用")
    created_by = Column(
        Integer, ForeignKey("users.id"), nullable=True, comment="创建人"
    )
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )

    # 关联
    user = relationship(
        "User", back_populates="patient_profile", foreign_keys=[user_id]
    )
    medical_records = relationship("MedicalRecord", back_populates="patient_profile")
    # TODO: 待 CxrImage / DetectionReport 模型创建后取消注释
    # cxr_images = relationship("CxrImage", back_populates="patient_profile")
    # detection_reports = relationship(
    #     "DetectionReport", back_populates="patient_profile"
    # )
    detection_tasks = relationship(
        "DetectionTask",
        back_populates="patient_profile",
        foreign_keys="DetectionTask.patient_profile_id",
    )


class DoctorPatientRelation(Base):
    """医患关联表 — 医生和病人的多对多关系"""

    __tablename__ = "doctor_patient_relations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        comment="医生用户ID",
    )
    patient_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        comment="病人用户ID",
    )
    relation_status = Column(String(20), default="active", comment="active / inactive")
    notes = Column(String(500), nullable=True, comment="备注")
    assigned_by = Column(
        Integer, ForeignKey("users.id"), nullable=True, comment="分配者（管理员）"
    )
    created_at = Column(DateTime, default=datetime.now, comment="关联时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )

    # 关联
    doctor = relationship(
        "User", back_populates="doctor_patients", foreign_keys=[doctor_id]
    )
    patient = relationship(
        "User", back_populates="assigned_doctors", foreign_keys=[patient_id]
    )


class MedicalRecord(Base):
    """病例表 — 结构化临床病例记录"""

    __tablename__ = "medical_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_profile_id = Column(
        Integer,
        ForeignKey("patient_profiles.id"),
        nullable=False,
        index=True,
        comment="所属患者",
    )
    record_uuid = Column(
        String(100), unique=True, nullable=False, index=True, comment="病例唯一标识"
    )
    record_type = Column(
        String(30),
        default="outpatient",
        comment="outpatient / inpatient / follow_up / emergency",
    )
    chief_complaint = Column(Text, nullable=True, comment="主诉")
    present_illness = Column(Text, nullable=True, comment="现病史")
    past_history = Column(Text, nullable=True, comment="既往史")
    family_history = Column(Text, nullable=True, comment="家族史")
    physical_examination = Column(Text, nullable=True, comment="体格检查")
    auxiliary_exams = Column(JSON, nullable=True, comment="辅助检查结果")
    diagnosis = Column(JSON, nullable=True, comment="诊断结论列表")
    treatment_plan = Column(Text, nullable=True, comment="治疗方案")
    prescription = Column(JSON, nullable=True, comment="处方信息")
    doctor_notes = Column(Text, nullable=True, comment="医生备注")
    record_status = Column(
        String(20), default="draft", comment="draft / completed / reviewed"
    )
    visit_date = Column(DateTime, nullable=True, comment="就诊日期")
    created_by = Column(
        Integer, ForeignKey("users.id"), nullable=False, comment="创建医生"
    )
    updated_by = Column(
        Integer, ForeignKey("users.id"), nullable=True, comment="最后编辑者"
    )
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )

    # 关联
    patient_profile = relationship("PatientProfile", back_populates="medical_records")
    attachments = relationship(
        "MedicalRecordAttachment", back_populates="record", cascade="all, delete-orphan"
    )


class MedicalRecordAttachment(Base):
    """病例附件表"""

    __tablename__ = "medical_record_attachments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    record_id = Column(
        Integer,
        ForeignKey("medical_records.id"),
        nullable=False,
        index=True,
        comment="所属病例",
    )
    attachment_type = Column(
        String(20),
        nullable=False,
        comment="cxr_image / lab_report / prescription / other",
    )
    file_name = Column(String(255), nullable=False, comment="文件名")
    minio_path = Column(String(500), nullable=False, comment="MinIO 路径")
    minio_url = Column(String(500), nullable=True, comment="预签名 URL")
    file_size = Column(BigInteger, nullable=True, comment="文件大小")
    mime_type = Column(String(100), nullable=True, comment="MIME 类型")
    description = Column(String(500), nullable=True, comment="附件描述")
    uploaded_by = Column(
        Integer, ForeignKey("users.id"), nullable=True, comment="上传人"
    )
    created_at = Column(DateTime, default=datetime.now, comment="上传时间")

    # 关联
    record = relationship("MedicalRecord", back_populates="attachments")
