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
      <el-table
        class="task-table"
        :data="taskList"
        stripe
        v-loading="loadingTasks"
      >
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
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button
              class="monitor-action-button"
              size="small"
              text
              @click="selectTask(row)"
              >监控</el-button
            >
            <el-button
              v-if="canCancelTask(row)"
              size="small"
              type="danger"
              text
              @click="stopTask(row)"
              >取消</el-button
            >
            <el-button
              v-if="canDeleteTask(row)"
              size="small"
              type="danger"
              text
              @click="deleteTask(row)"
            >
              <el-icon><Delete /></el-icon>删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="selectedTask" class="monitor-card" shadow="never">
      <template #header>
        <div class="card-header">
          <div class="monitor-title">
            <span>
              训练监控 — 任务 {{ selectedTask.task_uuid }}
              <el-tag
                :type="statusType(selectedTask.status)"
                size="small"
                style="margin-left: 8px"
                >{{ statusText(selectedTask.status) }}</el-tag
              >
            </span>
            <el-button
              v-if="canCancelTask(selectedTask)"
              size="small"
              type="danger"
              plain
              @click="stopTask(selectedTask)"
            >
              取消训练
            </el-button>
          </div>
          <div class="monitor-actions">
            <div class="monitor-info">
              <span>模型: {{ selectedTask.model_name }}</span>
              <span>设备: {{ selectedTask.device }}</span>
              <span
                >Epoch: {{ selectedTask.current_epoch }}/{{
                  selectedTask.epochs
                }}</span
              >
            </div>
            <el-button
              size="small"
              :icon="Refresh"
              :disabled="!canManuallyRefreshMonitor"
              :loading="monitorRefreshing"
              @click="refreshMonitor({ manual: true })"
            >
              {{ monitorRefreshLabel }}
            </el-button>
          </div>
        </div>
      </template>
      <el-tabs v-model="monitorActiveTab" @tab-change="handleMonitorTabChange">
        <el-tab-pane label="监控曲线" name="metrics">
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
        </el-tab-pane>
        <el-tab-pane label="运行日志" name="logs">
          <div class="training-log-panel">
            <div class="training-log-toolbar">
              <el-alert
                v-if="hasTrainingError"
                :title="trainingErrorSummary"
                type="error"
                show-icon
                :closable="false"
              />
              <div v-else class="training-log-meta">
                {{ runLogStatusText }}
              </div>
              <el-button
                :icon="Download"
                :disabled="!runLogMeta.exists"
                @click="downloadRunLog"
              >
                下载运行日志
              </el-button>
            </div>
            <el-scrollbar
              v-loading="runLogLoading"
              height="360px"
              class="training-log-scrollbar"
            >
              <div v-if="runLogLines.length" class="training-log-lines">
                <div
                  v-for="line in runLogLines"
                  :key="line.line_number"
                  class="training-log-line"
                >
                  <span class="training-log-line-number">{{
                    line.line_number
                  }}</span>
                  <span class="training-log-line-content">{{
                    line.content || " "
                  }}</span>
                </div>
              </div>
              <div v-else class="training-log-empty">
                {{ runLogStatusText }}
              </div>
            </el-scrollbar>
          </div>
        </el-tab-pane>
      </el-tabs>
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
        <el-button
          :icon="Download"
          :loading="reportExporting"
          @click="exportTrainingReport"
        >
          导出训练报告
        </el-button>
        <el-button @click="downloadModel">下载权重</el-button>
      </el-space>
    </el-card>

    <el-dialog
      v-model="showCreateDialog"
      title="新建训练任务"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-form :model="trainForm" label-width="120px">
        <el-form-item label="训练数据集">
          <div class="dataset-select-row">
            <el-select
              v-model="trainForm.dataset_id"
              filterable
              clearable
              :filter-method="filterTrainingDatasets"
              no-data-text="暂无已上传数据集"
              no-match-text="没有匹配的数据集"
              placeholder="输入名称筛选已上传数据集"
              @visible-change="onDatasetSelectVisibleChange"
            >
              <el-option
                v-for="ds in filteredTrainingDatasets"
                :key="ds.upload_id"
                :label="formatDatasetOption(ds)"
                :value="ds.dataset_id"
              />
            </el-select>
            <el-button @click="openDatasetUploadPage">
              <el-icon><Upload /></el-icon>上传
            </el-button>
          </div>
          <div
            v-if="selectedDataset"
            style="margin-top: 6px; font-size: 12px; color: #909399"
          >
            {{ formatDatasetOption(selectedDataset) }}
          </div>
        </el-form-item>
        <el-form-item label="检测场景">
          <el-select v-model="trainForm.scene_id" placeholder="选择场景">
            <el-option label="按数据集自动匹配/创建" :value="null" />
          </el-select>
        </el-form-item>
        <el-form-item label="基础模型">
          <el-select
            v-model="trainForm.model_name"
            filterable
            placeholder="搜索基础模型"
            style="width: 100%"
          >
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

  </div>
