<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>选择医生</h2>
        <span class="page-subtitle">Doctor Selection</span>
      </div>
    </div>

    <!-- 已有医生 -->
    <el-alert
      v-if="myDoctor.has_doctor"
      title="您已有绑定的医生"
      type="success"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #default>
        <b>{{ myDoctor.doctor_name }}</b
        >， 绑定时间：{{ formatTime(myDoctor.assigned_at) }}
      </template>
    </el-alert>

    <!-- 待审批 -->
    <el-alert
      v-else-if="myDoctor.pending_request"
      title="您已提交医生分配请求，等待管理员审批"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <template #default>
        已选择：<b>{{ myDoctor.pending_request.doctor_name }}</b
        >， 提交时间：{{ formatTime(myDoctor.pending_request.created_at) }}
      </template>
    </el-alert>

    <!-- 医生列表（仅无关系且无待审批时显示） -->
    <template v-if="!myDoctor.has_doctor && !myDoctor.pending_request">
      <div class="info-tip">
        <el-alert type="info" :closable="false" show-icon>
          请从下方医生列表中选择一位医生。选择后将提交分配请求，需管理员审批后生效。选择后不可更改。
        </el-alert>
      </div>

      <el-card shadow="never" v-loading="loading">
        <el-table :data="doctors" stripe v-if="doctors.length">
          <el-table-column label="姓名" min-width="120">
            <template #default="{ row }">
              <b>{{ row.display_name || row.username }}</b>
            </template>
          </el-table-column>
          <el-table-column label="专业方向" min-width="160">
            <template #default="{ row }">
              {{ row.specialty || "未设置" }}
            </template>
          </el-table-column>
          <el-table-column label="职称" width="120">
            <template #default="{ row }">
              {{ row.title || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="科室" width="120">
            <template #default="{ row }">
              {{ row.department || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="医院" min-width="140">
            <template #default="{ row }">
              {{ row.hospital || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="在管患者" width="90">
            <template #default="{ row }">{{
              row.active_patient_count ?? 0
            }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button
                type="primary"
                size="small"
                :loading="requestingId === row.id"
                @click="handleRequestDoctor(row)"
              >
                选择该医生
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无可选医生" :image-size="64" />
      </el-card>
    </template>
  </div>
</template>

<script setup>
import {
  getMyDoctorRequestApi,
  requestDoctorApi,
} from "@/api/doctorAssignment";
import { getDoctorsWithProfileApi } from "@/api/patient";
import { useUserStore } from "@/stores/user";
import { ElMessage, ElMessageBox } from "element-plus";
import { onMounted, ref } from "vue";

const userStore = useUserStore();

const doctors = ref([]);
const loading = ref(false);
const myDoctor = ref({ has_doctor: false, pending_request: null });
const requestingId = ref(null);

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
}

async function fetchDoctors() {
  loading.value = true;
  try {
    doctors.value = await getDoctorsWithProfileApi();
  } catch (e) {
    ElMessage.error("加载医生列表失败");
  } finally {
    loading.value = false;
  }
}

async function fetchMyStatus() {
  try {
    myDoctor.value = await getMyDoctorRequestApi();
  } catch {
    /* ignore */
  }
}

async function handleRequestDoctor(doctor) {
  try {
    await ElMessageBox.confirm(
      `确定选择 <b>${doctor.display_name || doctor.username}</b> 作为您的主治医生？选择后不可更改。`,
      "确认选择",
      {
        confirmButtonText: "确认选择",
        cancelButtonText: "取消",
        type: "info",
        dangerouslyUseHTMLString: true,
      },
    );
    requestingId.value = doctor.id;
    await requestDoctorApi({ doctor_id: doctor.id, source: "manual" });
    ElMessage.success("已提交请求，等待管理员审批");
    fetchMyStatus();
  } catch (e) {
    if (e !== "cancel" && e?.response) {
      ElMessage.error(e.response?.data?.detail || "请求失败");
    }
  } finally {
    requestingId.value = null;
  }
}

onMounted(() => {
  fetchMyStatus();
  fetchDoctors();
});
</script>

<style lang="scss" scoped>
.page-container {
  padding: $spacing-xl;
}
.page-header {
  margin-bottom: 20px;
  h2 {
    margin: 0;
  }
  .page-subtitle {
    color: #909399;
    font-size: 13px;
  }
}
.info-tip {
  margin-bottom: 16px;
}
</style>
