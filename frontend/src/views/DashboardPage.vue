<template>
  <div class="page-container">
    <h2>📊 数据看板</h2>

    <!-- 总览卡片 -->
    <el-row :gutter="16" class="stats-cards">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-num">{{ stats.total_detections }}</div>
          <div class="stat-label">检测总次数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-num">{{ stats.total_lesions }}</div>
          <div class="stat-label">检出病灶总数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-num">{{ stats.avg_inference_time_ms }}ms</div>
          <div class="stat-label">平均推理耗时</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-num">
            {{
              stats.risk_distribution?.find(
                (r) => r.name === "high" || r.name === "critical",
              )?.value || 0
            }}
          </div>
          <div class="stat-label">高风险/危急案例</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 图表区 -->
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>📈 检测量趋势（近7天）</template>
          <div ref="trendChartRef" style="height: 300px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>🍩 病灶类型分布</template>
          <div ref="lesionChartRef" style="height: 300px"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>⚠️ 风险等级分布</template>
          <div ref="riskChartRef" style="height: 300px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" v-if="stats.doctor_workload?.length">
          <template #header>👨‍⚕️ 医生工作量</template>
          <el-table :data="stats.doctor_workload" size="small">
            <el-table-column prop="username" label="医生" />
            <el-table-column prop="patient_count" label="管理病人" width="80" />
            <el-table-column
              prop="detection_count"
              label="检测次数"
              width="80"
            />
            <el-table-column prop="lesion_count" label="检出病灶" width="80" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import request from "@/utils/request";
import * as echarts from "echarts";
import { nextTick, onMounted, ref } from "vue";

const stats = ref({});
const trendChartRef = ref(null);
const lesionChartRef = ref(null);
const riskChartRef = ref(null);

onMounted(async () => {
  try {
    stats.value = await request.get("/dashboard/stats");
    await nextTick();
    renderCharts();
  } catch {
    /* ignore */
  }
});

function renderCharts() {
  // 趋势图
  if (trendChartRef.value) {
    const chart = echarts.init(trendChartRef.value);
    chart.setOption({
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: stats.value.trend?.map((t) => t.date) || [],
      },
      yAxis: { type: "value", minInterval: 1 },
      series: [
        {
          data: stats.value.trend?.map((t) => t.count) || [],
          type: "line",
          smooth: true,
          areaStyle: { color: "rgba(42,157,143,0.15)" },
          lineStyle: { color: "#2A9D8F", width: 3 },
          itemStyle: { color: "#2A9D8F" },
        },
      ],
    });
  }

  // 病灶分布饼图
  if (lesionChartRef.value) {
    const chart = echarts.init(lesionChartRef.value);
    chart.setOption({
      tooltip: { trigger: "item" },
      series: [
        {
          type: "pie",
          radius: ["40%", "70%"],
          data: stats.value.lesion_distribution || [],
          label: { formatter: "{b}: {c}" },
        },
      ],
    });
  }

  // 风险等级柱状图
  if (riskChartRef.value) {
    const chart = echarts.init(riskChartRef.value);
    const riskColors = {
      low: "#67C23A",
      medium: "#E6A23C",
      high: "#F56C6C",
      critical: "#8B0000",
    };
    chart.setOption({
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: ["低风险", "中风险", "高风险", "危急"] },
      yAxis: { type: "value", minInterval: 1 },
      series: [
        {
          type: "bar",
          data: (stats.value.risk_distribution || []).map((r, i) => ({
            value: r.value,
            itemStyle: { color: riskColors[r.name] || "#909399" },
          })),
        },
      ],
    });
  }
}
</script>

<style lang="scss" scoped>
.page-container {
  padding: 20px;
  h2 {
    margin-bottom: 16px;
    font-size: 20px;
  }
}
.stats-cards {
  margin-bottom: 8px;
}
.stat-card {
  text-align: center;
  padding: 8px;
}
.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: #2a9d8f;
}
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
</style>