</template>

<script setup>
import request from "@/utils/request";
import { Delete, Download, Plus, Refresh, Upload } from "@element-plus/icons-vue";
import * as echarts from "echarts";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();
const taskList = ref([]);
const loadingTasks = ref(false);
const selectedTask = ref(null);
const monitorRefreshing = ref(false);
const reportExporting = ref(false);
const runLogLoading = ref(false);
const runLogLines = ref([]);
const runLogMeta = ref(createEmptyRunLogMeta());
const lastMonitorRefreshAt = ref(null);
const refreshClockNow = ref(Date.now());
const monitorActiveTab = ref("metrics");
const showCreateDialog = ref(false);
const creating = ref(false);
const lossChartRef = ref(null);
const mapChartRef = ref(null);
let lossChart = null,
  mapChart = null;
let pollTimer = null;
let refreshClockTimer = null;
const MONITOR_AUTO_REFRESH_INTERVAL_MS = 60 * 1000;
const MONITOR_MANUAL_REFRESH_COOLDOWN_MS = 20 * 1000;
const MONITOR_POLL_TICK_MS = 1000;
const RUN_LOG_FETCH_LIMIT = 2000;
const TERMINAL_TRAINING_STATUSES = [
  "completed",
  "succeeded",
  "finished",
  "failed",
  "cancelled",
  "stopped",
  "aborted",
];

// 数据集管理
const datasetList = ref([]);
const datasetNameFilter = ref("");

const selectedDataset = computed(() => {
  return datasetList.value.find((d) => d.dataset_id === trainForm.value.dataset_id);
});

const availableTrainingDatasets = computed(() =>
  datasetList.value.filter((d) => isDatasetTrainable(d)),
);

const filteredTrainingDatasets = computed(() => {
  const keyword = datasetNameFilter.value.trim().toLowerCase();
  if (!keyword) return availableTrainingDatasets.value;
  return availableTrainingDatasets.value.filter((dataset) =>
    getDatasetName(dataset).toLowerCase().includes(keyword),
  );
});

