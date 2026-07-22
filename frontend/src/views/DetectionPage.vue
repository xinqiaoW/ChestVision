<template>
  <div class="detection-page">
    <div class="page-header">
      <div>
        <h2>检测工作台</h2>
        <span class="page-subtitle">Detection</span>
      </div>
    </div>

    <div class="detection-layout">
      <!-- 左侧：上传区 -->
      <div class="upload-panel">
        <el-card shadow="hover">
          <template #header>
            <span>📤 上传胸片</span>
          </template>

          <!-- 拖拽上传区 -->
          <div
            class="upload-area"
            :class="{ 'is-dragover': isDragover }"
            @dragover.prevent="isDragover = true"
            @dragleave.prevent="isDragover = false"
            @drop.prevent="handleDrop"
            @click="triggerUpload"
          >
            <input
              ref="fileInput"
              type="file"
              accept="image/*"
              style="display: none"
              @change="handleFileChange"
            />
            <template v-if="!previewUrl">
              <el-icon :size="48" color="#909399"><UploadFilled /></el-icon>
              <p>点击或拖拽胸片图像到此处</p>
              <p class="upload-hint">支持 JPG / PNG / BMP / TIFF</p>
            </template>
            <img v-else :src="previewUrl" class="preview-image" />
          </div>

          <!-- 参数设置 -->
          <div class="detect-params">
            <div class="param-item">
              <span>置信度阈值：{{ confThreshold }}</span>
              <el-slider
                v-model="confThreshold"
                :min="0.1"
                :max="0.9"
                :step="0.05"
                show-input
              />
            </div>
            <div class="param-item">
              <span>检测模型</span>
              <el-select
                v-model="selectedModelId"
                placeholder="选择模型（默认自动）"
                style="width: 100%"
                clearable
              >
                <el-option
                  v-for="m in modelList"
                  :key="m.id"
                  :label="`${m.model_name} (mAP50: ${m.map50 ? (m.map50 * 100).toFixed(1) + '%' : '-'})${m.is_default ? ' ★' : ''}`"
                  :value="m.id"
                />
              </el-select>
            </div>
            <div
              v-if="
                userStore.userType === 'doctor' ||
                userStore.userType === 'admin'
              "
              class="param-item"
            >
              <span>关联患者（用于病史分析与医生匹配）</span>
              <el-select
                v-model="selectedPatientId"
                placeholder="选择患者（可选）"
                style="width: 100%"
                clearable
              >
                <el-option
                  v-for="patient in patientList"
                  :key="patient.id"
                  :label="`${patient.patient_code} ${patient.real_name || patient.username}`"
                  :value="patient.id"
                />
              </el-select>
            </div>
          </div>

          <!-- 检测按钮 -->
          <el-button
            type="primary"
            size="large"
            :loading="detecting"
            :disabled="!selectedFile"
            class="detect-btn"
            @click="startDetect"
          >
            {{ detecting ? "AI 检测中..." : "🔍 开始检测" }}
          </el-button>
        </el-card>
      </div>

      <!-- 右侧：结果区 -->
      <div class="result-panel">
        <!-- 加载状态 -->
        <el-card v-if="detecting" shadow="hover">
          <div class="loading-box">
            <el-icon :size="40" class="is-loading"><Loading /></el-icon>
            <p>AI 正在分析胸片...</p>
          </div>
        </el-card>

        <!-- 检测结果 -->
        <template v-if="detectResult && !detecting">
          <!-- 统计卡片 -->
          <el-row :gutter="16" class="stats-row">
            <el-col :span="8">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">{{ detectResult.total_objects }}</div>
                <div class="stat-label">检出病灶数</div>
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">
                  {{ detectResult.inference_time_ms }}ms
                </div>
                <div class="stat-label">推理耗时</div>
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card shadow="hover" class="stat-card">
                <div class="stat-value">{{ detectResult.image_size }}</div>
                <div class="stat-label">图像尺寸</div>
              </el-card>
            </el-col>
          </el-row>

          <!-- 标注图像 -->
          <el-card shadow="hover" class="annotated-card">
            <template #header>
              <span>📸 检测结果可视化</span>
            </template>
            <img
              v-if="annotatedImageUrl"
              :src="annotatedImageUrl"
              class="annotated-image"
              @error="handleImageError"
            />
            <el-empty v-else description="标注图像加载中..." />
          </el-card>

          <!-- 病灶列表 -->
          <el-card
            v-if="detectResult.objects.length > 0"
            shadow="hover"
            class="result-table-card"
          >
            <template #header>
              <span>📋 病灶详情列表</span>
            </template>
            <el-table
              :data="detectResult.objects"
              stripe
              style="width: 100%"
              max-height="400"
            >
              <el-table-column type="index" label="#" width="50" />
              <el-table-column prop="class_name_cn" label="类别" width="100">
                <template #default="{ row }">
                  <el-tag
                    :style="{
                      backgroundColor: getClassColor(row.class_name),
                      color: '#fff',
                    }"
                  >
                    {{ row.class_name_cn }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="class_name" label="英文名" width="130" />
              <el-table-column prop="confidence" label="置信度" width="100">
                <template #default="{ row }">
                  <el-progress
                    :percentage="Math.round(row.confidence * 100)"
                    :color="getConfidenceColor(row.confidence)"
                    :stroke-width="8"
                  />
                </template>
              </el-table-column>
              <el-table-column label="边界框 (x1,y1,x2,y2)" min-width="200">
                <template #default="{ row }">
                  <code>{{ row.bbox.join(", ") }}</code>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- AI 病史感知分析（v3.0）-->
          <el-card
            v-if="detectResult.ai_analysis?.report"
            shadow="hover"
            class="analysis-card"
          >
            <template #header>
              <div class="analysis-header">
                <span>🤖 AI 综合分析</span>
                <el-tag
                  v-if="detectResult.ai_analysis.risk_level"
                  :type="riskTagType(detectResult.ai_analysis.risk_level)"
                  size="small"
                >
                  {{ riskLabel(detectResult.ai_analysis.risk_level) }}
                </el-tag>
              </div>
            </template>
            <div
              class="analysis-content"
              v-html="renderMarkdown(detectResult.ai_analysis.report)"
            ></div>
          </el-card>

          <!-- 无病灶提示 -->
          <el-result
            v-if="detectResult.objects.length === 0"
            icon="success"
            title="未检测到明显病灶"
            sub-title="该胸片未见明显异常，建议结合临床症状综合判断"
          />
        </template>

        <!-- 初始空状态 -->
        <el-empty
          v-if="!detectResult && !detecting"
          description="请上传胸片图像并点击「开始检测」"
        />
      </div>
    </div>

    <DoctorRecommendationDialog
      v-model="recommendationVisible"
      :task-id="recommendationTaskId"
      :patient-profile-id="selectedPatientId"
      :session-id="agentStore.currentSessionId"
    />
  </div>
</template>

<script setup>
import { getPatients } from "@/api/patient";
import DoctorRecommendationDialog from "@/components/DoctorRecommendationDialog.vue";
import { useAgentStore } from "@/stores/agent";
import { useUserStore } from "@/stores/user";
import { Loading, UploadFilled } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { onMounted, ref } from "vue";

// ── 上传相关 ──
const fileInput = ref(null);
const selectedFile = ref(null);
const previewUrl = ref("");
const isDragover = ref(false);

// ── 检测参数 ──
const confThreshold = ref(0.25);
const detecting = ref(false);

// ── 模型选择 ──
const modelList = ref([]);
const selectedModelId = ref(null);
const selectedPatientId = ref(null);
const patientList = ref([]);
const recommendationVisible = ref(false);
const recommendationTaskId = ref(null);
const userStore = useUserStore();
const agentStore = useAgentStore();

// ── 检测结果 ──
const detectResult = ref(null);
const annotatedImageUrl = ref("");

// ── 类别颜色 ──
const classColors = {
  Atelectasis: "#E67E22",
  Calcification: "#F1C40F",
  Consolidation: "#D35400",
  Effusion: "#3498DB",
  Emphysema: "#9B59B6",
  Fibrosis: "#1ABC9C",
  Fracture: "#E74C3C",
  Mass: "#E74C3C",
  Nodule: "#27AE60",
  Pneumothorax: "#00BCD4",
};

function getClassColor(className) {
  return classColors[className] || "#95A5A6";
}

function getConfidenceColor(confidence) {
  if (confidence >= 0.7) return "#27AE60"; // 绿色：高置信度
  if (confidence >= 0.4) return "#F39C12"; // 橙色：中等置信度
  return "#E74C3C"; // 红色：低置信度
}

function riskTagType(level) {
  const map = {
    low: "success",
    medium: "warning",
    high: "danger",
    critical: "danger",
  };
  return map[level] || "info";
}
function riskLabel(level) {
  const map = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    critical: "⚠️ 危急",
  };
  return map[level] || level;
}
function renderMarkdown(text) {
  if (!text) return "";
  return text
    .replace(/### (.+)/g, "<h4>$1</h4>")
    .replace(/## (.+)/g, "<h3>$1</h3>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br>");
}

// ── 触发文件选择 ──
function triggerUpload() {
  fileInput.value?.click();
}

// ── 处理文件选择 ──
function handleFileChange(e) {
  const file = e.target.files?.[0];
  if (file) setFile(file);
}

// ── 处理拖拽 ──
function handleDrop(e) {
  isDragover.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) setFile(file);
}

function setFile(file) {
  if (!file.type.startsWith("image/")) {
    ElMessage.warning("请选择图像文件");
    return;
  }
  selectedFile.value = file;
  detectResult.value = null;
  annotatedImageUrl.value = "";

  // 预览
  const reader = new FileReader();
  reader.onload = (e) => {
    previewUrl.value = e.target?.result || "";
  };
  reader.readAsDataURL(file);
}

// ── 加载可用模型列表 ──
async function fetchModels() {
  try {
    const { default: request } = await import("@/utils/request");
    const res = await request.get("/detection/models");
    modelList.value = res.models || [];
    // 自动选中默认模型
    const defaultModel = modelList.value.find((m) => m.is_default);
    if (defaultModel) {
      selectedModelId.value = defaultModel.id;
    }
  } catch {
    ElMessage.warning("模型列表加载失败，将使用默认模型");
  }
}

async function fetchPatients() {
  if (userStore.userType !== "doctor" && userStore.userType !== "admin") return;
  try {
    const result = await getPatients();
    patientList.value = result.items || [];
  } catch {
    patientList.value = [];
    ElMessage.warning("患者列表加载失败");
  }
}

onMounted(() => {
  fetchModels();
  fetchPatients();
});

// ── 开始检测 ──
async function startDetect() {
  if (!selectedFile.value) return;

  detecting.value = true;
  detectResult.value = null;
  annotatedImageUrl.value = "";

  try {
    const formData = new FormData();
    formData.append("file", selectedFile.value);

    const { default: request } = await import("@/utils/request");
    const params = { conf_threshold: confThreshold.value };
    if (selectedModelId.value) {
      params.model_version_id = selectedModelId.value;
    }
    if (selectedPatientId.value) {
      params.patient_profile_id = selectedPatientId.value;
    }
    const res = await request.post("/detection/detect", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      params,
      timeout: 60000,
    });

    detectResult.value = res;

    // 通过 axios 获取标注图（自动携带 token）
    if (res.annotated_image_url) {
      loadAnnotatedImage(res.annotated_image_url);
    }

    if (res.total_objects > 0) {
      ElMessage.success(`检测完成，发现 ${res.total_objects} 个病灶`);
      recommendationTaskId.value = res.task_id;
      recommendationVisible.value = true;
    } else {
      ElMessage.success("检测完成，未发现明显病灶");
    }
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || "检测请求失败");
  } finally {
    detecting.value = false;
  }
}

