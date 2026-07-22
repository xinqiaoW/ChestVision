<template>
  <div class="training-page">
    <div class="page-header">
      <div>
        <h2>模型训练与监控</h2>
        <span class="page-subtitle">Training</span>
      </div>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon>新建训练任务
      </el-button>
    </div>
    <el-card class="task-list-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>训练任务列表</span>
          <el-button text @click="fetchTasks"
            ><el-icon><Refresh /></el-icon>刷新</el-button
          >
        </div>
      </template>
      <el-table :data="taskList" stripe v-loading="loadingTasks">
        <el-table-column prop="task_uuid" label="任务 ID" width="100" />
        <el-table-column prop="model_name" label="模型" width="110" />
        <el-table-column prop="device" label="设备" width="80" />
        <el-table-column label="进度" width="180">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="
                row.status === 'completed'
                  ? 'success'
                  : row.status === 'failed'
                    ? 'exception'
                    : ''
              "
              :stroke-width="16"
            />
          </template>
        </el-table-column>
        <el-table-column label="Epoch" width="100">
          <template #default="{ row }"
            >{{ row.current_epoch }}/{{ row.epochs }}</template
          >
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{
              statusText(row.status)
            }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="selectTask(row)"
              >监控</el-button
            >
            <el-button
              v-if="row.status === 'running'"
              size="small"
              type="danger"
              text
              @click="stopTask(row.id)"
              >停止</el-button
            >
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 模型版本管理 — 全局默认模型切换 -->
    <el-card class="model-versions-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>📦 模型版本管理（全局默认模型）</span>
          <el-button text @click="fetchModelVersions">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
      </template>
      <el-table
        :data="modelVersions"
        stripe
        v-loading="loadingVersions"
        empty-text="暂无模型版本，请先训练并导出模型"
      >
        <el-table-column label="默认" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small"
              >当前使用</el-tag
            >
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型名称" min-width="160" />
        <el-table-column prop="version" label="版本" width="90" />
        <el-table-column label="mAP@50" width="100">
          <template #default="{ row }">
            <span :style="{ color: row.map50 ? '#67c23a' : '#909399' }">
              {{ row.map50 ? (row.map50 * 100).toFixed(1) + "%" : "-" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="mAP@50-95" width="110">
          <template #default="{ row }">
            {{ row.map50_95 ? (row.map50_95 * 100).toFixed(1) + "%" : "-" }}
          </template>
        </el-table-column>
        <el-table-column label="大小" width="90">
          <template #default="{ row }">
            {{
              row.file_size
                ? (row.file_size / 1024 / 1024).toFixed(1) + "MB"
                : "-"
            }}
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.is_default"
              size="small"
              type="primary"
              @click="setDefaultModel(row.id)"
              :loading="settingDefault === row.id"
            >
              设为当前使用
            </el-button>
            <el-tag v-else type="success" size="small">已是默认</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="selectedTask" class="monitor-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span
            >训练监控 — 任务 {{ selectedTask.task_uuid }}
            <el-tag
              :type="statusType(selectedTask.status)"
              size="small"
              style="margin-left: 8px"
              >{{ statusText(selectedTask.status) }}</el-tag
            >
          </span>
          <div class="monitor-info">
            <span>模型: {{ selectedTask.model_name }}</span>
            <span>设备: {{ selectedTask.device }}</span>
            <span
              >Epoch: {{ selectedTask.current_epoch }}/{{
                selectedTask.epochs
              }}</span
            >
          </div>
        </div>
      </template>
      <el-row :gutter="16" class="metric-cards">
        <el-col :span="4" v-for="item in metricCards" :key="item.label">
          <el-card shadow="hover" class="metric-item">
            <div class="metric-value">{{ item.value }}</div>
            <div class="metric-label">{{ item.label }}</div>
          </el-card>
        </el-col>
      </el-row>
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="12"
          ><div ref="lossChartRef" style="height: 350px"></div
        ></el-col>
        <el-col :span="12"
          ><div ref="mapChartRef" style="height: 350px"></div
        ></el-col>
      </el-row>
    </el-card>

    <!-- 【Day 7 新增】模型操作栏 -->
    <el-card
      v-if="selectedTask && selectedTask.status === 'completed'"
      class="action-card"
      shadow="never"
    >
      <template #header>
        <div class="card-header"><span>模型操作</span></div>
      </template>
      <el-space wrap>
        <el-button type="primary" @click="validateModel" :loading="validating"
          >评估模型</el-button
        >
        <el-button type="success" @click="exportModel" :loading="exporting"
          >导出模型</el-button
        >
        <el-button @click="downloadModel">下载权重</el-button>
        <el-button type="warning" @click="showPredictDialog = true"
          >测试验证</el-button
        >
      </el-space>
    </el-card>

    <!-- 【Day 7 新增】评估报告面板 -->
    <el-card v-if="evalReport" class="eval-card" shadow="never">
      <template #header>
        <div class="card-header">
          <span
            >评估报告
            <el-tag size="small" style="margin-left: 8px">{{
              evalReport.split === "val" ? "验证集" : "测试集"
            }}</el-tag>
          </span>
        </div>
      </template>
      <el-row :gutter="16" class="metric-cards">
        <el-col :span="6" v-for="item in evalMetricCards" :key="item.label">
          <el-card shadow="hover" class="metric-item">
            <div class="metric-value" :style="{ color: item.color }">
              {{ item.value }}
            </div>
            <div class="metric-label">{{ item.label }}</div>
          </el-card>
        </el-col>
      </el-row>
      <el-table :data="perClassData" stripe style="margin-top: 16px">
        <el-table-column prop="class_name" label="类别" width="160" />
        <el-table-column prop="ap50" label="AP@50" width="120">
          <template #default="{ row }">
            <span :style="{ color: row.ap50 < 0.5 ? '#f56c6c' : '#67c23a' }">
              {{ (row.ap50 * 100).toFixed(1) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="ap50_95" label="AP@50-95" width="120">
          <template #default="{ row }"
            >{{ (row.ap50_95 * 100).toFixed(1) }}%</template
          >
        </el-table-column>
        <el-table-column label="评价">
          <template #default="{ row }">
            <el-tag
              :type="
                row.ap50 >= 0.8
                  ? 'success'
                  : row.ap50 >= 0.5
                    ? 'warning'
                    : 'danger'
              "
              size="small"
            >
              {{
                row.ap50 >= 0.8 ? "优秀" : row.ap50 >= 0.5 ? "一般" : "需改进"
              }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="showCreateDialog"
      title="新建训练任务"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-form :model="trainForm" label-width="120px">
        <el-form-item label="训练数据集">
          <div
            style="display: flex; gap: 8px; align-items: center; width: 100%"
          >
            <el-select
              v-model="trainForm.dataset_name"
              placeholder="选择数据集"
              style="flex: 1"
              @change="onDatasetChange"
            >
              <el-option
                v-for="ds in datasetList"
                :key="ds.name"
                :label="`${ds.name} (训练${ds.train_count}张 + 验证${ds.val_count}张)`"
                :value="ds.name"
              />
            </el-select>
            <el-button @click="showUploadDataset = true" :icon="Upload"
              >上传</el-button
            >
          </div>
          <div
            v-if="selectedDataset"
            style="margin-top: 6px; font-size: 12px; color: #909399"
          >
            📊 {{ selectedDataset.train_count }} 训练 +
            {{ selectedDataset.val_count }} 验证 =
            {{ selectedDataset.total_count }} 张
          </div>
        </el-form-item>
        <el-form-item label="检测场景">
          <el-select v-model="trainForm.scene_id" placeholder="选择场景">
            <el-option label="胸片X光病灶检测" :value="3" />
          </el-select>
        </el-form-item>
        <el-form-item label="基础模型">
          <el-select v-model="trainForm.model_name">
            <el-option label="YOLO11n (Nano · 最快)" value="yolo11n" />
            <el-option label="YOLO11s (Small · 轻量)" value="yolo11s" />
            <el-option label="YOLO11m (Medium · 均衡)" value="yolo11m" />
            <el-option label="YOLO11l (Large · 高精度)" value="yolo11l" />
            <el-option label="YOLO11x (X-Large · 最高精度)" value="yolo11x" />
          </el-select>
        </el-form-item>
        <el-form-item label="训练轮数">
          <el-slider
            v-model="trainForm.epochs"
            :min="10"
            :max="500"
            :step="10"
            show-input
          />
        </el-form-item>
        <el-form-item label="批次大小">
          <el-input-number
            v-model="trainForm.batch_size"
            :min="1"
            :max="64"
            :step="2"
          />
        </el-form-item>
        <el-form-item label="图像尺寸">
          <el-select v-model="trainForm.img_size">
            <el-option label="640 (默认)" :value="640" />
            <el-option label="512" :value="512" />
          </el-select>
        </el-form-item>
        <el-form-item label="训练设备">
          <el-radio-group v-model="trainForm.device">
            <el-radio value="cpu">CPU (本地)</el-radio>
            <el-radio value="0">GPU:0</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="优化器">
          <el-select v-model="trainForm.optimizer">
            <el-option label="SGD (推荐)" value="SGD" />
            <el-option label="Adam" value="Adam" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始学习率">
          <el-input-number
            v-model="trainForm.lr0"
            :min="0.0001"
            :max="0.1"
            :step="0.001"
            :precision="4"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="createTask" :loading="creating"
          >启动训练</el-button
        >
      </template>
    </el-dialog>

    <!-- 上传数据集对话框 -->
    <el-dialog v-model="showUploadDataset" title="上传训练数据集" width="550px">
      <el-form label-width="100px">
        <el-form-item label="数据集名称">
          <el-input
            v-model="uploadDatasetName"
            placeholder="英文名，如 chest_xray_v2"
          />
        </el-form-item>
        <el-form-item label="数据说明">
          <div style="font-size: 12px; color: #909399; line-height: 1.6">
            ZIP 包目录结构：<br />
            ├─ images/train/ &nbsp; 训练图片 (.jpg/.png)<br />
            ├─ images/val/ &nbsp;&nbsp;&nbsp; 验证图片 <br />
            ├─ labels/train/ &nbsp; YOLO 标注 (.txt)<br />
            └─ labels/val/ &nbsp;&nbsp;&nbsp; 验证标注
          </div>
        </el-form-item>
        <el-form-item label="选择文件">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".zip"
            :on-change="onUploadFileChange"
            :file-list="[]"
          >
            <el-button type="primary">选择 ZIP 文件</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUploadDataset = false">取消</el-button>
        <el-button type="primary" @click="doUploadDataset" :loading="uploading">
          上传并准备数据集
        </el-button>
      </template>
    </el-dialog>

    <!-- 【Day 7 新增】测试图验证对话框 -->
    <el-dialog v-model="showPredictDialog" title="测试图验证" width="800px">
      <el-form label-width="100px">
        <el-form-item label="测试图片">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept="image/*"
            :on-change="onPredictFileChange"
            list-type="picture"
          >
            <el-button type="primary">选择图片</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider
            v-model="predictConf"
            :min="0.05"
            :max="0.95"
            :step="0.05"
            show-input
          />
        </el-form-item>
        <el-form-item label="IoU 阈值">
          <el-slider
            v-model="predictIou"
            :min="0.1"
            :max="0.9"
            :step="0.05"
            show-input
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPredictDialog = false">关闭</el-button>
        <el-button type="primary" @click="doPredict" :loading="predictLoading"
          >开始检测</el-button
        >
      </template>

      <div v-if="predictResult" style="margin-top: 16px">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="检测目标数">{{
            predictResult.total_objects
          }}</el-descriptions-item>
          <el-descriptions-item label="推理耗时"
            >{{ predictResult.inference_time }}ms</el-descriptions-item
          >
          <el-descriptions-item label="文件名">{{
            predictResult.filename
          }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="predictResult.annotated_image" style="margin-top: 12px">
          <img
            :src="'data:image/jpeg;base64,' + predictResult.annotated_image"
            style="max-width: 100%; border-radius: 4px"
          />
        </div>
        <el-table
          :data="predictResult.detections"
          stripe
          style="margin-top: 12px"
          max-height="200"
        >
          <el-table-column prop="class_name" label="类别" width="120" />
          <el-table-column prop="confidence" label="置信度" width="100">
            <template #default="{ row }"
              >{{ (row.confidence * 100).toFixed(1) }}%</template
            >
          </el-table-column>
          <el-table-column label="边界框" width="200">
            <template #default="{ row }">{{ row.bbox.join(", ") }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import request from "@/utils/request";
import { Plus, Refresh, Upload } from "@element-plus/icons-vue";
import * as echarts from "echarts";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

const taskList = ref([]);
const loadingTasks = ref(false);
const selectedTask = ref(null);
const showCreateDialog = ref(false);
const creating = ref(false);
const lossChartRef = ref(null);
const mapChartRef = ref(null);
let lossChart = null,
  mapChart = null;
let pollTimer = null;

// 数据集管理
const datasetList = ref([]);
const showUploadDataset = ref(false);
const uploadFile = ref(null);
const uploadDatasetName = ref("");
const uploading = ref(false);

// 模型版本管理
const modelVersions = ref([]);
const loadingVersions = ref(false);
const settingDefault = ref(null);

const selectedDataset = computed(() => {
  return datasetList.value.find((d) => d.name === trainForm.value.dataset_name);
});

// 【Day 7 新增】模型操作状态
const validating = ref(false);
const exporting = ref(false);
const evalReport = ref(null);
const showPredictDialog = ref(false);
const predictConf = ref(0.25);
const predictIou = ref(0.45);
const predictLoading = ref(false);
const predictResult = ref(null);
let predictFile = null;

const trainForm = ref({
  scene_id: 3,
  dataset_name: "chest_xray",
  model_name: "yolo11n",
  epochs: 50,
  batch_size: 8,
  img_size: 640,
  device: "cpu",
  optimizer: "SGD",
  lr0: 0.01,
});

const metricCards = computed(() => {
  if (!selectedTask.value) return [];
  const m = selectedTask.value.latest_metric;
  if (!m)
    return [
      {
        label: "Epoch",
        value: `${selectedTask.value.current_epoch}/${selectedTask.value.epochs}`,
      },
      { label: "进度", value: `${selectedTask.value.progress}%` },
      { label: "Box Loss", value: "-" },
      { label: "Cls Loss", value: "-" },
      { label: "mAP@50", value: "-" },
      { label: "mAP@50-95", value: "-" },
    ];
  return [
    { label: "Epoch", value: `${m.epoch}/${selectedTask.value.epochs}` },
    {
      label: "Box Loss",
      value: m.box_loss != null ? m.box_loss.toFixed(4) : "-",
    },
    {
      label: "Cls Loss",
      value: m.cls_loss != null ? m.cls_loss.toFixed(4) : "-",
    },
    {
      label: "Precision",
      value: m.precision != null ? (m.precision * 100).toFixed(1) + "%" : "-",
    },
    {
      label: "mAP@50",
      value: m.map50 != null ? (m.map50 * 100).toFixed(1) + "%" : "-",
    },
    {
      label: "mAP@50-95",
      value: m.map50_95 != null ? (m.map50_95 * 100).toFixed(1) + "%" : "-",
    },
  ];
});

// 【Day 7 新增】评估报告计算属性
const evalMetricCards = computed(() => {
  if (!evalReport.value) return [];
  const o = evalReport.value.overall;
  return [
    {
      label: "mAP@50",
      value: o.map50 != null ? (o.map50 * 100).toFixed(1) + "%" : "-",
      color: "#409eff",
    },
    {
      label: "mAP@50-95",
      value: o.map50_95 != null ? (o.map50_95 * 100).toFixed(1) + "%" : "-",
      color: "#67c23a",
    },
    {
      label: "Precision",
      value: o.precision != null ? (o.precision * 100).toFixed(1) + "%" : "-",
      color: "#e6a23c",
    },
    {
      label: "Recall",
      value: o.recall != null ? (o.recall * 100).toFixed(1) + "%" : "-",
      color: "#f56c6c",
    },
  ];
});

const perClassData = computed(() => {
  if (!evalReport.value) return [];
  const pc = evalReport.value.per_class || {};
  return Object.entries(pc)
    .map(([name, m]) => ({
      class_name: name,
      ap50: m.ap50,
      ap50_95: m.ap50_95,
    }))
    .sort((a, b) => b.ap50 - a.ap50);
});

function statusType(s) {
  const m = {
    pending: "info",
    running: "warning",
    completed: "success",
    failed: "danger",
    cancelled: "info",
  };
  return m[s] || "info";
}
function statusText(s) {
  const m = {
    pending: "等待中",
    running: "训练中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return m[s] || s;
}

async function fetchTasks() {
  loadingTasks.value = true;
  try {
    const res = await request.get("/training/tasks");
    taskList.value = res.items || [];
  } catch {
    ElMessage.error("获取训练任务列表失败");
  } finally {
    loadingTasks.value = false;
  }
}

async function selectTask(task) {
  selectedTask.value = task;
  await nextTick();
  // 延迟初始化图表，确保 Element Plus 组件完成布局
  setTimeout(() => {
    initCharts();
  }, 100);
  fetchMetrics();
  startPolling();
}

function initCharts() {
  if (lossChart) lossChart.dispose();
  if (mapChart) mapChart.dispose();
  if (lossChartRef.value) {
    lossChart = echarts.init(lossChartRef.value);
    // 监听窗口 resize 自动重绘
    window.addEventListener("resize", handleChartResize);
  }
  if (mapChartRef.value) {
    mapChart = echarts.init(mapChartRef.value);
  }
}

function handleChartResize() {
  if (lossChart && !lossChart.isDisposed()) lossChart.resize();
  if (mapChart && !mapChart.isDisposed()) mapChart.resize();
}

async function fetchMetrics() {
  if (!selectedTask.value) return;
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id;
    const res = await request.get(`/training/metrics/${taskId}`, {
      silent: true,
    });
    const metrics = res.metrics || [];
    const statusRes = await request.get(`/training/status/${taskId}`, {
      silent: true,
    });
    if (statusRes) selectedTask.value = { ...selectedTask.value, ...statusRes };
    if (metrics.length > 0) updateCharts(metrics);
  } catch {
    ElMessage.warning("获取训练指标失败");
  }
}

function updateCharts(metrics) {
  const epochs = metrics.map((m) => m.epoch);
  if (lossChart)
    lossChart.setOption({
      title: {
        text: "训练损失曲线",
        left: "center",
        textStyle: { fontSize: 14 },
      },
      tooltip: { trigger: "axis" },
      legend: { data: ["Box Loss", "Cls Loss", "DFL Loss"], bottom: 0 },
      grid: { left: "10%", right: "5%", top: "15%", bottom: "15%" },
      xAxis: { type: "category", data: epochs, name: "Epoch" },
      yAxis: { type: "value", name: "Loss" },
      series: [
        {
          name: "Box Loss",
          type: "line",
          data: metrics.map((m) => m.box_loss),
          smooth: true,
          lineStyle: { width: 2 },
        },
        {
          name: "Cls Loss",
          type: "line",
          data: metrics.map((m) => m.cls_loss),
          smooth: true,
          lineStyle: { width: 2 },
        },
        {
          name: "DFL Loss",
          type: "line",
          data: metrics.map((m) => m.dfl_loss),
          smooth: true,
          lineStyle: { width: 2 },
        },
      ],
    });
  if (mapChart)
    mapChart.setOption({
      title: {
        text: "评估指标曲线",
        left: "center",
        textStyle: { fontSize: 14 },
      },
      tooltip: { trigger: "axis" },
      legend: {
        data: ["mAP@50", "mAP@50-95", "Precision", "Recall"],
        bottom: 0,
      },
      grid: { left: "10%", right: "5%", top: "15%", bottom: "15%" },
      xAxis: { type: "category", data: epochs, name: "Epoch" },
      yAxis: { type: "value", name: "指标值", max: 1 },
      series: [
        {
          name: "mAP@50",
          type: "line",
          data: metrics.map((m) => m.map50),
          smooth: true,
          lineStyle: { width: 2, color: "#409eff" },
        },
        {
          name: "mAP@50-95",
          type: "line",
          data: metrics.map((m) => m.map50_95),
          smooth: true,
          lineStyle: { width: 2, color: "#67c23a" },
        },
        {
          name: "Precision",
          type: "line",
          data: metrics.map((m) => m.precision),
          smooth: true,
          lineStyle: { width: 2, type: "dashed", color: "#e6a23c" },
        },
        {
          name: "Recall",
          type: "line",
          data: metrics.map((m) => m.recall),
          smooth: true,
          lineStyle: { width: 2, type: "dashed", color: "#f56c6c" },
        },
      ],
    });
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (selectedTask.value) fetchMetrics();
  }, 5000);
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function createTask() {
  creating.value = true;
  try {
    const payload = {
      ...trainForm.value,
      scene_name: trainForm.value.dataset_name, // 用数据集名作为场景名
    };
    delete payload.dataset_name;
    const res = await request.post("/training/start", payload);
    ElMessage.success(`训练任务已创建：${res.task_uuid}`);
    showCreateDialog.value = false;
    await fetchTasks();
    if (res.id) {
      const t = taskList.value.find((t2) => t2.id === res.id);
      if (t) selectTask(t);
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "创建失败");
  } finally {
    creating.value = false;
  }
}

// ── 数据集管理 ──
async function fetchDatasets() {
  try {
    const res = await request.get("/training/datasets");
    datasetList.value = res.datasets || [];
  } catch {
    /* ignore */
  }
}

function onDatasetChange(name) {
  if (!name) return;
  // 数据集名同时也是场景名，需确保 scene 存在
}

async function doUploadDataset() {
  if (!uploadFile.value || !uploadDatasetName.value.trim()) {
    ElMessage.warning("请填写数据集名称并选择 ZIP 文件");
    return;
  }
  uploading.value = true;
  try {
    const fd = new FormData();
    fd.append("file", uploadFile.value);
    fd.append("dataset_name", uploadDatasetName.value.trim());
    await request.post("/training/datasets/upload", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120000,
    });
    ElMessage.success("数据集上传成功");
    showUploadDataset.value = false;
    uploadFile.value = null;
    uploadDatasetName.value = "";
    await fetchDatasets();
    // 自动选中新上传的数据集
    trainForm.value.dataset_name =
      uploadDatasetName.value || trainForm.value.dataset_name;
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "上传失败");
  } finally {
    uploading.value = false;
  }
}

function onUploadFileChange(file) {
  uploadFile.value = file.raw;
}

onMounted(() => {
  fetchTasks();
  fetchDatasets();
});

async function stopTask(taskId) {
  try {
    await ElMessageBox.confirm("确定要停止训练吗？", "确认停止", {
      type: "warning",
    });
    await request.post(`/training/stop/${taskId}`);
    ElMessage.success("已停止");
    await fetchTasks();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("停止失败");
  }
}

// 【Day 7 新增】模型操作：评估
async function validateModel() {
  if (!selectedTask.value) return;
  validating.value = true;
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id;
    const res = await request.post(`/training/validate/${taskId}`, {
      split: "val",
      conf: 0.001,
      iou: 0.6,
    });
    evalReport.value = res;
    ElMessage.success(
      `评估完成: mAP@50=${(res.overall.map50 * 100).toFixed(1)}%`,
    );
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "评估失败");
  } finally {
    validating.value = false;
  }
}

// 【Day 7 新增】模型操作：导出
async function exportModel() {
  if (!selectedTask.value) return;
  exporting.value = true;
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id;
    const res = await request.post(`/training/export/${taskId}`, {
      set_default: true,
    });
    ElMessage.success(`模型已导出: ${res.version}`);
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "导出失败");
  } finally {
    exporting.value = false;
  }
}

