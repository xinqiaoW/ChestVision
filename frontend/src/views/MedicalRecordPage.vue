<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>病例管理</h2>
        <span class="page-subtitle">Records</span>
      </div>
      <el-button
        type="primary"
        size="large"
        @click="showCreateDialog"
        v-if="canEdit"
        >+ 新建病例</el-button
      >
    </div>

    <div class="filter-bar">
      <el-select
        v-model="filterPatientId"
        placeholder="全部患者"
        clearable
        @change="fetchRecords"
        style="width: 180px"
        :loading="loadingPatients"
      >
        <el-option
          v-for="p in patientOptions"
          :key="p.id"
          :label="`${p.patient_code} ${p.real_name || p.username}`"
          :value="p.id"
        />
      </el-select>
      <el-select
        v-model="filterStatus"
        placeholder="全部状态"
        clearable
        style="width: 180px"
        @change="fetchRecords"
      >
        <el-option label="草稿" value="draft" /><el-option
          label="完成"
          value="completed"
        /><el-option label="已审核" value="reviewed" />
      </el-select>
    </div>

    <div class="table-card">
      <el-table :data="records" v-loading="loading">
        <el-table-column label="编号" width="120"
          ><template #default="{ row }"
            >REC-{{ row.id }}</template
          ></el-table-column
        >
        <el-table-column label="患者" min-width="140">
          <template #default="{ row }">{{
            row.patient_name || row.patient_code || "-"
          }}</template>
        </el-table-column>
        <el-table-column label="检查类型" width="130">
          <template #default="{ row }"
            ><span class="scan-type">{{
              typeLabel(row.record_type)
            }}</span></template
          >
        </el-table-column>
        <el-table-column prop="visit_date" label="就诊时间" width="160" />
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <span :class="['status-pill', row.record_status]">{{
              statusPillLabel(row.record_status)
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="showDetail(row)"
              >查看</el-button
            >
            <el-button
              size="small"
              link
              type="primary"
              @click="printRecord(row)"
              >打印</el-button
            >
            <el-button
              size="small"
              link
              type="primary"
              @click="showEditDialog(row)"
              v-if="canEdit"
              >编辑</el-button
            >
            <el-button
              size="small"
              link
              type="danger"
              @click="handleDelete(row)"
              v-if="isAdmin"
              >删除</el-button
            >
          </template>
        </el-table-column>
      </el-table>
      <p v-if="!records.length && !loading" class="empty-state">暂无病例记录</p>
    </div>

    <!-- 创建/编辑弹窗 -->
    <el-dialog
      v-model="formVisible"
      :title="editingRecord ? '编辑病例' : '新建病例'"
      width="700px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="90px" label-position="top">
        <el-form-item v-if="!editingRecord" label="患者" required>
          <el-select
            v-model="form.patient_profile_id"
            placeholder="请选择患者"
            filterable
            clearable
            :loading="loadingPatients"
            style="width: 100%"
          >
            <el-option
              v-for="p in patientOptions"
              :key="p.id"
              :label="`${p.patient_code} ${p.real_name || p.username || ''}`"
              :value="p.id"
            />
          </el-select>
          <div
            v-if="!loadingPatients && !patientOptions.length"
            class="patient-select-empty"
          >
            暂无可选患者，请先在患者管理中建立医患关系。
          </div>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="就诊类型">
              <el-select v-model="form.record_type" style="width: 100%">
                <el-option label="门诊" value="outpatient" />
                <el-option label="住院" value="inpatient" />
                <el-option label="复诊" value="follow_up" />
                <el-option label="急诊" value="emergency" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="就诊日期">
              <el-date-picker
                v-model="form.visit_date"
                type="date"
                value-format="YYYY-MM-DD"
                style="width: 100%"
                placeholder="选择日期"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="状态" v-if="editingRecord">
          <el-radio-group v-model="form.record_status">
            <el-radio value="draft">草稿</el-radio>
            <el-radio value="completed">已完成</el-radio>
            <el-radio value="reviewed">已审核</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="主诉">
          <el-input
            v-model="form.chief_complaint"
            type="textarea"
            :rows="2"
            placeholder="患者就诊主要原因"
          />
        </el-form-item>
        <el-form-item label="现病史">
          <el-input
            v-model="form.present_illness"
            type="textarea"
            :rows="2"
            placeholder="发病经过、症状演变"
          />
        </el-form-item>
        <el-form-item label="既往史">
          <el-input
            v-model="form.past_history"
            type="textarea"
            :rows="2"
            placeholder="过往疾病/手术/用药"
          />
        </el-form-item>
        <el-form-item label="家族史">
          <el-input
            v-model="form.family_history"
            type="textarea"
            :rows="1"
            placeholder="家族病史"
          />
        </el-form-item>
        <el-form-item label="体格检查">
          <el-input
            v-model="form.physical_examination"
            type="textarea"
            :rows="2"
            placeholder="T/P/R/BP、心肺听诊等"
          />
        </el-form-item>
        <el-form-item label="治疗方案">
          <el-input
            v-model="form.treatment_plan"
            type="textarea"
            :rows="2"
            placeholder="治疗计划"
          />
        </el-form-item>
        <el-form-item label="医生备注">
          <el-input
            v-model="form.doctor_notes"
            type="textarea"
            :rows="2"
            placeholder="备注"
          />
        </el-form-item>
        <el-form-item label="附件" v-if="editingRecord">
          <el-upload
            :action="`/api/medical-records/${editingRecord.id}/attachments`"
            :headers="{ Authorization: 'Bearer ' + token }"
            :on-success="onUploadSuccess"
            :show-file-list="false"
          >
            <el-button size="small">📎 上传附件</el-button>
          </el-upload>
          <div v-if="attachments.length" style="margin-top: 6px">
            <el-tag
              v-for="a in attachments"
              :key="a.id"
              closable
              size="small"
              style="margin-right: 4px"
              @close="deleteAttachment(a.id)"
              >{{ a.file_name }}</el-tag
            >
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave" :loading="saving">
          {{ editingRecord ? "保存修改" : "创建病例" }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="病例详情" width="700px">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="患者编号">{{
            detail.patient_code
          }}</el-descriptions-item>
          <el-descriptions-item label="就诊类型">{{
            typeLabel(detail.record_type)
          }}</el-descriptions-item>
          <el-descriptions-item label="主诉" :span="2">{{
            detail.chief_complaint || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="现病史" :span="2">{{
            detail.present_illness || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="既往史" :span="2">{{
            detail.past_history || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="家族史" :span="2">{{
            detail.family_history || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="体格检查" :span="2">{{
            detail.physical_examination || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="治疗方案" :span="2">{{
            detail.treatment_plan || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="医生备注" :span="2">{{
            detail.doctor_notes || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="就诊日期">{{
            detail.visit_date || "-"
          }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{
            detail.record_status
          }}</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import {
  createMedicalRecord,
  deleteMedicalRecord,
  getMedicalRecord,
  getMedicalRecords,
  updateMedicalRecord,
} from "@/api/medicalRecord";
import { getPatients } from "@/api/patient";
import { useUserStore } from "@/stores/user";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const userStore = useUserStore();
const isAdmin = computed(() => userStore.userType === "admin");
const isDoctor = computed(() => userStore.userType === "doctor");
const canEdit = computed(() => isAdmin.value || isDoctor.value);

const loading = ref(false);
const records = ref([]);
const detail = ref(null);
const detailVisible = ref(false);
const formVisible = ref(false);
const editingRecord = ref(null);
const saving = ref(false);
const filterPatientId = ref(null);
const filterStatus = ref(null);

function statusPillLabel(s) {
  const map = {
    draft: "待处理",
    completed: "AI已分析",
    reviewed: "医生已审核",
  };
  return map[s] || s;
}
const patientOptions = ref([]);
const loadingPatients = ref(false);
const attachments = ref([]);
const token = localStorage.getItem("chestx_token") || "";

const form = ref({
  patient_profile_id: null,
  record_type: "outpatient",
  record_status: "draft",
  chief_complaint: "",
  present_illness: "",
  past_history: "",
  family_history: "",
  physical_examination: "",
  treatment_plan: "",
  doctor_notes: "",
  visit_date: null,
});

function typeLabel(type) {
  const map = {
    outpatient: "门诊",
    inpatient: "住院",
    follow_up: "复诊",
    emergency: "急诊",
  };
  return map[type] || type;
}
function typeColor(type) {
  const map = {
    outpatient: "primary",
    inpatient: "warning",
    follow_up: "success",
    emergency: "danger",
  };
  return map[type] || "info";
}

async function fetchRecords() {
  loading.value = true;
  try {
    const params = {};
    if (filterPatientId.value)
      params.patient_profile_id = filterPatientId.value;
    records.value = (await getMedicalRecords(params)).items;
  } catch {
    ElMessage.error("加载病例列表失败");
  } finally {
    loading.value = false;
  }
}

async function fetchPatientOptions() {
  if (!canEdit.value) return;
  loadingPatients.value = true;
  try {
    const response = await getPatients();
    patientOptions.value = response.items || [];
  } catch (error) {
    patientOptions.value = [];
    ElMessage.error(error.response?.data?.detail || "加载患者列表失败");
  } finally {
    loadingPatients.value = false;
  }
}

async function showDetail(row) {
  try {
    detail.value = await getMedicalRecord(row.id);
    detailVisible.value = true;
  } catch {
    ElMessage.error("加载详情失败");
  }
}

async function showCreateDialog() {
  editingRecord.value = null;
  form.value = {
    patient_profile_id: filterPatientId.value || null,
    record_type: "outpatient",
    record_status: "draft",
    chief_complaint: "",
    present_illness: "",
    past_history: "",
    family_history: "",
    physical_examination: "",
    treatment_plan: "",
    doctor_notes: "",
    visit_date: null,
  };
  formVisible.value = true;
  if (!patientOptions.value.length) {
    await fetchPatientOptions();
  }
}

async function fetchAttachments(recordId) {
  try {
    const res = await fetch(`/api/medical-records/${recordId}/attachments`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json();
    attachments.value = data.items || [];
  } catch {
    attachments.value = [];
  }
}

function onUploadSuccess() {
  if (editingRecord.value) fetchAttachments(editingRecord.value.id);
}

async function deleteAttachment(attId) {
  if (!editingRecord.value) return;
  try {
    await fetch(
      `/api/medical-records/${editingRecord.value.id}/attachments/${attId}`,
      {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      },
    );
    fetchAttachments(editingRecord.value.id);
  } catch {
    /* ignore */
  }
}

function showEditDialog(row) {
  editingRecord.value = row;
  // 先获取完整数据再填充
  getMedicalRecord(row.id).then((data) => {
    form.value = {
      patient_profile_id: data.patient_profile_id,
      record_type: data.record_type || "outpatient",
      record_status: data.record_status || "draft",
      chief_complaint: data.chief_complaint || "",
      present_illness: data.present_illness || "",
      past_history: data.past_history || "",
      family_history: data.family_history || "",
      physical_examination: data.physical_examination || "",
      treatment_plan: data.treatment_plan || "",
      doctor_notes: data.doctor_notes || "",
      visit_date: data.visit_date || null,
    };
    formVisible.value = true;
    fetchAttachments(row.id);
  });
}

async function handleSave() {
  saving.value = true;
  try {
    // 格式化日期
    const data = { ...form.value };
    if (data.visit_date instanceof Date) {
      data.visit_date = data.visit_date.toISOString().split("T")[0];
    }
    if (editingRecord.value) {
      await updateMedicalRecord(editingRecord.value.id, data);
      ElMessage.success("更新成功");
    } else {
      if (!data.patient_profile_id) {
        ElMessage.warning("请先选择患者");
        saving.value = false;
        return;
      }
      await createMedicalRecord(data);
      ElMessage.success("创建成功");
    }
    formVisible.value = false;
    fetchRecords();
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "操作失败");
  } finally {
    saving.value = false;
  }
}

async function printRecord(row) {
  const token = localStorage.getItem("chestx_token");
  const res = await fetch(`/api/medical-records/${row.id}/print`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const html = await res.text();
  const win = window.open("", "_blank");
  win.document.write(html);
  win.document.close();
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm("确定删除该病例？", "确认", { type: "warning" });
    await deleteMedicalRecord(row.id);
    ElMessage.success("已删除");
    fetchRecords();
  } catch {
    /* cancelled */
  }
}

onMounted(() => {
  fetchRecords();
  fetchPatientOptions();
  // 从 URL 参数读取预设患者
  const patientParam = route.query.patient;
  if (patientParam) {
    filterPatientId.value = Number(patientParam);
  }
});
</script>

<style lang="scss" scoped>
.page-container {
  padding: $spacing-xl;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: $spacing-lg;
  flex-wrap: wrap;
  gap: 12px;
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
  margin-bottom: $spacing-md;
}

.table-card {
  background: #fff;
  border-radius: $border-radius-lg;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.03);
  overflow: hidden;
}

.scan-type {
  font-size: 13px;
  color: $text-regular;
  font-weight: 500;
}

.status-pill {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  &.completed {
    color: #16a34a;
    background: #f0fdf4;
  }
  &.reviewed {
    color: #2563eb;
    background: #eff6ff;
  }
  &.draft {
    color: #d97706;
    background: #fffbeb;
  }
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: $text-secondary;
  font-size: 14px;
}

.patient-select-empty {
  margin-top: 6px;
  color: $text-secondary;
  font-size: 12px;
  line-height: 1.5;
}
</style>