const trainForm = ref({
  scene_id: null,
  dataset_id: "",
  model_name: "yolo11n",
  epochs: 50,
  batch_size: 8,
  img_size: 640,
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

const monitorRefreshLabel = computed(() => {
  if (monitorRefreshing.value) return "刷新中";
  if (!lastMonitorRefreshAt.value) return "刷新";
  if (monitorAutoRefreshRemainingMs.value <= 0) return "刷新 · 即将刷新";
  return `刷新 · ${formatRefreshCountdown(monitorAutoRefreshRemainingMs.value)}后`;
});

const monitorRefreshElapsedMs = computed(() => {
  if (!lastMonitorRefreshAt.value) return Number.POSITIVE_INFINITY;
  return Math.max(refreshClockNow.value - lastMonitorRefreshAt.value, 0);
});

const monitorAutoRefreshRemainingMs = computed(() => {
  if (!lastMonitorRefreshAt.value) return 0;
  return Math.max(MONITOR_AUTO_REFRESH_INTERVAL_MS - monitorRefreshElapsedMs.value, 0);
});

const monitorManualCooldownRemainingMs = computed(() => {
  if (!lastMonitorRefreshAt.value) return 0;
  return Math.max(
    MONITOR_MANUAL_REFRESH_COOLDOWN_MS - monitorRefreshElapsedMs.value,
    0,
  );
});

const canManuallyRefreshMonitor = computed(
  () => !monitorRefreshing.value && monitorManualCooldownRemainingMs.value <= 0,
);

const trainingErrorDetail = computed(() => {
  const task = selectedTask.value;
  if (!task) return null;
  return (
    task.error_detail ||
    task.remote?.error_detail ||
    parseErrorDetail(task.remote?.error_message) ||
    parseErrorDetail(task.error_message)
  );
});

const hasTrainingError = computed(() => {
  const status = String(selectedTask.value?.status || "").toLowerCase();
  return status === "failed" && Boolean(trainingErrorDetail.value);
});

const trainingErrorSummary = computed(() => {
  const detail = trainingErrorDetail.value;
  if (!detail) return selectedTask.value?.error_message || "远程训练失败";
  const stage = detail.stage ? errorStageText(detail.stage) : "远程训练";
  const errorType = detail.error_type || "Error";
  const error = detail.error || selectedTask.value?.error_message || "训练失败";
  return `${stage} · ${errorType}: ${error}`;
});

const runLogStatusText = computed(() => {
  if (!selectedTask.value) return "请选择训练任务";
  if (!isRemoteTask(selectedTask.value)) return "当前任务不是远程训练任务";
  if (runLogLoading.value) return "正在读取运行日志";
  if (!runLogMeta.value.exists) return "运行日志尚未生成";
  const total = runLogMeta.value.total_lines || 0;
  if (!total) return "运行日志为空";
  const rangeStart = runLogMeta.value.start_line || 1;
  const rangeEnd = Math.max((runLogMeta.value.next_line || 1) - 1, rangeStart);
  if (runLogMeta.value.truncated_head || runLogMeta.value.truncated_tail) {
    return `显示第 ${rangeStart}-${rangeEnd} 行，共 ${total} 行`;
  }
  return `共 ${total} 行`;
});

function statusType(s) {
  const m = {
    pending: "info",
    running: "warning",
    completed: "success",
    succeeded: "success",
    finished: "success",
    failed: "danger",
    cancelled: "info",
    stopped: "info",
    aborted: "info",
  };
  return m[s] || "info";
}
function statusText(s) {
  const m = {
    pending: "等待中",
    running: "训练中",
    completed: "已完成",
    succeeded: "已完成",
    finished: "已完成",
    failed: "失败",
    cancelled: "已取消",
    stopped: "已停止",
    aborted: "已中止",
  };
  return m[s] || s;
}

function parseErrorDetail(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  if (typeof value !== "string") return { error: String(value) };
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "object" && parsed !== null
      ? parsed
      : { error: parsed };
  } catch {
    return { error: value };
  }
}

function errorStageText(stage) {
  const map = {
    prepare_dataset: "数据准备",
    train_yolo: "模型训练",
    collect_artifacts: "产物收集",
  };
  return map[stage] || stage;
}

function createEmptyRunLogMeta() {
  return {
    exists: false,
    total_lines: 0,
    start_line: 1,
    next_line: 1,
    log_key: "",
    truncated_head: false,
    truncated_tail: false,
  };
}

function resetRunLog() {
  runLogLines.value = [];
  runLogMeta.value = createEmptyRunLogMeta();
}

async function fetchRunLog() {
  if (!selectedTask.value || !isRemoteTask(selectedTask.value)) {
    resetRunLog();
    return;
  }
  runLogLoading.value = true;
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id;
    const res = await request.get(`/training/remote/logs/${taskId}`, {
      params: {
        limit: RUN_LOG_FETCH_LIMIT,
        tail: true,
      },
      silent: true,
    });
    runLogLines.value = res.lines || [];
    runLogMeta.value = {
      ...createEmptyRunLogMeta(),
      ...res,
    };
  } catch (e) {
    console.error("获取运行日志失败", e);
  } finally {
    runLogLoading.value = false;
  }
}

function downloadRunLog() {
  if (!selectedTask.value) return;
  if (!runLogMeta.value.exists) {
    ElMessage.info("暂无可下载的运行日志");
    return;
  }
  const taskId = selectedTask.value.id || selectedTask.value.task?.id;
  request
    .get(`/training/remote/logs/${taskId}/download-url`, {
      silent: true,
    })
    .then((res) => {
      if (res.download_url) {
        window.open(res.download_url, "_blank", "noopener");
      }
    })
    .catch((error) => {
      ElMessage.error(error.response?.data?.detail || "获取运行日志下载地址失败");
    });
}

