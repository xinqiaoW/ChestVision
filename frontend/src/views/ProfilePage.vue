<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h2>个人中心</h2>
        <span class="page-subtitle">Profile</span>
      </div>
    </div>

    <el-row :gutter="20">
      <!-- 左侧：基本信息卡片 -->
      <el-col :span="16">
        <!-- 基本信息 -->
        <el-card class="profile-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>📋 基本信息</span>
              <el-button type="primary" size="small" @click="startEditBasic"
                >编辑</el-button
              >
            </div>
          </template>

          <el-form v-if="!editingBasic" label-width="100px">
            <el-form-item label="用户名">{{
              userStore.user?.username
            }}</el-form-item>
            <el-form-item label="邮箱">{{
              userStore.user?.email
            }}</el-form-item>
            <el-form-item label="手机">{{
              userStore.user?.phone || "未设置"
            }}</el-form-item>
            <el-form-item label="角色">
              <el-tag :type="roleTagType">{{ roleLabel }}</el-tag>
            </el-form-item>
            <el-form-item label="注册时间">{{
              formatDate(userStore.user?.created_at)
            }}</el-form-item>
          </el-form>

          <el-form
            v-else
            ref="basicFormRef"
            :model="basicForm"
            :rules="basicRules"
            label-width="100px"
          >
            <el-form-item label="用户名" prop="username">
              <el-input v-model="basicForm.username" />
            </el-form-item>
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="basicForm.email" />
            </el-form-item>
            <el-form-item label="手机" prop="phone">
              <el-input v-model="basicForm.phone" />
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                :loading="savingBasic"
                @click="saveBasic"
                >保存</el-button
              >
              <el-button @click="cancelEditBasic">取消</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 修改密码 -->
        <el-card class="profile-card" shadow="hover">
          <template #header>
            <span>🔒 修改密码</span>
          </template>
          <el-form
            ref="pwdFormRef"
            :model="pwdForm"
            :rules="pwdRules"
            label-width="100px"
            style="max-width: 400px"
          >
            <el-form-item label="旧密码" prop="old_password">
              <el-input
                v-model="pwdForm.old_password"
                type="password"
                show-password
              />
            </el-form-item>
            <el-form-item label="新密码" prop="new_password">
              <el-input
                v-model="pwdForm.new_password"
                type="password"
                show-password
              />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirm_password">
              <el-input
                v-model="pwdForm.confirm_password"
                type="password"
                show-password
              />
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                :loading="savingPwd"
                @click="savePassword"
                >修改密码</el-button
              >
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 患者档案（仅患者） -->
        <el-card v-if="isPatient" class="profile-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span>🏥 健康档案</span>
              <el-button type="primary" size="small" @click="startEditPatient"
                >编辑</el-button
              >
            </div>
          </template>

          <el-form v-if="!editingPatient" label-width="110px">
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="姓名">{{
                  patientProfile?.real_name || "未设置"
                }}</el-form-item>
                <el-form-item label="年龄">{{
                  patientProfile?.age ?? "未设置"
                }}</el-form-item>
                <el-form-item label="血型">{{
                  patientProfile?.blood_type || "未设置"
                }}</el-form-item>
                <el-form-item label="身高">{{
                  patientProfile?.height_cm
                    ? patientProfile.height_cm + " cm"
                    : "未设置"
                }}</el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="性别">{{
                  patientProfile?.gender || "未设置"
                }}</el-form-item>
                <el-form-item label="出生日期">{{
                  patientProfile?.birth_date
                    ? formatDate(patientProfile.birth_date)
                    : "未设置"
                }}</el-form-item>
                <el-form-item label="体重">{{
                  patientProfile?.weight_kg
                    ? patientProfile.weight_kg + " kg"
                    : "未设置"
                }}</el-form-item>
                <el-form-item label="科室">{{
                  patientProfile?.department || "未设置"
                }}</el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="过敏史">{{
              patientProfile?.allergies || "无"
            }}</el-form-item>
            <el-form-item label="紧急联系人">{{
              patientProfile?.emergency_contact || "未设置"
            }}</el-form-item>
            <el-form-item label="紧急电话">{{
              patientProfile?.emergency_phone || "未设置"
            }}</el-form-item>
          </el-form>

          <el-form
            v-else
            ref="patientFormRef"
            :model="patientForm"
            label-width="110px"
          >
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="姓名"
                  ><el-input v-model="patientForm.real_name"
                /></el-form-item>
                <el-form-item label="年龄"
                  ><el-input-number
                    v-model="patientForm.age"
                    :min="0"
                    :max="150"
                /></el-form-item>
                <el-form-item label="血型">
                  <el-select v-model="patientForm.blood_type" clearable>
                    <el-option
                      v-for="b in bloodTypes"
                      :key="b"
                      :label="b"
                      :value="b"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="身高(cm)"
                  ><el-input-number
                    v-model="patientForm.height_cm"
                    :min="0"
                    :max="300"
                    :precision="1"
                /></el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="性别">
                  <el-select v-model="patientForm.gender" clearable>
                    <el-option label="男" value="Male" />
                    <el-option label="女" value="Female" />
                  </el-select>
                </el-form-item>
                <el-form-item label="出生日期">
                  <el-date-picker
                    v-model="patientForm.birth_date"
                    type="date"
                    value-format="YYYY-MM-DD"
                  />
                </el-form-item>
                <el-form-item label="体重(kg)"
                  ><el-input-number
                    v-model="patientForm.weight_kg"
                    :min="0"
                    :max="500"
                    :precision="1"
                /></el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="过敏史"
              ><el-input v-model="patientForm.allergies" type="textarea"
            /></el-form-item>
            <el-form-item label="紧急联系人"
              ><el-input v-model="patientForm.emergency_contact"
            /></el-form-item>
            <el-form-item label="紧急电话"
              ><el-input v-model="patientForm.emergency_phone"
            /></el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                :loading="savingPatient"
                @click="savePatient"
                >保存</el-button
              >
              <el-button @click="cancelEditPatient">取消</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <!-- 右侧：统计卡片 -->
      <el-col :span="8">
        <!-- 管理员统计 -->
        <template v-if="isAdmin">
          <el-card class="stats-side-card" shadow="hover">
            <template #header><span>📊 系统概览</span></template>
            <div class="stat-row" v-for="s in adminStats" :key="s.label">
              <span class="stat-label">{{ s.label }}</span>
              <span class="stat-value">{{ s.value }}</span>
            </div>
          </el-card>
        </template>

        <!-- 医生统计 -->
        <template v-if="isDoctor">
          <el-card class="stats-side-card" shadow="hover">
            <template #header><span>📊 工作概览</span></template>
            <div class="stat-row">
              <span class="stat-label">我的患者</span>
              <span class="stat-value">{{ doctorStats.patient_count }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">检测次数</span>
              <span class="stat-value">{{ doctorStats.total_detections }}</span>
            </div>
            <div class="stat-row">
              <span class="stat-label">病例记录</span>
              <span class="stat-value">{{ doctorStats.total_records }}</span>
            </div>
          </el-card>
        </template>

        <!-- 患者统计 -->
        <template v-if="isPatient">
          <el-card class="stats-side-card" shadow="hover">
            <template #header><span>📊 我的检测</span></template>
            <div class="stat-row">
              <span class="stat-label">检测次数</span>
              <span class="stat-value">{{
                patientStats.total_detections
              }}</span>
            </div>
          </el-card>
        </template>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import {
  changePasswordApi,
  getPatientProfileApi,
  getProfileStatsApi,
  updatePatientProfileApi,
  updateProfileApi,
} from "@/api/auth";
import { useUserStore } from "@/stores/user";
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

const userStore = useUserStore();

// ── 角色判断 ──
const isAdmin = computed(() => userStore.userType === "admin");
const isDoctor = computed(() => userStore.userType === "doctor");
const isPatient = computed(() => userStore.userType === "patient");

const roleLabel = computed(() => {
  const map = { admin: "管理员", doctor: "医生", patient: "患者" };
  return map[userStore.userType] || userStore.userType;
});
const roleTagType = computed(() => {
  const map = { admin: "danger", doctor: "warning", patient: "success" };
  return map[userStore.userType] || "info";
});

const bloodTypes = [
  "A",
  "B",
  "AB",
  "O",
  "A+",
  "B+",
  "AB+",
  "O+",
  "A-",
  "B-",
  "AB-",
  "O-",
];

// ── 基本信息编辑 ──
const editingBasic = ref(false);
const savingBasic = ref(false);
const basicFormRef = ref(null);
const basicForm = reactive({ username: "", email: "", phone: "" });
const basicRules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    { min: 3, max: 50 },
  ],
  email: [{ type: "email", message: "请输入有效邮箱", trigger: "blur" }],
};

