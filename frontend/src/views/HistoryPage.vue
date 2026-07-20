<template>
  <div class="page-container">
    <div class="page-header">
      <h2>历史记录</h2>
      <span class="page-subtitle">系统历史记录 — AI检测与模型执行审计日志</span>
    </div>

    <div class="filter-bar">
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        value-format="YYYY-MM-DD"
        @change="fetchTasks"
      />
      <el-button @click="clearFilter" v-if="dateRange">清除筛选</el-button>
    </div>

    <div class="timeline" v-loading="loading">
      <div
        v-for="task in taskList"
        :key="task.id"
        class="timeline-item"
        @click="showDetail(task)"
      >
        <div class="timeline-dot" :class="task.task_type"></div>
        <div class="timeline-card">
          <div class="timeline-time">{{ task.created_at }}</div>
          <div class="timeline-title">
            <span :class="['event-badge', task.task_type]">{{
              typeLabel(task.task_type)
            }}</span>
            <span v-if="task.total_objects > 0"
              >检出 <b>{{ task.total_objects }}</b> 个病灶</span
            >
            <span v-else>未检出病灶</span>
            <span class="timeline-duration"
              >· {{ task.inference_time_ms?.toFixed(0) }}ms</span
            >
          </div>
          <div class="timeline-tags" v-if="task.class_summary">
            <span
              v-for="(count, name) in task.class_summary"
              :key="name"
              class="lesion-tag"
              >{{ name }} ×{{ count }}</span
            >
          </div>
        </div>
      </div>
      <div v-if="!taskList.length && !loading" class="empty-state">
        暂无检测记录
      </div>
    </div>

    <div class="pagination-wrap" v-if="total > pageSize">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="fetchTasks"
      />
    </div>

    <!-- 详情弹窗 — 完全保留原样 -->
    <el-dialog v-model="detailVisible" title="检测详情" width="700px">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="任务 ID">{{
            detail.id
          }}</el-descriptions-item>
          <el-descriptions-item label="检测类型">{{
            detail.task_type
          }}</el-descriptions-item>
          <el-descriptions-item label="病灶总数">{{
            detail.total_objects
          }}</el-descriptions-item>
          <el-descriptions-item label="推理耗时"
            >{{ detail.inference_time_ms?.toFixed(0) }}ms</el-descriptions-item
          >
          <el-descriptions-item label="置信度阈值">{{
            detail.conf_threshold
          }}</el-descriptions-item>
          <el-descriptions-item label="图像尺寸">{{
            detail.image_size
          }}</el-descriptions-item>
          <el-descriptions-item label="检测时间" :span="2">{{
            detail.created_at
          }}</el-descriptions-item>
          <el-descriptions-item label="AI 风险评级" v-if="detail.risk_level">
            <el-tag
              :type="
                detail.risk_level === 'low'
                  ? 'success'
                  : detail.risk_level === 'medium'
                    ? 'warning'
                    : 'danger'
              "
            >
              {{
                detail.risk_level === "low"
                  ? "低风险"
                  : detail.risk_level === "medium"
                    ? "中风险"
                    : detail.risk_level === "high"
                      ? "高风险"
                      : "危急"
              }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="完成时间">{{
            detail.completed_at || "-"
          }}</el-descriptions-item>
        </el-descriptions>

        <div v-if="detail.analysis_report" style="margin-top: 16px">
          <h4>🤖 AI 综合分析</h4>
          <div
            class="analysis-text"
            v-html="simpleMd(detail.analysis_report)"
          ></div>
        </div>

        <h4 style="margin-top: 20px">病灶列表</h4>
        <el-table
          :data="detail.objects"
          size="small"
          v-if="detail.objects?.length"
        >
          <el-table-column prop="class_name_cn" label="类别" width="100" />
          <el-table-column prop="class_name" label="英文名" width="120" />
          <el-table-column label="置信度" width="90">
            <template #default="{ row }">
              <el-progress
                :percentage="row.confidence * 100"
                :stroke-width="6"
                :color="row.confidence > 0.7 ? '#67C23A' : '#E6A23C'"
              />
            </template>
          </el-table-column>
          <el-table-column label="边界框" min-width="200">
            <template #default="{ row }">
              <code>{{ row.bbox?.join(", ") }}</code>
            </template>
          </el-table-column>
        </el-table>
        <p v-else class="text-secondary">未检出病灶</p>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { getDetectionTaskDetail, getDetectionTasks } from "@/api/detection";
import { ElMessage } from "element-plus";
import { onMounted, ref } from "vue";

const loading = ref(false);
const taskList = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(15);
const dateRange = ref(null);

const detailVisible = ref(false);
const detail = ref(null);

async function fetchTasks() {
  loading.value = true;
  try {
    const params = { page: page.value, page_size: pageSize.value };
    if (dateRange.value) {
      params.start_date = dateRange.value[0];
      params.end_date = dateRange.value[1];
    }
    const res = await getDetectionTasks(params);
    taskList.value = res.items;
    total.value = res.total;
  } catch {
    ElMessage.error("加载历史记录失败");
  } finally {
    loading.value = false;
  }
}

function clearFilter() {
  dateRange.value = null;
  page.value = 1;
  fetchTasks();
}

function typeLabel(type) {
  const m = { single: "单图检测", batch: "批量检测", zip: "ZIP检测" };
  return m[type] || type;
}

function simpleMd(text) {
  if (!text) return "";
  return text
    .replace(/### (.+)/g, "<h4>$1</h4>")
    .replace(/## (.+)/g, "<h3>$1</h3>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br>");
}

async function showDetail(row) {
  try {
    detail.value = await getDetectionTaskDetail(row.id);
    detailVisible.value = true;
  } catch {
    ElMessage.error("加载详情失败");
  }
}

onMounted(fetchTasks);
</script>

<style lang="scss" scoped>
.page-container {
  padding: $spacing-xl;
}

.page-header {
  margin-bottom: $spacing-lg;
  h2 {
    font-size: 22px;
    font-weight: 700;
    margin: 0 0 4px;
    color: $text-primary;
  }
  .page-subtitle {
    font-size: 13px;
    color: $text-secondary;
  }
}

.filter-bar {
  display: flex;
  gap: 12px;
  margin-bottom: $spacing-xl;
}

.timeline {
  position: relative;
  padding-left: 36px;
}
.timeline::before {
  content: "";
  position: absolute;
  left: 11px;
  top: 4px;
  bottom: 4px;
  width: 2px;
  background: #e8ecf0;
}

.timeline-item {
  position: relative;
  margin-bottom: 20px;
  cursor: pointer;
  &:last-child {
    margin-bottom: 0;
  }
}

.timeline-dot {
  position: absolute;
  left: -36px;
  top: 6px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #fff;
  border: 3px solid #2a9d8f;
  z-index: 1;
  &.batch {
    border-color: #4a7fd9;
  }
  &.zip {
    border-color: #d97706;
  }
}

.timeline-card {
  background: #fff;
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  transition: box-shadow 0.2s;
  &:hover {
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  }
}

.timeline-time {
  font-size: 12px;
  color: $text-secondary;
  margin-bottom: 6px;
}
.timeline-title {
  font-size: 14px;
  color: $text-primary;
}
.timeline-duration {
  font-size: 12px;
  color: $text-secondary;
  margin-left: 4px;
}

.event-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  margin-right: 8px;
  background: #f0fdfa;
  color: #1b7a6e;
  &.batch {
    background: #eff6ff;
    color: #4a7fd9;
  }
  &.zip {
    background: #fffbeb;
    color: #d97706;
  }
}

.timeline-tags {
  margin-top: 8px;
}
.lesion-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 2px 10px;
  background: #fef2f2;
  color: #dc2626;
  border-radius: 10px;
  font-size: 12px;
}

.pagination-wrap {
  margin-top: $spacing-xl;
}
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: $text-secondary;
}
</style>
