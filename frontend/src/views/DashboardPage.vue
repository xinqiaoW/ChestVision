<template>
  <div class="page-container">
    <div class="page-header">
      <h2>数据看板</h2>
      <span class="page-subtitle">Dashboard</span>
    </div>

    <!-- 总览卡片 -->
    <div class="kpi-cards">
      <div class="kpi-card">
        <div class="kpi-icon kpi-blue">📊</div>
        <div class="kpi-info">
          <div class="kpi-num">{{ stats.total_detections || 0 }}</div>
          <div class="kpi-label">检测总次数</div>
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-icon kpi-red">⚠️</div>
        <div class="kpi-info">
          <div class="kpi-num">{{ stats.total_lesions || 0 }}</div>
          <div class="kpi-label">检出病灶总数</div>
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-icon kpi-green">⏱️</div>
        <div class="kpi-info">
          <div class="kpi-num">{{ stats.avg_inference_time_ms || 0 }}ms</div>
          <div class="kpi-label">平均推理耗时</div>
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-icon kpi-teal">🔬</div>
        <div class="kpi-info">
          <div class="kpi-num">
            {{
              stats.risk_distribution?.find(
                (r) => r.name === "high" || r.name === "critical",
              )?.value || 0
            }}
          </div>
          <div class="kpi-label">高风险/危急案例</div>
        </div>
      </div>
    </div>

    <!-- 图表区 -->
    <div class="charts-row">
      <div class="chart-card chart-large">
        <div class="chart-header">
          <h3>影像分析趋势</h3>
          <span class="chart-period">近7天</span>
        </div>
        <div ref="trendChartRef" class="chart-body"></div>
      </div>
      <div class="chart-card chart-small">
        <div class="chart-header">
          <h3>病灶类型分布</h3>
        </div>
        <div ref="lesionChartRef" class="chart-body"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import request from "@/utils/request";
import * as echarts from "echarts";
import { ElMessage } from "element-plus";
import { nextTick, onMounted, ref } from "vue";

const stats = ref({});
const trendChartRef = ref(null);
const lesionChartRef = ref(null);

onMounted(async () => {
  try {
    stats.value = await request.get("/dashboard/stats");
    await nextTick();
    renderCharts();
  } catch {
    ElMessage.error("数据看板加载失败，请稍后重试");
  }
});

function renderCharts() {
  if (trendChartRef.value && stats.value.trend?.length) {
    const chart = echarts.init(trendChartRef.value);
    const dates = stats.value.trend.map((t) => t.date);
    const counts = stats.value.trend.map((t) => t.count);
    chart.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 40, right: 20, top: 20, bottom: 30 },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: "#e8ecf0" } },
        axisTick: { show: false },
        axisLabel: { color: "#8c8c8c", fontSize: 12 },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        splitLine: { lineStyle: { color: "#f0f2f5" } },
        axisLabel: { color: "#8c8c8c", fontSize: 12 },
      },
      series: [
        {
          data: counts,
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(42,157,143,0.2)" },
              { offset: 1, color: "rgba(42,157,143,0)" },
            ]),
          },
          lineStyle: { color: "#2A9D8F", width: 3 },
          itemStyle: { color: "#2A9D8F" },
        },
      ],
    });
  }

  if (lesionChartRef.value && stats.value.lesion_distribution?.length) {
    const chart = echarts.init(lesionChartRef.value);
    chart.setOption({
      tooltip: { trigger: "item" },
      legend: { bottom: 0, textStyle: { color: "#4A5568", fontSize: 12 } },
      series: [
        {
          type: "pie",
          radius: ["55%", "78%"],
          center: ["50%", "45%"],
          data: stats.value.lesion_distribution,
          label: { show: false },
          itemStyle: { borderColor: "#fff", borderWidth: 3 },
        },
      ],
    });
  }
}
</script>

<style lang="scss" scoped>
.page-container {
  padding: $spacing-xl;
}

.page-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: $spacing-xl;
  h2 {
    font-size: 22px;
    font-weight: 700;
    color: $text-primary;
    margin: 0;
  }
  .page-subtitle {
    font-size: 13px;
    color: $text-secondary;
  }
}

.kpi-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: $spacing-md;
  margin-bottom: $spacing-xl;
}

.kpi-card {
  background: #fff;
  border-radius: $border-radius-lg;
  padding: 20px 22px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.03);
  display: flex;
  align-items: flex-start;
  gap: 16px;
  position: relative;
  overflow: hidden;
  transition: all 0.3s;
  &:hover {
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
    transform: translateY(-1px);
  }
}

.kpi-icon {
  width: 48px;
  height: 48px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
  &.kpi-blue {
    background: #eff6ff;
  }
  &.kpi-red {
    background: #fef2f2;
  }
  &.kpi-green {
    background: #f0fdf4;
  }
  &.kpi-teal {
    background: #f0fdfa;
  }
}

.kpi-info {
  flex: 1;
}

.kpi-num {
  font-size: 28px;
  font-weight: 800;
  color: $text-primary;
  letter-spacing: -0.5px;
  line-height: 1.2;
}

.kpi-label {
  font-size: 13px;
  color: $text-secondary;
  margin-top: 4px;
}

.kpi-trend {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 20px;
  &.up {
    color: #16a34a;
    background: #f0fdf4;
  }
  &.down {
    color: #dc2626;
    background: #fef2f2;
  }
}

.charts-row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: $spacing-md;
}

.chart-card {
  background: #fff;
  border-radius: $border-radius-lg;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.03);
  padding: 20px 24px;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  h3 {
    font-size: 15px;
    font-weight: 600;
    color: $text-primary;
    margin: 0;
  }
  .chart-period {
    font-size: 12px;
    color: $text-secondary;
    background: #f5f6f8;
    padding: 4px 10px;
    border-radius: 12px;
  }
}

.chart-body {
  height: 300px;
}

@media (max-width: 1024px) {
  .kpi-cards {
    grid-template-columns: repeat(2, 1fr);
  }
  .charts-row {
    grid-template-columns: 1fr;
  }
}
</style>