function startEditBasic() {
  basicForm.username = userStore.user?.username || "";
  basicForm.email = userStore.user?.email || "";
  basicForm.phone = userStore.user?.phone || "";
  editingBasic.value = true;
}
function cancelEditBasic() {
  editingBasic.value = false;
}

async function saveBasic() {
  const valid = await basicFormRef.value?.validate().catch(() => false);
  if (!valid) return;
  savingBasic.value = true;
  try {
    await updateProfileApi({
      username: basicForm.username,
      email: basicForm.email,
      phone: basicForm.phone,
    });
    await userStore.fetchUserInfo();
    editingBasic.value = false;
    ElMessage.success("基本信息已更新");
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "更新失败");
  } finally {
    savingBasic.value = false;
  }
}

// ── 密码修改 ──
const savingPwd = ref(false);
const pwdFormRef = ref(null);
const pwdForm = reactive({
  old_password: "",
  new_password: "",
  confirm_password: "",
});
const validateConfirmPwd = (rule, value, callback) => {
  if (value !== pwdForm.new_password) callback(new Error("两次输入不一致"));
  else callback();
};
const pwdRules = {
  old_password: [{ required: true, message: "请输入旧密码", trigger: "blur" }],
  new_password: [
    {
      required: true,
      min: 6,
      max: 100,
      message: "密码长度6-100位",
      trigger: "blur",
    },
  ],
  confirm_password: [
    { required: true, validator: validateConfirmPwd, trigger: "blur" },
  ],
};