// 【Day 7 新增】模型操作：下载
async function downloadModel() {
  if (!selectedTask.value) return;
  const taskId = selectedTask.value.id || selectedTask.value.task?.id;
  const token = localStorage.getItem("chestx_token") || "";
  try {
    const response = await fetch(`/api/training/download/${taskId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) {
      throw new Error("下载失败");
    }
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedTask.value.model_name}_${selectedTask.value.task_uuid}_best.pt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  } catch (e) {
    ElMessage.error("下载失败");
  }
}

// 【Day 7 新增】测试图验证：选择文件
function onPredictFileChange(file) {
  predictFile = file.raw;
  predictResult.value = null;
}

// 【Day 7 新增】测试图验证：执行预测
async function doPredict() {
  if (!predictFile || !selectedTask.value) return;
  predictLoading.value = true;
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id;
    const form = new FormData();
    form.append("file", predictFile);
    form.append("task_id", taskId);
    form.append("conf", predictConf.value);
    form.append("iou", predictIou.value);
    const res = await request.post("/training/predict", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    predictResult.value = res;
    ElMessage.success(`检测到 ${res.total_objects} 个目标`);
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "预测失败");
  } finally {
    predictLoading.value = false;
  }
}

onMounted(() => {
  fetchTasks();
  fetchDatasets();
  fetchModelVersions();
});

// ── 模型版本管理 ──
async function fetchModelVersions() {
  loadingVersions.value = true;
  try {
    const res = await request.get("/training/models");
    modelVersions.value = res.models || [];
  } catch {
    /* ignore */
  } finally {
    loadingVersions.value = false;
  }
}

async function setDefaultModel(modelVersionId) {
  settingDefault.value = modelVersionId;
  try {
    await request.post(`/training/models/${modelVersionId}/set-default`);
    ElMessage.success("全局默认模型已切换，检测接口将使用新模型");
    await fetchModelVersions();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "设置失败");
  } finally {
    settingDefault.value = null;
  }
}

onBeforeUnmount(() => {
  stopPolling();
  window.removeEventListener("resize", handleChartResize);
  if (lossChart) lossChart.dispose();
  if (mapChart) mapChart.dispose();
});
</script>

<style scoped>
.training-page {
  padding: 20px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 22px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.monitor-info {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #909399;
}
.metric-cards {
  margin-bottom: 8px;
}
.metric-item {
  text-align: center;
  padding: 8px 0;
}
.metric-value {
  font-size: 20px;
  font-weight: 700;
  color: #303133;
}
.metric-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
.task-list-card,
.monitor-card,
.action-card,
.eval-card {
  margin-bottom: 20px;
}
</style>