function downloadJson(payload, filename) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function canCancelTask(task) {
  return Boolean(task) && shouldPollMonitorTask(task);
}

function canDeleteTask(task) {
  return Boolean(task) && !shouldPollMonitorTask(task);
}

function shouldPollMonitorTask(task) {
  return (
    Boolean(task) &&
    !TERMINAL_TRAINING_STATUSES.includes(String(task.status || "").toLowerCase())
  );
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
  monitorActiveTab.value = "metrics";
  resetRunLog();
  lastMonitorRefreshAt.value = null;
  await nextTick();
  // 延迟初始化图表，确保 Element Plus 组件完成布局
  setTimeout(() => {
    initCharts();
  }, 100);
  await refreshMonitor();
  if (shouldPollMonitorTask(selectedTask.value)) {
    startPolling();
  } else {
    stopPolling();
  }
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

async function refreshMonitor({ manual = false } = {}) {
  if (manual && !canManuallyRefreshMonitor.value) return;
  await fetchMetrics({ force: true });
}

async function fetchMetrics({ force = false } = {}) {
  if (!selectedTask.value) return;
  if (!force && !shouldPollMonitorTask(selectedTask.value)) {
    stopPolling();
    return;
  }
  if (monitorRefreshing.value) return;
  monitorRefreshing.value = true;
  try {
    const taskId = selectedTask.value.id || selectedTask.value.task?.id;
    const res = await request.get(`/training/metrics/${taskId}`, {
      silent: true,
    });
    const metrics = res.metrics || [];
    const statusUrl = isRemoteTask(selectedTask.value)
      ? `/training/remote/status/${taskId}`
      : `/training/status/${taskId}`;
    const statusRes = await request.get(statusUrl, {
      silent: true,
    });
    if (statusRes) {
      selectedTask.value = normalizeTaskStatus(selectedTask.value, statusRes);
    }
    syncMonitorTab();
    if (metrics.length > 0) updateCharts(metrics);
    await fetchRunLog();
    if (!shouldPollMonitorTask(selectedTask.value)) {
      stopPolling();
    }
  } catch (e) {
    console.error("获取指标失败", e);
    await fetchRunLog();
  } finally {
    const refreshedAt = Date.now();
    lastMonitorRefreshAt.value = refreshedAt;
    refreshClockNow.value = refreshedAt;
    monitorRefreshing.value = false;
  }
}

function syncMonitorTab() {
  if (hasTrainingError.value) {
    monitorActiveTab.value = "logs";
  } else if (monitorActiveTab.value === "error") {
    monitorActiveTab.value = "metrics";
  }
}

function handleMonitorTabChange(name) {
  if (name === "metrics") {
    nextTick(handleChartResize);
  }
}

function isRemoteTask(task) {
  return task?.device === "remote" || Boolean(task?.remote);
}

function normalizeTaskStatus(currentTask, statusRes) {
  if (statusRes.task) {
    return {
      ...currentTask,
      ...statusRes.task,
      latest_metric: statusRes.latest_metric,
      is_running: statusRes.is_running,
    };
  }
  return {
    ...currentTask,
    ...statusRes,
  };
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
  if (!shouldPollMonitorTask(selectedTask.value)) return;
  pollTimer = setInterval(() => {
    if (!shouldPollMonitorTask(selectedTask.value)) {
      stopPolling();
      return;
    }
    if (!monitorRefreshing.value && monitorAutoRefreshRemainingMs.value <= 0) {
      refreshMonitor();
    }
  }, MONITOR_POLL_TICK_MS);
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function createTask() {
  if (!trainForm.value.dataset_id) {
    ElMessage.warning("请选择状态为已上传的数据集");
    return;
  }
  if (!selectedDataset.value || !isDatasetTrainable(selectedDataset.value)) {
    ElMessage.warning("只能选择状态为已上传的数据集");
    return;
  }
  creating.value = true;
  try {
    const payload = {
      dataset_id: trainForm.value.dataset_id,
      model_name: trainForm.value.model_name,
      epochs: trainForm.value.epochs,
      img_size: trainForm.value.img_size,
      batch_size: trainForm.value.batch_size,
      optimizer: trainForm.value.optimizer,
      lr0: trainForm.value.lr0,
    };
    const res = await request.post("/training/remote/start", payload);
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
    const res = await request.get("/training/remote/datasets");
    datasetList.value = res.datasets || [];
    const currentAvailable = availableTrainingDatasets.value.some(
      (dataset) => dataset.dataset_id === trainForm.value.dataset_id,
    );
    if (!currentAvailable) {
      trainForm.value.dataset_id =
        availableTrainingDatasets.value[0]?.dataset_id || "";
    }
  } catch {
    /* ignore */
  }
}

function filterTrainingDatasets(value) {
  datasetNameFilter.value = value;
}

function onDatasetSelectVisibleChange(visible) {
  if (!visible) {
    datasetNameFilter.value = "";
  }
}

function openDatasetUploadPage() {
  const { href } = router.resolve({
    path: "/datasets",
    query: { openUpload: "1" },
  });
  window.open(href, "_blank", "noopener");
}

async function selectTaskFromQuery() {
  const taskUuid = String(route.query.task_uuid || "").trim();
  if (!taskUuid) return;
  const task = taskList.value.find(
    (item) => item.task_uuid === taskUuid || String(item.id) === taskUuid,
  );
  if (task) {
    await selectTask(task);
  }
}

function formatDatasetOption(dataset) {
  const size = formatBytes(dataset.actual_size || dataset.expected_size);
  return `${getDatasetName(dataset)}-${formatDatasetUploadTime(dataset)}-${size}`;
}

function isDatasetTrainable(dataset) {
  return dataset?.status === "UPLOADED";
}

function getDatasetName(dataset) {
  return dataset?.dataset_name || dataset?.name || dataset?.dataset_id || "-";
}

function formatDatasetUploadTime(dataset) {
  return formatDate(
    dataset?.server_verified_at ||
      dataset?.client_completed_at ||
      dataset?.updated_at ||
      dataset?.created_at,
  );
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!size) return "0 MB";
  if (size >= 1024 * 1024 * 1024) {
    return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function formatRefreshCountdown(valueMs) {
  const seconds = Math.max(Math.ceil(valueMs / 1000), 0);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSeconds = seconds % 60;
  if (!remainSeconds) return `${minutes}分钟`;
  return `${minutes}分${String(remainSeconds).padStart(2, "0")}秒`;
}

async function stopTask(task) {
  try {
    await ElMessageBox.confirm("确定要取消训练吗？", "确认取消", {
      type: "warning",
      confirmButtonText: "取消训练",
      cancelButtonText: "关闭",
    });
    const taskId = task.id || task.task?.id;
    const stopUrl = isRemoteTask(task)
      ? `/training/remote/stop/${taskId}`
      : `/training/stop/${taskId}`;
    await request.post(stopUrl);
    ElMessage.success("已停止");
    await fetchTasks();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("停止失败");
  }
}

async function deleteTask(task) {
  const taskId = task.id || task.task?.id;
  try {
    await ElMessageBox.confirm(
      `删除训练记录 ${task.task_uuid || taskId} 会同时删除该训练产生的模型、训练产物和运行日志，且不可恢复。确定继续吗？`,
      "确认删除",
      {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
      },
    );
    await request.delete(`/training/tasks/${taskId}`, {
      params: { cascade_models: true },
    });
    ElMessage.success("训练记录已删除");
    if (
      selectedTask.value &&
      (selectedTask.value.id || selectedTask.value.task?.id) === taskId
    ) {
      selectedTask.value = null;
      resetRunLog();
      stopPolling();
    }
    await fetchTasks();
  } catch (e) {
    if (e !== "cancel") {
      ElMessage.error(e.response?.data?.detail || "删除训练记录失败");
    }
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

async function exportTrainingReport() {
  if (!selectedTask.value) return;
  reportExporting.value = true;
  try {
    const task = selectedTask.value;
    const taskId = task.id || task.task?.id;
    const [metricsRes, artifactsRes] = await Promise.all([
      request.get(`/training/metrics/${taskId}`, { silent: true }),
      isRemoteTask(task)
        ? request
            .get(`/training/remote/artifacts/${taskId}`, { silent: true })
            .catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] }),
    ]);
    const metrics = metricsRes.metrics || [];
    const latestMetric = metrics.at(-1) || task.latest_metric || null;
    const report = {
      report_type: "training_report",
      generated_at: new Date().toISOString(),
      task: {
        id: task.id,
        task_uuid: task.task_uuid,
        status: task.status,
        model_name: task.model_name,
        device: task.device,
        epochs: task.epochs,
        current_epoch: task.current_epoch,
        progress: task.progress,
        img_size: task.img_size,
        batch_size: task.batch_size,
        optimizer: task.optimizer,
        lr0: task.lr0,
        dataset_path: task.dataset_path,
        data_yaml: task.data_yaml,
        created_at: task.created_at,
        started_at: task.started_at,
        completed_at: task.completed_at,
        error_message: task.error_message,
      },
      remote: task.remote || null,
      latest_metric: latestMetric,
      metrics,
      artifacts: artifactsRes.items || [],
      error_detail: trainingErrorDetail.value,
    };
    downloadJson(
      report,
      `training-report-${task.task_uuid || task.id}.json`,
    );
    ElMessage.success("训练报告已导出");
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "导出训练报告失败");
  } finally {
    reportExporting.value = false;
  }
}

onMounted(async () => {
  await Promise.all([fetchTasks(), fetchDatasets()]);
  await selectTaskFromQuery();
  refreshClockTimer = setInterval(() => {
    refreshClockNow.value = Date.now();
  }, 1000);
});

watch(
  () => route.query.task_uuid,
  async () => {
    if (!taskList.value.length) {
      await fetchTasks();
    }
    await selectTaskFromQuery();
  },
);

onBeforeUnmount(() => {
  stopPolling();
  if (refreshClockTimer) {
    clearInterval(refreshClockTimer);
    refreshClockTimer = null;
  }
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
  gap: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.monitor-title {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.monitor-info {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: #909399;
}
.dataset-select-row {
  align-items: center;
  display: grid;
  gap: 10px;
  grid-template-columns: minmax(0, 1fr) auto;
  width: 100%;
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
.training-log-panel {
  padding-top: 4px;
}
.training-log-toolbar {
  align-items: flex-start;
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr) auto;
  margin-bottom: 12px;
}
.training-log-meta {
  color: #606266;
  font-size: 13px;
  line-height: 28px;
}
.training-log-scrollbar {
  background: #1f2329;
  border: 1px solid #30363d;
  border-radius: 6px;
}
.training-log-lines {
  color: #e5e7eb;
  font-family:
    ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
    monospace;
  font-size: 12px;
  line-height: 1.6;
  padding: 12px 0;
}
.training-log-line {
  align-items: flex-start;
  display: grid;
  gap: 12px;
  grid-template-columns: 72px minmax(0, 1fr);
  min-height: 20px;
  padding: 0 14px;
}
.training-log-line-number {
  color: #8b949e;
  text-align: right;
  user-select: none;
}
.training-log-line-content {
  color: #e5e7eb;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.training-log-empty {
  color: #8b949e;
  font-size: 13px;
  padding: 18px;
}
.task-list-card,
.monitor-card,
.action-card,
.eval-card {
  margin-bottom: 20px;
}
.task-list-card {
  width: 100%;
}
.task-list-card :deep(.el-card__body) {
  box-sizing: border-box;
  width: 100%;
}
.task-table {
  width: 100%;
}
.monitor-action-button.el-button.is-text,
.monitor-action-button.el-button.is-text:not(.is-disabled):hover,
.monitor-action-button.el-button.is-text:not(.is-disabled):focus,
.monitor-action-button.el-button.is-text:not(.is-disabled):active {
  --el-button-active-bg-color: transparent;
  --el-button-hover-bg-color: transparent;
  --el-button-hover-border-color: transparent;
  background: transparent;
  background-color: transparent;
  border-color: transparent;
  box-shadow: none;
  color: var(--el-color-primary);
}
</style>
