<template>
  <el-dialog
    :model-value="visible"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    width="min(680px, 92vw)"
    class="disclaimer-dialog"
    destroy-on-close
  >
    <template #header>
      <div class="disclaimer-header">
        <span class="header-icon">⚠️</span>
        <h2>用户须知与免责声明</h2>
      </div>
    </template>

    <div class="disclaimer-body">
      <p class="intro">
        欢迎使用
        <strong>ChestVision 智能影像分析平台</strong>（以下简称"本平台"）。
        在使用本平台前，请您仔细阅读以下内容。点击"我已知晓"即表示您已充分理解并同意本声明全部条款。
      </p>

      <el-divider />

      <section>
        <h3>一、辅助工具声明</h3>
        <p>
          本平台所提供的 AI
          影像分析、病灶检测、医生推荐等功能，均为<strong>临床辅助工具</strong>，
          旨在为医疗专业人员提供参考信息，<strong>不构成最终诊断意见或治疗方案</strong>。
          所有检测结果必须由具备执业资格的医师结合患者临床表现、实验室检查及其他影像学资料进行综合判断。
        </p>
      </section>

      <section>
        <h3>二、不替代专业医疗</h3>
        <p>
          本平台的 AI
          分析结果<strong>不能替代专业医师的诊断</strong>，不可作为自行用药、
          自行治疗或拒绝正规医疗的依据。如您有任何健康问题，请及时前往正规医疗机构就诊。
        </p>
      </section>

      <section>
        <h3>三、数据与隐私保护</h3>
        <p>
          本平台严格遵守《中华人民共和国个人信息保护法》《医疗卫生机构网络安全管理办法》等相关法律法规。
          您上传的医学影像及个人信息仅用于本平台范围内的辅助分析，不会被用于任何未经授权的商业用途。
          我们已采取合理的技术手段保障数据安全，但无法保证互联网传输的绝对安全。
        </p>
      </section>

      <section>
        <h3>四、使用责任</h3>
        <ul>
          <li>
            您应确保上传影像的合法性，不得上传包含他人隐私信息的影像资料。
          </li>
          <li>
            医生用户应独立判断 AI
            分析结果的可靠性，对最终诊断和治疗决策承担全部责任。
          </li>
          <li>
            因网络故障、系统维护、不可抗力等原因导致的服务中断，本平台不承担赔偿责任。
          </li>
        </ul>
      </section>

      <section>
        <h3>五、知识产权</h3>
        <p>
          本平台的软件、算法模型、界面设计及相关文档的知识产权归平台所有。
          未经书面授权，任何人不得复制、修改、反向工程或以其他方式使用本平台的任何组成部分。
        </p>
      </section>

      <el-divider />

      <div class="agreement-confirm">
        <el-checkbox v-model="agreed" size="large">
          <span class="agreement-text">
            我已仔细阅读并充分理解以上全部内容，同意遵守本平台的使用条款与免责声明
          </span>
        </el-checkbox>
      </div>
    </div>

    <template #footer>
      <el-button
        type="primary"
        size="large"
        :disabled="!agreed"
        :loading="confirming"
        @click="onConfirm"
      >
        我已知晓，开始使用
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from "vue";

const props = defineProps({
  visible: { type: Boolean, default: false },
});
const emit = defineEmits(["confirmed"]);

const agreed = ref(false);
const confirming = ref(false);

function onConfirm() {
  if (!agreed.value) return;
  confirming.value = true;
  // 短暂延迟给用户反馈
  setTimeout(() => {
    confirming.value = false;
    emit("confirmed");
  }, 300);
}
</script>

<style lang="scss" scoped>
.disclaimer-dialog {
  :deep(.el-dialog__header) {
    margin: 0;
    padding: 24px 28px 0;
  }
  :deep(.el-dialog__body) {
    padding: 12px 28px 0;
    max-height: 58vh;
    overflow-y: auto;
  }
  :deep(.el-dialog__footer) {
    padding: 16px 28px 24px;
    text-align: center;
  }
}

.disclaimer-header {
  display: flex;
  align-items: center;
  gap: 10px;

  .header-icon {
    font-size: 26px;
  }
  h2 {
    margin: 0;
    font-size: 20px;
    font-weight: 700;
    color: #303133;
  }
}

.disclaimer-body {
  color: #4a4e57;
  line-height: 1.8;
  font-size: 14px;

  .intro {
    color: #606266;
    font-size: 15px;
    strong {
      color: #2a9d8f;
    }
  }

  section {
    margin-bottom: 16px;

    h3 {
      font-size: 15px;
      font-weight: 600;
      color: #303133;
      margin: 0 0 6px;
      padding-left: 4px;
      border-left: 3px solid #2a9d8f;
      padding-left: 10px;
    }

    p {
      margin: 4px 0;
      strong {
        color: #e6523e;
      }
    }

    ul {
      margin: 4px 0;
      padding-left: 20px;
      li {
        margin: 3px 0;
        &::marker {
          color: #909399;
        }
      }
    }
  }
}

.agreement-confirm {
  display: flex;
  justify-content: center;
  padding: 8px 0;

  .agreement-text {
    font-size: 15px;
    font-weight: 500;
    color: #303133;
  }
}
</style>
