<template>
  <div class="detection-result-card">
    <div class="card-header">
      <el-icon><DataAnalysis /></el-icon>
      <span>检测结果</span>
      <el-tag size="small" type="success">
        {{ result.total_objects ?? 0 }} 个病灶
      </el-tag>
    </div>

    <div class="card-body">
      <!-- 单图标注图 -->
      <div class="result-image" v-if="annotatedImageSrc">
        <img
          :src="annotatedImageSrc"
          alt="检测标注图"
          @click="showFullImage = true"
        />
      </div>

      <!-- 统计信息 -->
      <div class="result-stats">
        <div class="stat-item">
          <span class="stat-label">推理耗时</span>
          <span class="stat-value"
            >{{
              result.inference_time || result.total_inference_time || 0
            }}ms</span
          >
        </div>
        <div class="stat-item">
          <span class="stat-label">病灶数量</span>
          <span class="stat-value">{{ result.total_objects ?? 0 }} 个</span>
        </div>
        <div class="stat-item" v-if="result.total_images">
          <span class="stat-label">图片数量</span>
          <span class="stat-value">{{ result.total_images }} 张</span>
        </div>

        <el-table
          v-if="classCountsArray.length > 0"
          :data="classCountsArray"
          size="small"
          style="margin-top: 12px"
        >
          <el-table-column prop="name" label="类别" />
          <el-table-column prop="count" label="数量" width="70" />
        </el-table>
      </div>
    </div>

    <!-- 全屏预览 -->
    <el-dialog v-model="showFullImage" title="检测标注图" width="80%">
      <img
        v-if="annotatedImageSrc"
        :src="annotatedImageSrc"
        style="width: 100%"
      />
    </el-dialog>
  </div>
</template>

<script setup>
import { DataAnalysis } from "@element-plus/icons-vue";
import { computed, ref } from "vue";

const props = defineProps({ result: { type: Object, required: true } });
const showFullImage = ref(false);

const annotatedImageSrc = computed(() => {
  if (props.result.annotated_image_base64)
    return `data:image/jpeg;base64,${props.result.annotated_image_base64}`;
  if (props.result.annotated_image_path)
    return `/api/detection/image?path=${encodeURIComponent(props.result.annotated_image_path)}`;
  return null;
});

const classCountsArray = computed(() => {
  const counts = props.result.class_counts || {};
  return Object.entries(counts).map(([name, count]) => ({ name, count }));
});
</script>

<style lang="scss" scoped>
.detection-result-card {
  margin-top: 12px;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e0e0e0;
  font-weight: 600;
  font-size: 14px;
}
.card-body {
  display: flex;
  gap: 16px;
  padding: 12px;
}
.result-image {
  flex: 1;
  min-width: 0;
  img {
    width: 100%;
    max-height: 300px;
    object-fit: contain;
    border-radius: 4px;
    cursor: pointer;
  }
}
.result-stats {
  flex: 0 0 200px;
}
.stat-item {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
}
.stat-label {
  color: #909399;
}
.stat-value {
  font-weight: 600;
  color: #303133;
}
</style>
