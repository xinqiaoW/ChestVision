<template>
  <el-dialog
    :model-value="modelValue"
    width="min(1040px, 94vw)"
    class="doctor-recommendation-dialog"
    destroy-on-close
    @close="closeDialog"
  >
    <template #header>
      <div class="dialog-title">
        <div class="title-icon">AI</div>
        <div>
          <h2>为本次检查匹配医生</h2>
          <p>综合病灶、当前对话、患者历史与医生过往信息生成</p>
        </div>
      </div>
    </template>

    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="8" animated />
      <p>对话模型正在分析匹配依据…</p>
    </div>

    <el-result
      v-else-if="errorMessage"
      icon="warning"
      title="暂时无法生成医生推荐"
      :sub-title="errorMessage"
    >
      <template #extra>
        <el-button type="primary" @click="loadRecommendations(true)">重试</el-button>
      </template>
    </el-result>

    <template v-else>
      <div class="evidence-bar">
        <span class="evidence-label">本次已参考</span>
        <span>{{ evidence.lesions || 0 }} 个病灶</span>
        <span>{{ evidence.operator_messages || 0 }} 条当前用户对话</span>
        <span>{{ evidence.patient_messages || 0 }} 条患者对话</span>
        <span>{{ evidence.medical_records || 0 }} 份病例</span>
        <span>{{ evidence.doctor_self_statements || 0 }} 条医生自述</span>
      </div>

      <el-alert
        v-if="selectionMethod === 'fallback'"
        title="对话模型暂时不可用，当前展示规则排序结果，可点击重新分析。"
        type="warning"
        show-icon
        :closable="false"
        class="method-alert"
      />

      <div class="doctor-grid">
        <article
          v-for="doctor in recommendations"
          :key="doctor.id"
          :class="['doctor-card', { selected: doctor.status === 'selected' }]"
        >
          <div class="card-topline">
            <span class="rank-badge">{{ doctor.rank === 1 ? "AI 首选" : `推荐 ${doctor.rank}` }}</span>
            <span class="score">{{ Math.round(doctor.match_score) }}<small>匹配分</small></span>
          </div>

          <div class="doctor-identity">
            <el-avatar :size="52" :src="doctor.avatar || ''">
              {{ doctor.display_name?.slice(0, 1) || "医" }}
            </el-avatar>
            <div>
              <h3>{{ doctor.display_name }}</h3>
              <p>{{ doctor.specialty || "胸部疾病综合诊疗" }}</p>
            </div>
          </div>

          <div class="lesion-tags">
            <el-tag
              v-for="lesion in doctor.matched_lesions"
              :key="lesion"
              size="small"
              effect="plain"
            >
              {{ lesionLabel(lesion) }}
            </el-tag>
          </div>

          <p class="summary">{{ doctor.summary }}</p>
          <ul class="reason-list">
            <li v-for="reason in doctor.reasons?.slice(0, 3)" :key="reason">
              {{ reason }}
            </li>
          </ul>

          <div class="doctor-stats">
            <span>历史病例 {{ doctor.historical_case_count ?? 0 }}</span>
            <span>在管患者 {{ doctor.active_patient_count ?? 0 }}</span>
          </div>

          <el-button
            class="select-button"
            :type="doctor.status === 'selected' ? 'success' : 'primary'"
            :loading="selectingId === doctor.id"
            :disabled="doctor.status === 'selected'"
            @click="selectDoctor(doctor)"
          >
            {{ doctor.status === "selected" ? "已选择" : "选择这位医生" }}
          </el-button>
        </article>
      </div>

      <div class="dialog-footnote">
        <span>AI 推荐仅用于辅助分诊，不替代医疗机构正式转诊。</span>
        <el-button text type="primary" @click="loadRecommendations(true)">重新分析</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import {
  generateDoctorRecommendations,
  selectDoctorRecommendation,
} from "@/api/doctorRecommendation";
import { ElMessage } from "element-plus";
import { computed, ref, watch } from "vue";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  taskId: { type: Number, default: null },
  patientProfileId: { type: Number, default: null },
  sessionId: { type: Number, default: null },
});
const emit = defineEmits(["update:modelValue", "selected"]);

const loading = ref(false);
const errorMessage = ref("");
const recommendations = ref([]);
const contextUsed = ref({});
const selectionMethod = ref("ai");
const selectingId = ref(null);

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

const evidence = computed(() => contextUsed.value || {});

function closeDialog() {
  emit("update:modelValue", false);
}

function lesionLabel(name) {
  return lesionNameMap[name] || name;
}

