<template>
  <div class="chat-page">
    <!-- 消息列表 -->
    <div class="message-list" ref="msgListRef">
      <div
        v-for="(msg, i) in agentStore.messages"
        :key="i"
        :class="['message-row', `msg-${msg.role}`]"
      >
        <!-- AI 头像 -->
        <div class="msg-avatar" v-if="msg.role === 'assistant'">
          <span class="avatar-bot">🫁</span>
        </div>

        <div class="msg-body">
          <!-- 发送者名称 + 时间 -->
          <div class="msg-meta">
            <span class="msg-sender">{{
              msg.role === "user" ? "我" : "ChestVision AI"
            }}</span>
          </div>

          <!-- 消息气泡 -->
          <div
            :class="[
              'message-bubble',
              msg.role === 'user' ? 'user-bubble' : 'assistant-bubble',
            ]"
          >
            <div v-if="msg.role === 'user'" class="message-content">
              {{ msg.content }}
            </div>
            <div v-if="msg.image" class="msg-attachment">
              <img :src="msg.imagePreview" alt="附件" />
            </div>

            <div
              v-if="msg.role === 'assistant' && msg.loading"
              class="typing-indicator"
            >
              <span></span><span></span><span></span>
            </div>
            <div
              v-else-if="msg.role === 'assistant'"
              class="message-content markdown-body"
              v-html="renderMd(msg.content)"
            ></div>
            <div v-if="msg.downloadPdfUrl" class="msg-actions">
              <a
                href="#"
                @click.prevent="downloadReport(msg.downloadPdfUrl)"
                class="download-link"
              >
                📥 下载/打印报告
              </a>
            </div>
            <DetectionResultCard
              v-if="msg.detectionResult"
              :result="msg.detectionResult"
            />
          </div>

          <!-- 工具调用 -->
          <div v-if="msg.toolCall" class="tool-call-info">
            <el-tag size="small" type="info" effect="plain"
              >🔧 {{ msg.toolCall.tool }}</el-tag
            >
          </div>
        </div>

        <!-- 用户头像 -->
        <div class="msg-avatar" v-if="msg.role === 'user'">
          <el-avatar :size="32">{{ userStore.username?.charAt(0) }}</el-avatar>
        </div>
      </div>
    </div>

    <!-- 快捷操作栏 -->
    <div class="quick-actions">
      <el-select
        v-if="userStore.userType === 'doctor' || userStore.userType === 'admin'"
        v-model="selectedPatientId"
        placeholder="选择患者（可选）"
        clearable
        size="small"
        style="width: 220px"
        @change="onPatientChange"
      >
        <el-option
          v-for="p in patientList"
          :key="p.id"
          :label="`${p.patient_code} ${p.real_name || p.username}`"
          :value="p.id"
        />
      </el-select>
      <el-button @click="quickDetect('single')" :disabled="agentStore.isLoading"
        >📷 单图检测</el-button
      >
      <el-button @click="quickDetect('batch')" :disabled="agentStore.isLoading"
        >📁 批量/ZIP</el-button
      >
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <el-button
        class="attach-btn"
        @click="triggerFile"
        :disabled="agentStore.isLoading"
        circle
        >📎</el-button
      >
      <input
        ref="fileInputRef"
        type="file"
        accept="image/*,.zip"
        multiple
        style="display: none"
        @change="onFileSelect"
      />
      <el-input
        v-model="inputText"
        placeholder="输入消息，或上传胸片进行AI分析..."
        @keyup.enter="sendMsg"
        :disabled="agentStore.isLoading"
      />
      <el-button
        v-if="!agentStore.isLoading"
        type="primary"
        @click="sendMsg"
        :disabled="!inputText.trim() && !selectedFiles.length"
        >发送</el-button
      >
      <el-button v-else type="danger" @click="stopChat">停止</el-button>
    </div>
  </div>
</template>

<script setup>
import { detectBatch, detectSingle, detectZip } from "@/api/detection";
import { getPatients } from "@/api/patient";
import DetectionResultCard from "@/components/DetectionResultCard.vue";
import { useAgentStore } from "@/stores/agent";
import { useUserStore } from "@/stores/user";
import request from "@/utils/request";
import { streamChat } from "@/utils/stream";
import { ElMessage } from "element-plus";
import MarkdownIt from "markdown-it";
import { nextTick, onMounted, ref } from "vue";

const md = new MarkdownIt({ breaks: true, html: false });

const agentStore = useAgentStore();
const userStore = useUserStore();
const inputText = ref("");
const selectedFiles = ref([]);
const msgListRef = ref(null);
const fileInputRef = ref(null);
const selectedPatientId = ref(null);
const patientList = ref([]);

function scrollBottom() {
  nextTick(() => {
    if (msgListRef.value)
      msgListRef.value.scrollTop = msgListRef.value.scrollHeight;
  });
}

function triggerFile() {
  fileInputRef.value?.click();
}
function onFileSelect(e) {
  const files = Array.from(e.target.files);
  if (files.length) {
    selectedFiles.value = files;
    ElMessage.info(
      files.length === 1
        ? `${files[0].name} 已选择`
        : `${files.length} 个文件已选择`,
    );
  }
}

