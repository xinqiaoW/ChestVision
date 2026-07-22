<template>
  <div class="dataset-page">
    <div class="page-header">
      <div>
        <h2>数据集管理</h2>
        <div class="header-meta">
          <span>{{ datasetList.length }} 个数据集</span>
          <span>{{ totalImages }} 张图片</span>
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
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.has_data ? 'success' : 'warning'" size="small">
              {{ row.has_data ? "可训练" : "未就绪" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="train_count"
          label="训练集"
          width="110"
          sortable
        />
        <el-table-column prop="val_count" label="验证集" width="110" sortable />
        <el-table-column
          prop="total_count"
          label="总图片"
          width="110"
          sortable
        />
        <el-table-column label="路径" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="path-text">datasets/{{ row.name }}/yolo_dataset</span>
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
      <template #footer>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">
          上传
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { deleteDataset, getDatasets, uploadDataset } from "@/api/dataset";
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

const filteredDatasets = computed(() => {
  const text = keyword.value.trim().toLowerCase();
  if (!text) return datasetList.value;
  return datasetList.value.filter((item) =>
    item.name.toLowerCase().includes(text),
  );
});

const totalImages = computed(() =>
  datasetList.value.reduce((sum, item) => sum + (item.total_count || 0), 0),
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
}

function onFileRemove() {
  selectedFile.value = null;
  fileList.value = [];
}

function resetUploadForm() {
  uploadForm.value.datasetName = "";
  selectedFile.value = null;
  fileList.value = [];
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
  try {
    await uploadDataset({
      datasetName,
      file: selectedFile.value,
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
    await deleteDataset(row.name);
    ElMessage.success("数据集已删除");
    await fetchDatasets();
  } catch (e) {
    if (e !== "cancel") {
      ElMessage.error(e.response?.data?.detail || "删除失败");
    }
  }
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
}
</style>