async function savePassword() {
  const valid = await pwdFormRef.value?.validate().catch(() => false);
  if (!valid) return;
  savingPwd.value = true;
  try {
    await changePasswordApi({
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    });
    ElMessage.success("密码修改成功，请重新登录");
    pwdForm.old_password = "";
    pwdForm.new_password = "";
    pwdForm.confirm_password = "";
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "修改失败");
  } finally {
    savingPwd.value = false;
  }
}

// ── 患者档案 ──
const patientProfile = ref(null);
const editingPatient = ref(false);
const savingPatient = ref(false);
const patientFormRef = ref(null);
const patientForm = reactive({
  real_name: "",
  age: null,
  gender: "",
  birth_date: null,
  blood_type: "",
  height_cm: null,
  weight_kg: null,
  allergies: "",
  emergency_contact: "",
  emergency_phone: "",
});

function startEditPatient() {
  const p = patientProfile.value;
  if (p)
    Object.assign(patientForm, {
      real_name: p.real_name || "",
      age: p.age,
      gender: p.gender || "",
      birth_date: p.birth_date,
      blood_type: p.blood_type || "",
      height_cm: p.height_cm,
      weight_kg: p.weight_kg,
      allergies: p.allergies || "",
      emergency_contact: p.emergency_contact || "",
      emergency_phone: p.emergency_phone || "",
    });
  editingPatient.value = true;
}
function cancelEditPatient() {
  editingPatient.value = false;
}

async function savePatient() {
  savingPatient.value = true;
  try {
    const res = await updatePatientProfileApi(patientForm);
    patientProfile.value = res;
    editingPatient.value = false;
    ElMessage.success("健康档案已更新");
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || "更新失败");
  } finally {
    savingPatient.value = false;
  }
}

// ── 统计数据 ──
const adminStats = ref([]);
const doctorStats = reactive({
  patient_count: 0,
  total_detections: 0,
  total_records: 0,
});
const patientStats = reactive({ total_detections: 0 });

async function loadStats() {
  try {
    const res = await getProfileStatsApi();
    if (isAdmin.value) {
      adminStats.value = [
        { label: "总用户", value: res.total_users },
        { label: "患者数", value: res.total_patients },
        { label: "医生数", value: res.total_doctors },
        { label: "检测总数", value: res.total_detections },
        { label: "模型数", value: res.total_models },
      ];
    } else if (isDoctor.value) {
      Object.assign(doctorStats, res);
    } else {
      Object.assign(patientStats, res);
    }
  } catch (e) {
    /* ignore */
  }
}

// ── 工具 ──
function formatDate(d) {
  if (!d) return "未知";
  return new Date(d).toLocaleDateString("zh-CN");
}

onMounted(async () => {
  if (isPatient.value) {
    try {
      patientProfile.value = await getPatientProfileApi();
    } catch (e) {
      /* */
    }
  }
  loadStats();
});
</script>

<style scoped>
.page-container {
  padding: 20px;
}
.profile-card {
  margin-bottom: 16px;
  border-radius: 12px;
  transition: box-shadow 0.3s;
}
.profile-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
}

.stats-side-card {
  margin-bottom: 16px;
  border-radius: 12px;
}
.stat-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
}
.stat-row:last-child {
  border-bottom: none;
}
.stat-row .stat-label {
  color: #909399;
}
.stat-row .stat-value {
  font-size: 20px;
  font-weight: bold;
  color: #409eff;
}
</style>