function renderMd(text) {
  return md.render(text || "");
}

async function sendMsg() {
  const text = inputText.value.trim();
  const files = selectedFiles.value;
  if (!text && !files.length) return;

  // 用户消息
  agentStore.addMessage({
    role: "user",
    content:
      text || (files.length > 1 ? `[${files.length}张胸片]` : "[图片检测]"),
    image: files.length === 1 ? files[0].name : null,
    imagePreview: files.length === 1 ? URL.createObjectURL(files[0]) : null,
    images: files.length > 1 ? files.map((f) => URL.createObjectURL(f)) : null,
  });
  inputText.value = "";
  selectedFiles.value = [];

  // ── "生成报告" 直接调 API ──
  if (text.includes("生成报告") && !files.length) {
    agentStore.addMessage({ role: "assistant", content: "", loading: true });
    scrollBottom();
    try {
      const res = await request.post("/reports/generate", { task_id: 0 });
      const aiMsg = agentStore.messages[agentStore.messages.length - 1];
      aiMsg.content = res.content;
      aiMsg.downloadPdfUrl = `/api/reports/${res.id}/pdf`;
      aiMsg.loading = false;
    } catch (e) {
      const aiMsg = agentStore.messages[agentStore.messages.length - 1];
      aiMsg.content = `生成报告失败：${e.response?.data?.detail || e.message}`;
      aiMsg.loading = false;
    }
    scrollBottom();
    return;
  }

  // AI 加载占位
  agentStore.addMessage({ role: "assistant", content: "", loading: true });
  scrollBottom();

  // ── 有图片：直接走检测 API（入库 + AI 分析）──
  if (files.length > 0) {
    const last = agentStore.messages[agentStore.messages.length - 1];
    last.content = "🔍 正在检测病灶...";

    const detectionResults = [];
    for (const f of files) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const detectRes = await request.post("/detection/detect", fd, {
          headers: { "Content-Type": "multipart/form-data" },
          timeout: 120000,
        });
        detectionResults.push(detectRes);
      } catch (e) {
        last.content = `${f.name} 检测失败：${e.response?.data?.detail || e.message}`;
        last.loading = false;
        return;
      }
    }

    last.loading = false;

    if (detectionResults.length === 1) {
      const r = detectionResults[0];
      const classCounts = {};
      r.objects.forEach((o) => {
        classCounts[o.class_name_cn] = (classCounts[o.class_name_cn] || 0) + 1;
      });
      last.detectionResult = {
        total_objects: r.total_objects,
        inference_time: r.inference_time_ms,
        class_counts: classCounts,
        detections: r.objects,
        annotated_image_base64: r.annotated_image_base64 || "",
      };
      last.content =
        r.total_objects > 0
          ? `检测完成，发现 ${r.total_objects} 个病灶：${r.objects.map((o) => o.class_name_cn).join("、")}`
          : "检测完成，未发现明显病灶。";
      if (r.ai_analysis?.report) {
        last.content += `\n\n### AI 综合分析\n${r.ai_analysis.report}`;
      }
    } else {
      const totalObjects = detectionResults.reduce(
        (s, r) => s + r.total_objects,
        0,
      );
      last.content = `批量检测完成，共 ${detectionResults.length} 张，发现 ${totalObjects} 个病灶。`;
      last.detectionResult = {
        total_objects: totalObjects,
        class_counts: {},
        detections: detectionResults.flatMap((r) => r.objects),
        annotated_image_base64: "",
        inference_time: detectionResults.reduce(
          (s, r) => s + r.inference_time_ms,
          0,
        ),
      };
    }
    scrollBottom();
    return;
  }

  // ── 纯文本：走 Agent SSE 对话 ──
  let fullContent = "";
  const stop = streamChat(
    "/api/chat/stream",
    { message: text, patient_profile_id: selectedPatientId.value || undefined },
    {
      onMessage: (data) => {
        if (data.type === "text_chunk") {
          fullContent += data.content;
          agentStore.updateLastAssistantMessage(fullContent);
          scrollBottom();
        } else if (data.type === "error") {
          const last = agentStore.messages[agentStore.messages.length - 1];
          last.content = data.content;
          last.loading = false;
        }
      },
      onDone: () => {
        const last = agentStore.messages[agentStore.messages.length - 1];
        if (last.loading) last.loading = false;
        agentStore.setLoading(false);
      },
      onError: (err) => {
        const last = agentStore.messages[agentStore.messages.length - 1];
        last.content = `出错：${err.message}`;
        last.loading = false;
        agentStore.setLoading(false);
      },
    },
  );
  agentStore.abortController = stop;
}

function onPatientChange() {
  if (selectedPatientId.value) {
    const p = patientList.value.find((pp) => pp.id === selectedPatientId.value);
    ElMessage.info(
      `已切换到患者：${p?.patient_code || selectedPatientId.value}`,
    );
  }
}

async function loadPatients() {
  if (userStore.userType === "doctor" || userStore.userType === "admin") {
    try {
      patientList.value = (await getPatients()).items;
    } catch {
      /* ignore */
    }
  }
}

