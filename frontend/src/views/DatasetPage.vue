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
      @closed="resetUploadForm"
    >
      <el-form label-width="96px">
        <el-form-item label="数据集名称">
          <el-input
            v-model="uploadForm.datasetName"
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
            :on-change="onFileChange"
            :on-remove="onFileRemove"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="el-upload__text">拖入或选择 ZIP 文件</div>
          </el-upload>
        </el-form-item>
      </el-form>
      <div v-if="uploadProgress.visible" class="upload-progress">
        <div class="progress-title">
          <span>OSS 分片上传</span>
          <span>{{ uploadProgress.phaseText }}</span>
        </div>
        <div class="progress-stats">
          <div class="progress-stat">
            <span class="stat-label">总分片</span>
            <span class="stat-value">{{ uploadProgress.totalParts }} 片</span>
          </div>
          <div class="progress-stat">
            <span class="stat-label">已上传</span>
            <span class="stat-value">
              {{ uploadProgress.uploadedParts }}/{{ uploadProgress.totalParts }} 片
            </span>
          </div>
          <div class="progress-stat">
            <span class="stat-label">当前分片</span>
            <span class="stat-value">
              {{
                uploadProgress.currentPartNumber
                  ? `第 ${uploadProgress.currentPartNumber} 片`
                  : "-"
              }}
            </span>
          </div>
          <div class="progress-stat">
            <span class="stat-label">分片大小</span>
            <span class="stat-value">{{ formatBytes(uploadProgress.partSize) }}</span>
          </div>
        </div>
        <el-progress
          :percentage="Math.round(uploadProgress.percent)"
          :status="uploadProgress.percent >= 100 ? 'success' : undefined"
        />
        <div class="progress-meta">
          <span>
            {{ formatBytes(uploadProgress.uploadedBytes) }} /
            {{ formatBytes(uploadProgress.totalBytes) }}
          </span>
          <span>速度 {{ formatSpeed(uploadProgress.speedBytesPerSecond) }}</span>
          <span>预计剩余 {{ formatDuration(uploadProgress.remainingSeconds) }}</span>
        </div>
      </div>
      <template #footer>
        <el-button :disabled="uploading" @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">
          分片上传
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import {
  DATASET_UPLOAD_PART_SIZE,
  deleteDataset,
  getDatasets,
  uploadDataset,
} from "@/api/dataset";
import {
  Delete,
  FolderOpened,
  Refresh,
  Search,
  Upload,
  UploadFilled,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

const datasetList = ref([]);
const keyword = ref("");
const loading = ref(false);
const showUploadDialog = ref(false);
const uploading = ref(false);
const uploadForm = ref({
  datasetName: "",
});
const selectedFile = ref(null);
const fileList = ref([]);
const uploadProgress = ref(createUploadProgress());

const filteredDatasets = computed(() => {
  const text = keyword.value.trim().toLowerCase();
  if (!text) return datasetList.value;
  return datasetList.value.filter((item) =>
    item.name.toLowerCase().includes(text),
  );
});

const readyDatasets = computed(() =>
  datasetList.value.filter((item) => ["UPLOADED", "READY"].includes(item.status))
    .length,
);

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

  uploading.value = true;
  uploadProgress.value = createUploadProgress(true);
  try {
    await uploadDataset({
      datasetName,
      file: selectedFile.value,
      onProgress: updateUploadProgress,
    });
    ElMessage.success("数据集上传成功");
    showUploadDialog.value = false;
    await fetchDatasets();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "上传失败");
  } finally {
    uploading.value = false;
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
    phaseText: "等待上传",
    currentPartNumber: null,
    uploadedParts: 0,
    totalParts,
    partSize: DATASET_UPLOAD_PART_SIZE,
    uploadedBytes: 0,
    totalBytes,
    percent: 0,
    speedBytesPerSecond: 0,
    remainingSeconds: null,
  };
}

function updateUploadProgress(progress) {
  const phaseTextMap = {
    signing: "签发分片 URL",
    uploading: progress.currentPartNumber
      ? `上传第 ${progress.currentPartNumber} 片`
      : "上传中",
    finalizing: "合并分片",
    completed: "上传完成",
  };
  uploadProgress.value = {
    visible: true,
    partSize: uploadProgress.value.partSize || DATASET_UPLOAD_PART_SIZE,
    ...progress,
    phaseText: phaseTextMap[progress.phase] || "上传中",
  };
}

function statusText(status) {
  const map = {
    INITIATED: "已创建",
    UPLOADING: "上传中",
    CLIENT_COMPLETED: "等待确认",
    UPLOADED: "已上传",
    READY: "可训练",
    FAILED: "失败",
    EXPIRED: "已过期",
    CANCELLED: "已删除",
  };
  return map[status] || status || "-";
}

function statusType(status) {
  if (["UPLOADED", "READY"].includes(status)) return "success";
  if (["FAILED", "EXPIRED", "CANCELLED"].includes(status)) return "danger";
  if (status === "CLIENT_COMPLETED") return "warning";
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

onMounted(fetchDatasets);
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

.upload-progress {
  border-top: 1px solid #ebeef5;
  margin-top: 8px;
  padding-top: 14px;
}

.progress-title,
.progress-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.progress-title {
  color: #303133;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.progress-stats {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-bottom: 12px;
}

.progress-stat {
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  min-width: 0;
  padding: 8px;
}

.stat-label,
.stat-value {
  display: block;
  min-width: 0;
}

.stat-label {
  color: #909399;
  font-size: 12px;
}

.stat-value {
  color: #303133;
  font-size: 13px;
  font-weight: 600;
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-meta {
  color: #606266;
  flex-wrap: wrap;
  font-size: 12px;
  margin-top: 8px;
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

  .progress-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