async function loadRecommendations(refresh = false) {
  if (!props.taskId) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const result = await generateDoctorRecommendations({
      task_id: props.taskId,
      patient_profile_id: props.patientProfileId || undefined,
      session_id: props.sessionId || undefined,
      limit: 3,
      refresh,
    });
    recommendations.value = result.recommendations || [];
    contextUsed.value = result.context_used || {};
    selectionMethod.value = result.selection_method || "ai";
  } catch (error) {
    errorMessage.value =
      error.response?.data?.detail || error.message || "推荐服务请求失败";
  } finally {
    loading.value = false;
  }
}

async function selectDoctor(doctor) {
  selectingId.value = doctor.id;
  try {
    await selectDoctorRecommendation(doctor.id);
    recommendations.value.forEach((item) => {
      item.status = item.id === doctor.id ? "selected" : "recommended";
    });
    ElMessage.success(`已选择${doctor.display_name}，等待后续确认`);
    emit("selected", doctor);
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "选择失败，请重试");
  } finally {
    selectingId.value = null;
  }
}

watch(
  () => [props.modelValue, props.taskId],
  ([visible]) => {
    if (visible && props.taskId) loadRecommendations(false);
  },
);
</script>

<style lang="scss">
.doctor-recommendation-dialog {
  border-radius: 20px;
  overflow: hidden;

  .el-dialog__header { margin: 0; padding: 24px 28px 18px; border-bottom: 1px solid #edf2f7; }
  .el-dialog__body { padding: 22px 28px 24px; background: #f7f9fc; }
}

.dialog-title { display: flex; align-items: center; gap: 14px; }
.dialog-title .title-icon { width: 46px; height: 46px; border-radius: 14px; display: grid; place-items: center; color: #fff; font-weight: 800; background: linear-gradient(135deg, #2563eb, #14b8a6); box-shadow: 0 8px 24px rgba(37, 99, 235, .22); }
.dialog-title h2 { margin: 0; font-size: 21px; color: #172033; }
.dialog-title p { margin: 5px 0 0; color: #7b8498; font-size: 13px; }
.loading-state { padding: 22px 10px; text-align: center; color: #7b8498; }
.evidence-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 16px; }
.evidence-bar span { padding: 6px 10px; border-radius: 999px; background: #e9f0ff; color: #3e5d9c; font-size: 12px; }
.evidence-bar .evidence-label { padding-left: 0; color: #596273; background: transparent; font-weight: 600; }
.method-alert { margin-bottom: 16px; }
.doctor-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.doctor-card { position: relative; display: flex; flex-direction: column; min-height: 390px; padding: 19px; border: 1px solid #e5eaf2; border-radius: 17px; background: #fff; box-shadow: 0 10px 30px rgba(38, 51, 77, .07); transition: transform .2s, border-color .2s, box-shadow .2s; }
.doctor-card:hover { transform: translateY(-3px); border-color: #91b4ff; box-shadow: 0 14px 34px rgba(37, 99, 235, .13); }
.doctor-card.selected { border: 2px solid #2bb673; }
.card-topline { display: flex; justify-content: space-between; align-items: flex-start; }
.rank-badge { padding: 5px 9px; border-radius: 8px; background: #eef4ff; color: #2d63c8; font-size: 12px; font-weight: 700; }
.score { color: #146a62; font-size: 25px; font-weight: 800; line-height: 1; }
.score small { display: block; margin-top: 4px; color: #9aa2b2; font-size: 9px; font-weight: 500; text-align: right; }
.doctor-identity { display: flex; gap: 12px; align-items: center; margin: 15px 0 12px; }
.doctor-identity h3 { margin: 0; color: #192235; font-size: 18px; }
.doctor-identity p { margin: 4px 0 0; color: #697386; font-size: 12px; line-height: 1.45; }
.lesion-tags { display: flex; flex-wrap: wrap; gap: 5px; min-height: 25px; }
.summary { margin: 13px 0 10px; color: #4b5568; font-size: 13px; line-height: 1.65; }
.reason-list { margin: 0 0 14px; padding-left: 18px; color: #5f6878; font-size: 12px; line-height: 1.65; }
.reason-list li::marker { color: #3b82f6; }
.doctor-stats { display: flex; gap: 16px; margin-top: auto; padding: 11px 0; border-top: 1px solid #edf0f5; color: #8991a1; font-size: 11px; }
.select-button { width: 100%; }
.dialog-footnote { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; color: #8b93a3; font-size: 12px; }

@media (max-width: 820px) {
  .doctor-grid { grid-template-columns: 1fr; }
  .doctor-card { min-height: auto; }
}
</style>