async function downloadReport(url) {
  const token = localStorage.getItem("chestx_token");
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const html = await res.text();
  const win = window.open("", "_blank");
  win.document.write(html);
  win.document.close();
}

function stopChat() {
  agentStore.abort();
  const last = agentStore.messages[agentStore.messages.length - 1];
  if (last?.loading) {
    last.loading = false;
    last.content += "\n[已停止]";
  }
}

// 快捷检测 — 直接调快捷 API（不入库、不 AI 分析）
async function quickDetect(type) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = type === "single" ? "image/*" : "image/*,.zip";
  input.multiple = type !== "single";
  input.onchange = async (e) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    const isZip = files.some((f) => f.name.endsWith(".zip"));

    agentStore.addMessage({
      role: "user",
      content: isZip
        ? `[ZIP检测] ${files[0].name}`
        : `[快捷检测] ${files.length} 张胸片`,
      images: files.map((f) => URL.createObjectURL(f)),
    });
    agentStore.addMessage({
      role: "assistant",
      content: "🔍 检测中...",
      loading: true,
    });
    scrollBottom();

    try {
      const fd = new FormData();
      if (isZip) fd.append("file", files[0]);
      else if (files.length === 1) fd.append("file", files[0]);
      else files.forEach((f) => fd.append("files", f));

      const api = isZip
        ? detectZip
        : files.length === 1
          ? detectSingle
          : detectBatch;
      const result = await api(fd);
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content =
        result.total_objects > 0
          ? `检测完成！发现 ${result.total_objects} 个病灶`
          : "检测完成，未发现明显病灶";
      last.loading = false;
      last.detectionResult = result;
    } catch (err) {
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content = `检测失败：${err.message}`;
      last.loading = false;
    }
  };
  input.click();
}

onMounted(() => {
  loadPatients();
  if (agentStore.messages.length === 0) {
    agentStore.addMessage({
      role: "assistant",
      content:
        "你好！我是**胸片X光AI辅助诊断助手** 🫁\n\n我可以帮你：\n- 📷 上传胸片进行 AI 病灶检测\n- 📁 批量检测多张胸片或 ZIP 包\n- 💬 自然语言分析解读检测结果\n\n支持 10 种胸部病变：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸\n\n---\n*请上传胸片或输入消息开始*",
    });
  }
});
</script>

<style lang="scss" scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: $bg-color;
}
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

.message-row {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
  animation: msgIn 0.3s ease;
  &.msg-user {
    flex-direction: row-reverse;
  }
}
.msg-avatar {
  flex-shrink: 0;
  .avatar-bot {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    font-size: 20px;
    background: linear-gradient(135deg, #e6f7f5, #c8f0e8);
    border-radius: 10px;
  }
}
.msg-body {
  max-width: 72%;
}
.msg-meta {
  margin-bottom: 4px;
  .msg-sender {
    font-size: 12px;
    color: $text-secondary;
    font-weight: 500;
  }
}
.msg-user .msg-meta {
  text-align: right;
}

.message-bubble {
  padding: 12px 16px;
  border-radius: $border-radius-md;
  line-height: 1.65;
  font-size: 14px;
  word-break: break-word;
}
.user-bubble {
  background: linear-gradient(135deg, $primary-color, $primary-light);
  color: #fff;
  border-bottom-right-radius: $spacing-xs;
  box-shadow: 0 2px 8px rgba($primary-color, 0.2);
}
.assistant-bubble {
  background: $bg-white;
  border: 1px solid #e8ecf0;
  border-bottom-left-radius: $spacing-xs;
  box-shadow: $shadow-sm;
}
.message-content {
  white-space: pre-wrap;
}
.msg-attachment {
  margin-top: 8px;
  img {
    max-width: 220px;
    border-radius: $border-radius-sm;
  }
}
.msg-actions {
  margin-top: 10px;
}
.download-link {
  display: inline-block;
  padding: 6px 14px;
  background: #2a9d8f;
  color: #fff;
  border-radius: 6px;
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
  &:hover {
    background: #238b7e;
  }
}

.typing-indicator {
  display: flex;
  gap: 5px;
  padding: 6px 0;
  span {
    width: 7px;
    height: 7px;
    background: #bfbfbf;
    border-radius: 50%;
    animation: typing 1.2s infinite;
  }
  span:nth-child(2) {
    animation-delay: 0.2s;
  }
  span:nth-child(3) {
    animation-delay: 0.4s;
  }
}
.quick-actions {
  display: flex;
  gap: 8px;
  padding: 12px 24px;
  border-top: 1px solid #eceff4;
  background: $bg-white;
}
.input-area {
  display: flex;
  gap: 8px;
  padding: 12px 24px;
  border-top: 1px solid #eceff4;
  background: $bg-white;
  .el-input {
    flex: 1;
  }
}
.tool-call-info {
  margin-top: 6px;
}

@keyframes typing {
  0%,
  60%,
  100% {
    opacity: 0.3;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-4px);
  }
}
@keyframes msgIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
