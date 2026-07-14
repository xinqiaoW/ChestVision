<template>
  <div class="page-container">
    <h2>📋 检测历史记录</h2>

    <el-card shadow="never">
      <el-table
        :data="taskList"
        stripe
        v-loading="loading"
        @row-click="showDetail"
        style="cursor: pointer"
      >
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="task_type" label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small">{{
              row.task_type === "single" ? "单图" : row.task_type
            }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="病灶统计" min-width="200">
          <template #default="{ row }">
            <span v-if="row.total_objects > 0">
              <el-tag
                v-for="(count, name) in row.class_summary"
                :key="name"
                size="small"
                type="warning"
                style="margin-right: 4px"
                >{{ name }} ×{{ count }}</el-tag
              >
            </span>
            <span v-else class="text-secondary">未检出病灶</span>
          </template>
        </el-table-column>
        <el-table-column prop="total_objects" label="总数" width="60" />
        <el-table-column prop="inference_time_ms" label="耗时" width="100">
          <template #default="{ row }"
            >{{ row.inference_time_ms?.toFixed(0) }}ms</template
          >
        </el-table-column>
        <el-table-column prop="created_at" label="检测时间" width="170" />
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              text
              @click.stop="showDetail(row)"
            >
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="fetchTasks"
        />
      </div>
    </el-card>

    <!-- 详情弹窗 -->
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
        </el-descriptions>

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

const detailVisible = ref(false);
const detail = ref(null);

async function fetchTasks() {
  loading.value = true;
  try {
    const res = await getDetectionTasks({
      page: page.value,
      page_size: pageSize.value,
    });
    taskList.value = res.items;
    total.value = res.total;
  } catch {
    ElMessage.error("加载历史记录失败");
  } finally {
    loading.value = false;
  }
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
  padding: 20px;
  h2 {
    margin-bottom: 16px;
    font-size: 20px;
  }
}
.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.text-secondary {
  color: #909399;
  font-size: 13px;
}
</style>
