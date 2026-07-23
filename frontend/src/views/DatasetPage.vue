<template>
  <div class="dataset-page">
    <div class="page-header">
      <div>
        <h2>数据集管理</h2>
        <div class="header-meta">
          <span>{{ datasetList.length }} 个数据集</span>
          <span>{{ readyDatasets }} 个可训练</span>
        </div>
      </div>
      <div class="header-actions">
        <el-button @click="fetchDatasets" :loading="loading">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="primary" @click="openUploadDialog">
          <el-icon><Upload /></el-icon>上传数据集
        </el-button>
      </div>
    </div>

    <el-card class="dataset-table-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>数据集列表</span>
          <el-input
            v-model="keyword"
            class="search-input"
            clearable
            placeholder="搜索数据集"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
      </template>

      <el-table
        :data="filteredDatasets"
        stripe
        v-loading="loading"
        empty-text="暂无数据集"
      >
        <el-table-column prop="name" label="数据集" min-width="180">
          <template #default="{ row }">
            <div class="dataset-name-cell">
              <el-icon><FolderOpened /></el-icon>
              <span>{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="文件大小" width="130" sortable>
          <template #default="{ row }">
            {{ formatBytes(row.actual_size || row.expected_size) }}
          </template>
        </el-table-column>
        <el-table-column label="OSS 对象" min-width="320" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="path-text">oss://{{ row.bucket }}/{{ row.raw_object_key }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.updated_at || row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="danger"
              text
              @click="confirmDelete(row)"
            >
              <el-icon><Delete /></el-icon>删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="showUploadDialog"
      title="上传数据集"
      width="560px"
      :close-on-click-modal="false"
      :show-close="!uploading"
      :before-close="beforeUploadDialogClose"
      @closed="resetUploadForm"
    >
      <el-form label-width="96px">
        <el-form-item label="数据集名称">
          <el-input
            v-model="uploadForm.datasetName"
            :disabled="uploading"
            maxlength="64"
            show-word-limit
            placeholder="chest_xray_v2"
          />
        </el-form-item>
        <el-form-item label="ZIP 文件">
          <el-upload
            drag
            accept=".zip"
            :auto-upload="false"
            :limit="1"
            :file-list="fileList"
            :show-file-list="false"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
            :disabled="uploading"
          >
            <div v-if="selectedFile" class="selected-upload-file">
              <el-icon><Document /></el-icon>
              <span class="selected-file-name" :title="selectedFile.name">
                {{ selectedFile.name }}
              </span>
              <el-button
                v-if="!uploading"
                text
                type="danger"
                @click.stop="onFileRemove"
              >
                移除
              </el-button>
            </div>
            <template v-else>
              <el-icon class="upload-icon"><UploadFilled /></el-icon>
              <div class="el-upload__text">拖入或选择 ZIP 文件</div>
            </template>
          </el-upload>
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
  </div>
</template>

<script setup>
import {
  DATASET_UPLOAD_PART_SIZE,
  DatasetUploadCancelledError,
  createDatasetUploadController,
  deleteDataset,
  getDatasets,
  uploadDataset,
} from "@/api/dataset";
import {
  CircleClose,
  Delete,
  Document,
  FolderOpened,
  Refresh,
  Search,
  Upload,
  UploadFilled,
  VideoPause,
  VideoPlay,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const datasetList = ref([]);
const keyword = ref("");
const loading = ref(false);
const showUploadDialog = ref(false);
const uploading = ref(false);
const cancelingUpload = ref(false);
const uploadPaused = ref(false);
const uploadForm = ref({
  datasetName: "",
});
const selectedFile = ref(null);
const fileList = ref([]);
const uploadProgress = ref(createUploadProgress());
let uploadController = null;
let unsubscribeUploadController = null;

const filteredDatasets = computed(() => {
  const text = keyword.value.trim().toLowerCase();
  if (!text) return datasetList.value;
  return datasetList.value.filter((item) =>
    item.name.toLowerCase().includes(text),
  );
});

const readyDatasets = computed(() =>
  datasetList.value.filter((item) => item.status === "UPLOADED").length,
);

const canStartUpload = computed(
  () => Boolean(uploadForm.value.datasetName.trim()) && Boolean(selectedFile.value),
);

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

async function fetchDatasets() {
  loading.value = true;
  try {
    const res = await getDatasets();
    datasetList.value = res.datasets || [];
  } catch (e) {
    console.error("获取数据集失败", e);
  } finally {
    loading.value = false;
  }
}

function openUploadDialog() {
  showUploadDialog.value = true;
}

function onFileChange(file) {
  if (!String(file.name || "").toLowerCase().endsWith(".zip")) {
    ElMessage.warning("数据集文件必须是 .zip");
    selectedFile.value = null;
    fileList.value = [];
    return false;
  }
  selectedFile.value = file.raw;
  fileList.value = [file];
  if (!uploadForm.value.datasetName && file.name) {
    uploadForm.value.datasetName = file.name.replace(/\.zip$/i, "");
  }
  uploadProgress.value = createUploadProgress(true);
}

function onFileRemove() {
  selectedFile.value = null;
  fileList.value = [];
  uploadProgress.value = createUploadProgress();
}

function resetUploadForm() {
  if (uploading.value) return;
  uploadForm.value.datasetName = "";
  selectedFile.value = null;
  fileList.value = [];
  uploadProgress.value = createUploadProgress();
  uploadPaused.value = false;
  cancelingUpload.value = false;
}

function beforeUploadDialogClose(done) {
  if (uploading.value && uploadProgress.value.phase !== "completed") {
    ElMessage.warning("上传进行中，请先取消上传");
    return;
  }
  done();
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
  const datasetName = uploadForm.value.datasetName.trim();
  if (!/^[A-Za-z0-9_-]+$/.test(datasetName)) {
    ElMessage.warning("数据集名称仅支持字母、数字、下划线、连字符");
    return;
  }
  if (!selectedFile.value) {
    ElMessage.warning("请选择 ZIP 文件");
    return;
  }

  const controller = createDatasetUploadController();
  bindUploadController(controller);
  uploading.value = true;
  cancelingUpload.value = false;
  uploadProgress.value = createUploadProgress(true);
  try {
    await uploadDataset({
      datasetName,
      file: selectedFile.value,
      onProgress: updateUploadProgress,
      controller,
    });
    ElMessage.success("数据集上传成功");
    showUploadDialog.value = false;
    await fetchDatasets();
  } catch (e) {
    if (e instanceof DatasetUploadCancelledError || e?.code === "UPLOAD_CANCELLED") {
      uploadProgress.value = {
        ...uploadProgress.value,
        visible: true,
        phase: "cancelled",
        remainingSeconds: null,
      };
      ElMessage.info("上传已取消");
      await fetchDatasets();
    } else {
      ElMessage.error(e.response?.data?.detail || "上传失败");
    }
  } finally {
    uploading.value = false;
    clearUploadController();
  }
}

async function confirmDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除数据集 ${row.name} 吗？`,
      "确认删除",
      {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
      },
    );
    await deleteDataset(row.upload_id || row.dataset_id || row.name);
    ElMessage.success("数据集已删除");
    await fetchDatasets();
  } catch (e) {
    if (e !== "cancel") {
      ElMessage.error(e.response?.data?.detail || "删除失败");
    }
  }
}

function createUploadProgress(visible = false) {
  const totalBytes = selectedFile.value?.size || 0;
  const totalParts = totalBytes
    ? Math.ceil(totalBytes / DATASET_UPLOAD_PART_SIZE)
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

function statusText(status) {
  const map = {
    INITIATED: "已创建",
    UPLOADING: "上传中",
    UPLOADED: "已上传",
    FAILED: "失败",
    EXPIRED: "已过期",
    CANCELLED: "已删除",
  };
  return map[status] || status || "-";
}

function statusType(status) {
  if (status === "UPLOADED") return "success";
  if (["FAILED", "EXPIRED", "CANCELLED"].includes(status)) return "danger";
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

function openUploadDialogFromQuery() {
  if (route.query.openUpload === "1") {
    openUploadDialog();
  }
}

watch(
  () => route.query.openUpload,
  () => openUploadDialogFromQuery(),
);

onMounted(() => {
  fetchDatasets();
  openUploadDialogFromQuery();
});
</script>

<style lang="scss" scoped>
.dataset-page {
  padding: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 22px;
  color: #303133;
}

.header-meta {
  display: flex;
  gap: 14px;
  margin-top: 6px;
  color: #909399;
  font-size: 13px;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.dataset-table-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.search-input {
  width: 260px;
}

.dataset-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #303133;
}

.path-text {
  color: #606266;
  font-family:
    ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
    "Courier New", monospace;
  font-size: 12px;
}

.upload-icon {
  color: #909399;
  font-size: 32px;
  margin-bottom: 8px;
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

.upload-progress {
  border-top: 1px solid #ebeef5;
  margin-top: 8px;
  padding-top: 14px;
}

.progress-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.progress-meta {
  color: #606266;
  flex-wrap: wrap;
  font-size: 12px;
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

@media (max-width: 768px) {
  .page-header,
  .card-header {
    align-items: stretch;
    flex-direction: column;
  }

  .header-actions,
  .search-input {
    width: 100%;
  }

  .header-actions .el-button {
    flex: 1;
  }

  .upload-footer {
    align-items: stretch;
    flex-direction: column;
  }

}
</style>