async function loadAnnotatedImage(url) {
  try {
    // 用原生 fetch 避免 axios 拦截器的 404 弹窗和 data 解包问题
    const token = localStorage.getItem("chestx_token");
    const fullUrl = url.startsWith("http")
      ? url
      : `${window.location.origin}${url}`;
    const response = await fetch(fullUrl, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      return;
    }
    const blob = await response.blob();
    annotatedImageUrl.value = URL.createObjectURL(blob);
  } catch {
    /* 标注图非关键，静默失败 */
  }
}

function handleImageError() {
  annotatedImageUrl.value = "";
  ElMessage.warning("标注图像加载失败");
}
</script>

<style lang="scss" scoped>
.detection-page {
  h2 {
    margin-bottom: $spacing-lg;
  }
}

.detection-layout {
  display: flex;
  gap: $spacing-lg;
  height: calc(100vh - 120px);
}

// ── 上传面板 ──
.upload-panel {
  width: 380px;
  flex-shrink: 0;

  .upload-area {
    border: 2px dashed #dcdfe6;
    border-radius: 8px;
    padding: 30px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.3s;
    min-height: 200px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;

    &:hover,
    &.is-dragover {
      border-color: #409eff;
      background-color: rgba(64, 158, 255, 0.05);
    }

    p {
      margin: 8px 0 0;
      color: #606266;
    }

    .upload-hint {
      font-size: 12px;
      color: #909399;
    }
  }

  .preview-image {
    max-width: 100%;
    max-height: 260px;
    object-fit: contain;
    border-radius: 4px;
  }

  .detect-params {
    margin-top: $spacing-md;

    .param-item {
      span {
        font-size: 13px;
        color: #606266;
        display: block;
        margin-bottom: 4px;
      }
    }
  }

  .detect-btn {
    width: 100%;
    margin-top: $spacing-md;
  }
}

// ── 结果面板 ──
.result-panel {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: $spacing-md;
}

.loading-box {
  text-align: center;
  padding: 40px 0;
  p {
    margin-top: 16px;
    color: #909399;
  }
}

.stats-row {
  .stat-card {
    text-align: center;
    .stat-value {
      font-size: 28px;
      font-weight: 700;
      color: #409eff;
    }
    .stat-label {
      font-size: 13px;
      color: #909399;
      margin-top: 4px;
    }
  }
}

.annotated-card {
  .annotated-image {
    max-width: 100%;
    border-radius: 4px;
  }
}

.result-table-card {
  code {
    font-size: 12px;
    color: #606266;
    background: #f5f7fa;
    padding: 2px 6px;
    border-radius: 3px;
  }
}

.analysis-card {
  margin-top: 16px;
  border-left: 4px solid #2a9d8f;
}
.analysis-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.analysis-content {
  font-size: 14px;
  line-height: 1.8;
  h3 {
    font-size: 16px;
    color: #2a9d8f;
    margin: 12px 0 8px;
  }
  h4 {
    font-size: 14px;
    color: #333;
    margin: 8px 0 4px;
  }
  p {
    margin: 4px 0;
  }
}
</style>
