<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>患者管理</h2>
        <span class="page-subtitle">Patients</span>
      </div>
    </div>

    <el-card v-if="isAdmin" shadow="never" class="review-card">
      <template #header>
        <div class="review-header">
          <div>
            <b>AI 医生推荐待确认</b>
            <span>患者选择推荐医生后，会在这里等待管理员审核</span>
          </div>
          <el-badge
            :value="pendingReviews.length"
            :hidden="!pendingReviews.length"
          >
            <el-button size="small" @click="fetchPendingReviews"
              >刷新</el-button
            >
          </el-badge>
        </div>
      </template>

      <el-table
        v-if="pendingReviews.length"
        :data="pendingReviews"
        stripe
        v-loading="reviewLoading"
      >
        <el-table-column label="患者" min-width="150">
          <template #default="{ row }">
            <template v-if="row.patient">
              <b>{{ row.patient.real_name || row.patient.patient_code }}</b>
              <div class="cell-secondary">{{ row.patient.patient_code }}</div>
            </template>
            <el-tag v-else type="warning" size="small">未关联患者</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="检出病灶" min-width="150">
          <template #default="{ row }">
            <el-tag
              v-for="lesion in row.matched_lesions"
              :key="lesion"
              size="small"
              effect="plain"
              class="lesion-tag"
              >{{ lesionName(lesion) }}</el-tag
            >
          </template>
        </el-table-column>
        <el-table-column label="申请医生" min-width="160">
          <template #default="{ row }">
            <b>{{ row.display_name }}</b>
            <div class="cell-secondary">{{ row.specialty }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="match_score" label="AI匹配分" width="100" />
        <el-table-column label="申请人" min-width="130">
          <template #default="{ row }">
            {{ row.requested_by }}
            <div class="cell-secondary">{{ formatTime(row.selected_at) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="success"
              @click="handleReview(row, true)"
            >
              确认
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              @click="handleReview(row, false)"
            >
              驳回
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty
        v-else-if="!reviewLoading"
        description="暂无待确认的医生选择"
        :image-size="64"
      />
    </el-card>

    <el-card shadow="never">
      <el-table :data="patients" stripe v-loading="loading">
        <el-table-column prop="patient_code" label="编号" width="110" />
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column prop="real_name" label="姓名" width="100">
          <template #default="{ row }">{{ row.real_name || "-" }}</template>
        </el-table-column>
        <el-table-column prop="gender" label="性别" width="70" />
        <el-table-column prop="age" label="年龄" width="60" />
        <el-table-column label="当前医生" min-width="180">
          <template #default="{ row }">
            <el-tag
              v-for="doc in row.doctors"
              :key="doc.id"
              size="small"
              closable
              v-if="isAdmin"
              @close="handleRemoveRelation(doc.relation_id, row)"
              style="margin-right: 4px"
              >{{ doc.username }}</el-tag
            >
            <span v-else>{{
              row.doctors.map((d) => d.username).join(", ") || "未分配"
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" v-if="isAdmin" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              @click="showAssignDialog(row)"
            >
              分配医生
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" v-if="isDoctor" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              @click="$router.push(`/medical-records?patient=${row.id}`)"
            >
              查看病例
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <p
        v-if="patients.length === 0 && !loading"
        class="text-secondary"
        style="text-align: center; padding: 40px"
      >
        {{ isAdmin ? "暂无病人注册" : "暂无分配的病人" }}
      </p>
    </el-card>

    <!-- 分配医生弹窗（仅管理员可见） -->
    <el-dialog v-model="dialogVisible" title="分配医生" width="450px">
      <p>
        将
        <b>{{ selectedPatient?.username || selectedPatient?.patient_code }}</b>
        分配给：
      </p>
      <el-select
        v-model="selectedDoctorId"
        placeholder="选择医生"
        style="width: 100%"
      >
        <el-option
          v-for="doc in doctors"
          :key="doc.id"
          :label="doc.username"
          :value="doc.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!selectedDoctorId"
          @click="handleAssign"
        >
          确认分配
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import {
  confirmDoctorRecommendation,
  getPendingDoctorReviews,
  rejectDoctorRecommendation,
} from "@/api/doctorRecommendation";
import {
  assignPatient,
  getDoctors,
  getPatients,
  removeRelation,
} from "@/api/patient";
import { useUserStore } from "@/stores/user";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

const userStore = useUserStore();
const isAdmin = computed(() => userStore.userType === "admin");
const isDoctor = computed(() => userStore.userType === "doctor");

const loading = ref(false);
const patients = ref([]);
const doctors = ref([]);
const dialogVisible = ref(false);
const selectedPatient = ref(null);
const selectedDoctorId = ref(null);
const pendingReviews = ref([]);
const reviewLoading = ref(false);

const lesionNameMap = {
  Atelectasis: "肺不张",
  Calcification: "钙化",
  Consolidation: "实变",
  Effusion: "胸腔积液",
  Emphysema: "肺气肿",
  Fibrosis: "纤维化",
  Fracture: "骨折",
  Mass: "肿块",
  Nodule: "结节",
  Pneumothorax: "气胸",
};

function lesionName(value) {
  return lesionNameMap[value] || value;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
}

async function fetchPendingReviews() {
  if (!isAdmin.value) return;
  reviewLoading.value = true;
  try {
    const result = await getPendingDoctorReviews();
    pendingReviews.value = result.items || [];
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载待确认请求失败");
  } finally {
    reviewLoading.value = false;
  }
}

async function handleReview(row, confirmed) {
  try {
    const action = confirmed ? "确认" : "驳回";
    const { value } = await ElMessageBox.prompt(
      `${action}${row.display_name}的申请？${!row.patient && confirmed ? "本次未关联患者，确认后不会自动建立医患关系。" : ""}`,
      `${action}医生选择`,
      {
        confirmButtonText: action,
        cancelButtonText: "取消",
        inputPlaceholder: "审核备注（可选）",
        inputType: "textarea",
        type: confirmed ? "success" : "warning",
      },
    );
    const result = confirmed
      ? await confirmDoctorRecommendation(row.id, value || "")
      : await rejectDoctorRecommendation(row.id, value || "");
    ElMessage.success(result.message);
    await Promise.all([fetchPendingReviews(), fetchPatients()]);
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(error.response?.data?.detail || "审核操作失败");
  }
}

async function fetchPatients() {
  loading.value = true;
  try {
    patients.value = (await getPatients()).items;
  } catch {
    ElMessage.error("加载患者列表失败");
  } finally {
    loading.value = false;
  }
}

async function fetchDoctors() {
  if (!isAdmin.value) return;
  try {
    doctors.value = await getDoctors();
  } catch {
    /* ignore */
  }
}

function showAssignDialog(patient) {
  selectedPatient.value = patient;
  selectedDoctorId.value = null;
  dialogVisible.value = true;
}

async function handleAssign() {
  try {
    await assignPatient({
      doctor_id: selectedDoctorId.value,
      patient_id: selectedPatient.value.user_id,
    });
    ElMessage.success("分配成功");
    dialogVisible.value = false;
    fetchPatients();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "分配失败");
  }
}

async function handleRemoveRelation(relationId, patient) {
  try {
    await ElMessageBox.confirm(
      `确定解除与 ${patient.username} 的医患关系？`,
      "确认",
      { type: "warning" },
    );
    await removeRelation(relationId);
    ElMessage.success("已解除");
    fetchPatients();
  } catch {
    /* cancelled */
  }
}

onMounted(() => {
  fetchPatients();
  fetchDoctors();
  fetchPendingReviews();
});
</script>

<style lang="scss" scoped>
.page-container {
  padding: $spacing-xl;
}
.review-card {
  margin-bottom: 16px;
  border-radius: $border-radius-lg;
}
.review-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  span {
    display: block;
    margin-top: 4px;
    color: #909399;
    font-size: 12px;
  }
}
.cell-secondary {
  margin-top: 3px;
  color: #909399;
  font-size: 12px;
}
.lesion-tag {
  margin: 2px 4px 2px 0;
}
.text-secondary {
  color: #909399;
  text-align: center;
  padding: 40px;
}
</style>
