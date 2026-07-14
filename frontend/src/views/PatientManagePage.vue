<template>
  <div class="page-container">
    <h2>👥 患者管理</h2>

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
});
</script>

<style lang="scss" scoped>
.page-container {
  padding: 20px;
  h2 {
    margin-bottom: 16px;
    font-size: 20px;
  }
}
.text-secondary {
  color: #909399;
}
</style>
