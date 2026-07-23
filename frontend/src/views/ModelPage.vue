<template>
  <div class="model-page">
    <div class="page-header">
      <div>
        <h2>模型管理</h2>
        <div class="header-meta">
          <span>{{ modelList.length }} 个模型</span>
          <span>{{ uploadedCount }} 个上传模型</span>
          <span>{{ trainedCount }} 个训练模型</span>
        </div>
      </div>
      <div class="header-actions">
        <el-button @click="refreshAll" :loading="loadingDefault || loadingModels">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="primary" @click="openUploadDialog">
          <el-icon><Upload /></el-icon>上传模型
        </el-button>
      </div>
    </div>

    <el-card class="default-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>默认推理模型</span>
          <el-tag v-if="defaultScene" size="small">
            {{ defaultScene.display_name || defaultScene.name }}
          </el-tag>
        </div>
      </template>

      <el-skeleton v-if="loadingDefault" :rows="3" animated />
      <el-empty v-else-if="!defaultModel" description="暂无默认推理模型" />
      <div v-else class="default-model-grid">
        <div class="default-model-main">
          <el-icon class="default-model-icon"><Box /></el-icon>
          <div class="default-model-title">
            <strong>{{ defaultModel.model_name }}</strong>
            <span>{{ defaultModel.version }}</span>
          </div>
        </div>
        <div class="default-detail">
          <span class="detail-label">模型类型</span>
          <span>{{ defaultModel.model_type || "-" }}</span>
        </div>
        <div class="default-detail">
          <span class="detail-label">来源</span>
          <span>{{ sourceText(defaultModel) }}</span>
        </div>
        <div class="default-detail">
          <span class="detail-label">权重大小</span>
          <span>{{ formatBytes(defaultModel.file_size) }}</span>
        </div>
        <div class="default-detail">
          <span class="detail-label">创建日期</span>
          <span>{{ formatDate(defaultModel.created_at) }}</span>
        </div>
        <div class="default-detail">
          <span class="detail-label">本地缓存</span>
          <span>
            <el-tag :type="cacheTagType(defaultCache?.cache_status)" size="small">
              {{ cacheStatusText(defaultCache?.cache_status) }}
            </el-tag>
            <span v-if="defaultCache?.cache_updated_at" class="cache-time">
              {{ formatDate(defaultCache.cache_updated_at) }}
            </span>
          </span>
        </div>
      </div>
    </el-card>

    <div class="upload-band">
      <el-button type="primary" @click="openUploadDialog">
        <el-icon><Upload /></el-icon>上传模型
      </el-button>
    </div>

    <el-card class="models-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>所有模型</span>
          <el-button text @click="fetchModels" :loading="loadingModels">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
      </template>

      <div class="filters">
        <el-input
          v-model="filters.modelName"
          clearable
          placeholder="模型名称"
          @keyup.enter="fetchModels"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-input
          v-model="filters.version"
          clearable
          placeholder="版本"
          @keyup.enter="fetchModels"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="filters.sceneId" clearable placeholder="场景类型">
          <el-option
            v-for="scene in sceneOptions"
            :key="scene.id"
            :label="scene.display_name || scene.name"
            :value="scene.id"
          />
        </el-select>
        <el-select v-model="filters.modelType" clearable placeholder="模型类型">
          <el-option
            v-for="item in MODEL_TYPES"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="filters.sourceType" placeholder="来源">
          <el-option label="全部来源" value="all" />
          <el-option label="训练得到" value="trained" />
          <el-option label="上传" value="uploaded" />
        </el-select>
        <el-button type="primary" @click="fetchModels">筛选</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>

      <el-table
        :data="modelList"
        stripe
        v-loading="loadingModels"
        empty-text="暂无模型"
      >
        <el-table-column prop="model_name" label="模型名称" min-width="170" />
        <el-table-column prop="version" label="版本" min-width="120" />
        <el-table-column label="场景类型" min-width="130">
          <template #default="{ row }">
            {{ row.scene_display_name || row.scene_name || "-" }}
          </template>
        </el-table-column>
        <el-table-column prop="model_type" label="模型类型" width="110" />
        <el-table-column label="来源" min-width="150">
          <template #default="{ row }">
            <el-button
              v-if="row.source_type === 'trained' && row.source_training"
              text
              type="primary"
              @click="openTrainingSource(row)"
            >
              {{ row.source_label }}
            </el-button>
            <el-tag v-else size="small">上传</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建日期" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              text
              :disabled="!row.downloadable"
              @click="downloadWeight(row)"
            >
              <el-icon><Download /></el-icon>下载权重
            </el-button>
            <el-button size="small" text type="primary" @click="openConfig(row)">
              <el-icon><Setting /></el-icon>配置
            </el-button>
            <el-button size="small" text type="danger" @click="confirmDelete(row)">
              <el-icon><Delete /></el-icon>删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="showUploadDialog"
      title="上传模型"
      width="620px"
      :close-on-click-modal="false"
      :show-close="!uploading"
      :before-close="beforeUploadDialogClose"
      @closed="resetUploadForm"
    >
      <el-form label-width="100px">
        <el-form-item label="场景类型">
          <el-select
            v-model="uploadForm.sceneId"
            clearable
            placeholder="默认胸片检查场景"
            style="width: 100%"
            :disabled="uploading"
          >
            <el-option
              v-for="scene in sceneOptions"
              :key="scene.id"
              :label="scene.display_name || scene.name"
              :value="scene.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="模型名称">
          <el-input
            v-model="uploadForm.modelName"
            :disabled="uploading"
            maxlength="100"
            placeholder="chest_yolo_custom"
          />
        </el-form-item>
        <el-form-item label="版本">
          <el-input
            v-model="uploadForm.version"
            :disabled="uploading"
            maxlength="50"
            placeholder="v1.0.0"
          />
        </el-form-item>
        <el-form-item label="模型类型">
          <el-select
            v-model="uploadForm.modelType"
            filterable
            style="width: 100%"
            :disabled="uploading"
          >
            <el-option
              v-for="item in MODEL_TYPES"
              :key="item.value"
              :label="`${item.label} · ${item.extensions.join('/')}`"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="模型文件">
          <el-upload
            drag
            :accept="acceptedModelExtensions"
            :auto-upload="false"
            :limit="1"
            :file-list="modelFileList"
            :show-file-list="false"
            :on-change="onModelFileChange"
            :on-remove="onModelFileRemove"
            :disabled="uploading"
          >
            <div v-if="selectedModelFile" class="selected-upload-file">
              <el-icon><Document /></el-icon>
              <span class="selected-file-name" :title="selectedModelFile.name">
                {{ selectedModelFile.name }}
              </span>
              <el-button
                v-if="!uploading"
                text
                type="danger"
                @click.stop="onModelFileRemove"
              >
                移除
              </el-button>
            </div>
            <template v-else>
              <el-icon class="upload-icon"><UploadFilled /></el-icon>
              <div class="el-upload__text">
                拖入或选择 {{ acceptedModelExtensions }} 模型文件
              </div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="uploadForm.description"
            type="textarea"
            :rows="2"
            :disabled="uploading"
            maxlength="1000"
            placeholder="可选"
          />
        </el-form-item>
      </el-form>

      <div v-if="uploadProgress.visible" class="upload-progress">
        <el-progress
          :percentage="Math.round(uploadProgress.percent)"
          :status="uploadProgressStatus"
        />
        <div class="progress-meta">
          <span>
            {{ formatBytes(uploadProgress.uploadedBytes) }} /
            {{ formatBytes(uploadProgress.totalBytes) }}
          </span>
          <span>速度 {{ formatSpeed(uploadProgress.speedBytesPerSecond) }}</span>
          <span>预计剩余 {{ formatDuration(uploadProgress.remainingSeconds) }}</span>
        </div>
        <div class="progress-parts">
          已上传分片 {{ uploadProgress.uploadedParts }}/{{ uploadProgress.totalParts }}
        </div>
      </div>

      <template #footer>
        <div class="upload-footer">
          <el-button
            v-if="uploading"
            :disabled="!canPauseUpload"
            @click="toggleUploadPause"
          >
            <el-icon>
              <VideoPlay v-if="uploadPaused" />
              <VideoPause v-else />
            </el-icon>
            {{ uploadPaused ? "继续上传" : "暂停上传" }}
          </el-button>
          <el-button
            :class="{ 'cancel-upload-button': uploading }"
            :disabled="uploading ? !canCancelUpload : !canStartUpload"
            :loading="cancelingUpload"
            :plain="uploading"
            :type="uploading ? 'danger' : 'primary'"
            @click="handleUploadAction"
          >
            <el-icon>
              <CircleClose v-if="uploading" />
              <Upload v-else />
            </el-icon>
            {{ uploading ? "取消上传" : "分片上传" }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showConfigDialog" title="配置模型" width="480px">
      <div v-if="configModel" class="config-body">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="模型名称">
            {{ configModel.model_name }}
          </el-descriptions-item>
          <el-descriptions-item label="版本">
            {{ configModel.version }}
          </el-descriptions-item>
          <el-descriptions-item label="场景类型">
            {{ configModel.scene_display_name || configModel.scene_name || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="当前默认">
            {{ configModel.is_default ? "是" : "否" }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
      <template #footer>
        <el-button @click="showConfigDialog = false">关闭</el-button>
        <el-button
          type="primary"
          :disabled="configModel?.is_default"
          :loading="settingDefaultId === configModel?.id"
          @click="confirmSetDefault"
        >
          替换当前默认推理模型
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import {
  MODEL_TYPES,
  MODEL_UPLOAD_PART_SIZE,
  ModelUploadCancelledError,
  createModelUploadController,
  deleteModel,
  getDefaultModel,
  getModelDownloadUrl,
  getModels,
  modelTypeRule,
  setDefaultModel,
  uploadModel,
  validateModelFileExtension,
} from "@/api/modelManagement";
import {
  Box,
  CircleClose,
  Delete,
  Document,
  Download,
  Refresh,
  Search,
  Setting,
  Upload,
  UploadFilled,
  VideoPause,
  VideoPlay,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

const router = useRouter();

const loadingDefault = ref(false);
const loadingModels = ref(false);
const defaultInfo = ref(null);
const modelList = ref([]);
const filters = ref({
  modelName: "",
  version: "",
  sceneId: null,
  modelType: "",
  sourceType: "all",
});

const showUploadDialog = ref(false);
const uploading = ref(false);
const cancelingUpload = ref(false);
const uploadPaused = ref(false);
const uploadForm = ref(createDefaultUploadForm());
const selectedModelFile = ref(null);
const modelFileList = ref([]);
const uploadProgress = ref(createUploadProgress());
let uploadController = null;
let unsubscribeUploadController = null;

const showConfigDialog = ref(false);
const configModel = ref(null);
const settingDefaultId = ref(null);

const defaultModel = computed(() => defaultInfo.value?.model || null);
const defaultScene = computed(() => defaultInfo.value?.scene || null);
const defaultCache = computed(() => defaultInfo.value?.cache || null);
const uploadedCount = computed(
  () => modelList.value.filter((item) => item.source_type === "uploaded").length,
);
const trainedCount = computed(
  () => modelList.value.filter((item) => item.source_type === "trained").length,
);

const sceneOptions = computed(() => {
  const map = new Map();
  const addScene = (scene) => {
    if (!scene?.id) return;
    map.set(scene.id, {
      id: scene.id,
      name: scene.name,
      display_name: scene.display_name,
    });
  };
  addScene(defaultScene.value);
  modelList.value.forEach((model) => {
    if (model.scene_id) {
      addScene({
        id: model.scene_id,
        name: model.scene_name,
        display_name: model.scene_display_name,
      });
    }
  });
  return Array.from(map.values());
});

const acceptedModelExtensions = computed(() =>
  modelTypeRule(uploadForm.value.modelType).extensions.join(","),
);

const canStartUpload = computed(() => {
  if (!selectedModelFile.value) return false;
  return Boolean(
    uploadForm.value.modelName.trim() &&
      uploadForm.value.version.trim() &&
      uploadForm.value.modelType &&
      validateModelFileExtension(
        selectedModelFile.value.name,
        uploadForm.value.modelType,
      ),
  );
});

const canPauseUpload = computed(
  () =>
    uploading.value &&
    !cancelingUpload.value &&
    !["finalizing", "completed", "cancelling", "cancelled"].includes(
      uploadProgress.value.phase,
    ),
);

const canCancelUpload = computed(
  () =>
    uploading.value &&
    !cancelingUpload.value &&
    !["finalizing", "completed", "cancelling", "cancelled"].includes(
      uploadProgress.value.phase,
    ),
);

const uploadProgressStatus = computed(() => {
  if (["cancelling", "cancelled"].includes(uploadProgress.value.phase)) {
    return "exception";
  }
  if (uploadProgress.value.phase === "paused") {
    return "warning";
  }
  return uploadProgress.value.percent >= 100 ? "success" : undefined;
});

async function refreshAll() {
  await Promise.all([fetchDefaultModel(), fetchModels()]);
}

async function fetchDefaultModel() {
  loadingDefault.value = true;
  try {
    defaultInfo.value = await getDefaultModel();
  } catch (error) {
    console.error("获取默认模型失败", error);
  } finally {
    loadingDefault.value = false;
  }
}

async function fetchModels() {
  loadingModels.value = true;
  try {
    const res = await getModels({
      model_name: filters.value.modelName || undefined,
      version: filters.value.version || undefined,
      scene_id: filters.value.sceneId || undefined,
      model_type: filters.value.modelType || undefined,
      source_type: filters.value.sourceType || "all",
    });
    modelList.value = res.items || res.models || [];
  } catch (error) {
    console.error("获取模型列表失败", error);
  } finally {
    loadingModels.value = false;
  }
}

function resetFilters() {
  filters.value = {
    modelName: "",
    version: "",
    sceneId: null,
    modelType: "",
    sourceType: "all",
  };
  fetchModels();
}

function openUploadDialog() {
  showUploadDialog.value = true;
  if (!uploadForm.value.sceneId && defaultScene.value?.id) {
    uploadForm.value.sceneId = defaultScene.value.id;
  }
}

function beforeUploadDialogClose(done) {
  if (uploading.value && uploadProgress.value.phase !== "completed") {
    ElMessage.warning("上传进行中，请先取消上传");
    return;
  }
  done();
}

function resetUploadForm() {
  if (uploading.value) return;
  uploadForm.value = createDefaultUploadForm();
  if (defaultScene.value?.id) {
    uploadForm.value.sceneId = defaultScene.value.id;
  }
  selectedModelFile.value = null;
  modelFileList.value = [];
  uploadProgress.value = createUploadProgress();
  uploadPaused.value = false;
  cancelingUpload.value = false;
}

function onModelFileChange(file) {
  if (!validateModelFileExtension(file.name, uploadForm.value.modelType)) {
    ElMessage.warning(`${uploadForm.value.modelType} 只支持 ${acceptedModelExtensions.value}`);
    selectedModelFile.value = null;
    modelFileList.value = [];
    return false;
  }
  selectedModelFile.value = file.raw;
  modelFileList.value = [file];
  const stem = file.name.replace(/\.[^.]+$/g, "");
  if (!uploadForm.value.modelName) {
    uploadForm.value.modelName = stem;
  }
  if (!uploadForm.value.version) {
    uploadForm.value.version = `upload-${new Date().toISOString().slice(0, 10)}`;
  }
  uploadProgress.value = createUploadProgress(true);
}

function onModelFileRemove() {
  selectedModelFile.value = null;
  modelFileList.value = [];
  uploadProgress.value = createUploadProgress();
}

function handleUploadAction() {
  if (uploading.value) {
    cancelUpload();
    return;
  }
  submitUpload();
}

function bindUploadController(controller) {
  unsubscribeUploadController?.();
  uploadController = controller;
  unsubscribeUploadController = controller.onStateChange((state) => {
    uploadPaused.value = state.paused;
  });
}

function clearUploadController() {
  unsubscribeUploadController?.();
  unsubscribeUploadController = null;
  uploadController = null;
  uploadPaused.value = false;
  cancelingUpload.value = false;
}

function toggleUploadPause() {
  if (!uploadController || cancelingUpload.value) return;
  if (uploadPaused.value) {
    uploadController.resume();
    return;
  }
  uploadController.pause();
}

function cancelUpload() {
  if (!uploadController || cancelingUpload.value) return;
  cancelingUpload.value = true;
  uploadProgress.value = {
    ...uploadProgress.value,
    visible: true,
    phase: "cancelling",
    remainingSeconds: null,
  };
  uploadController.cancel();
}

async function submitUpload() {
  if (!canStartUpload.value) {
    ElMessage.warning("请填写模型信息并选择符合后缀要求的文件");
    return;
  }
  const controller = createModelUploadController();
  bindUploadController(controller);
  uploading.value = true;
  cancelingUpload.value = false;
  uploadProgress.value = createUploadProgress(true);
  try {
    await uploadModel({
      sceneId: uploadForm.value.sceneId,
      sceneName: "chest_xray",
      modelName: uploadForm.value.modelName.trim(),
      version: uploadForm.value.version.trim(),
      modelType: uploadForm.value.modelType,
      description: uploadForm.value.description.trim() || null,
      file: selectedModelFile.value,
      onProgress: updateUploadProgress,
      controller,
    });
    ElMessage.success("模型上传成功");
    showUploadDialog.value = false;
    await refreshAll();
  } catch (error) {
    if (error instanceof ModelUploadCancelledError || error?.code === "UPLOAD_CANCELLED") {
      uploadProgress.value = {
        ...uploadProgress.value,
        visible: true,
        phase: "cancelled",
        remainingSeconds: null,
      };
      ElMessage.info("上传已取消");
      await fetchModels();
    } else {
      ElMessage.error(error.response?.data?.detail || "模型上传失败");
    }
  } finally {
    uploading.value = false;
    clearUploadController();
  }
}

function openConfig(row) {
  configModel.value = row;
  showConfigDialog.value = true;
}

async function confirmSetDefault() {
  if (!configModel.value || configModel.value.is_default) return;
  settingDefaultId.value = configModel.value.id;
  try {
    await setDefaultModel(configModel.value.id, {
      scene_id: configModel.value.scene_id,
    });
    ElMessage.success("默认推理模型已更新");
    showConfigDialog.value = false;
    await refreshAll();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "配置失败");
  } finally {
    settingDefaultId.value = null;
  }
}

async function confirmDelete(row) {
  if (row.is_default) {
    ElMessage.warning("当前默认推理模型需要先配置其他模型替换后再删除");
    return;
  }
  const cascade = row.source_type === "trained";
  try {
    await ElMessageBox.confirm(
      cascade
        ? `模型 ${row.model_name} 来源于训练任务，删除会同时删除源训练记录和 OSS 对象。确定继续吗？`
        : `确定删除模型 ${row.model_name} 吗？`,
      "确认删除",
      {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
      },
    );
    await deleteModel(row.id, { cascade });
    ElMessage.success("模型已删除");
    await refreshAll();
  } catch (error) {
    if (error !== "cancel") {
      ElMessage.error(error.response?.data?.detail || "删除失败");
    }
  }
}

async function downloadWeight(row) {
  try {
    const res = await getModelDownloadUrl(row.id);
    const link = document.createElement("a");
    link.href = res.url;
    link.download = res.filename || `${row.model_name}-${row.version}.pt`;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "下载链接获取失败");
  }
}

function openTrainingSource(row) {
  const taskUuid = row.source_training?.task_uuid || row.source_label;
  const href =
    row.source_training?.url ||
    router.resolve({ path: "/training", query: { task_uuid: taskUuid } }).href;
  window.open(href, "_blank", "noopener");
}

function createDefaultUploadForm() {
  return {
    sceneId: null,
    modelName: "",
    version: "",
    modelType: "yolo11n",
    description: "",
  };
}

function createUploadProgress(visible = false) {
  const totalBytes = selectedModelFile.value?.size || 0;
  const totalParts = totalBytes
    ? Math.ceil(totalBytes / MODEL_UPLOAD_PART_SIZE)
    : 0;
  return {
    visible,
    phase: "idle",
    uploadedParts: 0,
    totalParts,
    uploadedBytes: 0,
    totalBytes,
    percent: 0,
    speedBytesPerSecond: 0,
    remainingSeconds: null,
  };
}

function updateUploadProgress(progress) {
  uploadProgress.value = {
    visible: true,
    ...progress,
  };
}

function sourceText(model) {
  if (!model) return "-";
  return model.source_type === "trained"
    ? `训练任务 ${model.source_label || "-"}`
    : "上传";
}

function cacheStatusText(status) {
  const map = {
    synced: "已同步",
    syncing: "同步中",
    failed: "同步异常",
    unknown: "未知",
  };
  return map[status] || "未知";
}

function cacheTagType(status) {
  if (status === "synced") return "success";
  if (status === "failed") return "danger";
  if (status === "syncing") return "warning";
  return "info";
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!size) return "0 MB";
  if (size >= 1024 * 1024 * 1024) {
    return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function formatSpeed(value) {
  if (!value) return "-";
  return `${formatBytes(value)}/s`;
}

function formatDuration(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  const seconds = Math.max(Math.ceil(value), 0);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m ${rest}s`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

onMounted(refreshAll);
</script>

<style scoped>
.model-page {
  padding: 20px;
}

.page-header {
  align-items: center;
  display: flex;
  gap: 16px;
  justify-content: space-between;
  margin-bottom: 20px;
}

.page-header h2 {
  color: #303133;
  font-size: 22px;
  margin: 0;
}

.header-meta {
  color: #909399;
  display: flex;
  font-size: 13px;
  gap: 14px;
  margin-top: 6px;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.default-card,
.models-card {
  margin-bottom: 20px;
}

.card-header {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
}

.default-model-grid {
  align-items: center;
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(220px, 1.6fr) repeat(5, minmax(120px, 1fr));
}

.default-model-main {
  align-items: center;
  display: flex;
  gap: 12px;
  min-width: 0;
}

.default-model-icon {
  background: #ecf5ff;
  border-radius: 6px;
  color: #409eff;
  font-size: 24px;
  height: 42px;
  justify-content: center;
  width: 42px;
}

.default-model-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.default-model-title strong,
.default-model-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.default-model-title span,
.detail-label,
.cache-time {
  color: #909399;
  font-size: 12px;
}

.default-detail {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.upload-band {
  align-items: center;
  border-bottom: 1px solid #ebeef5;
  border-top: 1px solid #ebeef5;
  display: flex;
  justify-content: flex-end;
  margin-bottom: 20px;
  padding: 14px 0;
}

.filters {
  display: grid;
  gap: 10px;
  grid-template-columns:
    minmax(150px, 1.2fr) minmax(120px, 1fr) minmax(140px, 1fr)
    minmax(130px, 1fr) minmax(120px, 1fr) auto auto;
  margin-bottom: 14px;
}

.selected-upload-file {
  align-items: center;
  display: grid;
  gap: 10px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  min-height: 92px;
  padding: 0 18px;
  width: 100%;
}

.selected-file-name {
  color: #303133;
  font-size: 14px;
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-icon {
  color: #909399;
  font-size: 32px;
  margin-bottom: 8px;
}

.upload-progress {
  border-top: 1px solid #ebeef5;
  margin-top: 8px;
  padding-top: 14px;
}

.progress-meta {
  color: #606266;
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 12px;
  justify-content: space-between;
  margin-top: 8px;
}

.progress-parts {
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}

.upload-footer {
  align-items: center;
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

.cancel-upload-button {
  background: #fff;
  border-color: #f56c6c;
  color: #f56c6c;
}

.cancel-upload-button:hover,
.cancel-upload-button:focus {
  background: #fef0f0;
  border-color: #f56c6c;
  color: #f56c6c;
}

.config-body {
  margin-top: 4px;
}

@media (max-width: 1100px) {
  .default-model-grid,
  .filters {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 768px) {
  .page-header,
  .header-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .default-model-grid,
  .filters {
    grid-template-columns: 1fr;
  }
}
</style>
