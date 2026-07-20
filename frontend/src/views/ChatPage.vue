<template>
  <div class="chat-page">
    <!-- 左侧：会话列表 -->
    <div :class="['chat-sessions-panel', { collapsed: !showSessions }]">
      <div class="sessions-header">
        <h3>对话记录</h3>
        <el-button size="small" type="primary" @click="startNewChat"
          >+ 新建对话</el-button
        >
      </div>
      <div class="sessions-list" v-loading="agentStore.sessionsLoading">
        <div
          v-for="s in agentStore.sessions"
          :key="s.id"
          :class="[
            'session-row',
            { active: s.id === agentStore.currentSessionId },
          ]"
          @click="switchToSession(s.id)"
        >
          <div class="session-row-title">{{ s.title }}</div>
          <div class="session-row-meta">{{ s.message_count }} 条消息</div>
        </div>
        <div
          v-if="!agentStore.sessions.length && !agentStore.sessionsLoading"
          class="sessions-empty"
        >
          暂无对话记录
        </div>
      </div>
    </div>

    <!-- 右侧：聊天区 -->
    <div class="chat-main">
      <!-- 折叠按钮 -->
      <div
        class="session-toggle-btn"
        @click="showSessions = !showSessions"
        :title="showSessions ? '收起对话列表' : '展开对话列表'"
      >
        <span>{{ showSessions ? "◀" : "▶" }}</span>
      </div>
      <!-- 消息列表 -->
      <div class="chat-messages" ref="msgListRef">
        <div
          v-for="(msg, i) in agentStore.messages"
          :key="i"
          :class="['msg-row', `msg-${msg.role}`]"
        >
          <div class="msg-avatar" v-if="msg.role === 'assistant'">
            <span class="avatar-bot">🫁</span>
          </div>
          <div class="msg-body">
            <div class="msg-meta">
              <span class="msg-sender">{{
                msg.role === "user" ? "我" : "ChestVision AI"
              }}</span>
            </div>
            <div
              :class="[
                'msg-bubble',
                msg.role === 'user' ? 'user-bubble' : 'assistant-bubble',
              ]"
            >
              <div v-if="msg.role === 'user'" class="msg-text">
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
                class="msg-text markdown-body"
                v-html="renderMd(msg.content)"
              ></div>
              <div v-if="msg.downloadPdfUrl" class="msg-actions">
                <a
                  href="#"
                  @click.prevent="downloadReport(msg.downloadPdfUrl)"
                  class="action-link"
                  >📥 下载/打印报告</a
                >
              </div>
            </div>

            <!-- Multi-Agent 节点流程可视化 -->
            <div
              v-if="msg.agentNodes && msg.agentNodes.length"
              class="agent-nodes-area"
            >
              <div class="agent-flow-label">🤖 Multi-Agent 协作流程</div>
              <div class="agent-flow">
                <div
                  v-for="(an, idx) in msg.agentNodes"
                  :key="idx"
                  class="agent-node-badge"
                  :class="an.status"
                >
                  <span class="agent-node-icon">{{
                    an.node === "supervisor" ? "🧠" :
                    an.node === "detection" ? "🔬" :
                    an.node === "diagnosis" ? "📋" :
                    an.node === "report" ? "📄" :
                    an.node === "qa" ? "📚" : "📝"
                  }}</span>
                  <span class="agent-node-label">{{ an.label }}</span>
                </div>
              </div>
            </div>

            <!-- 工具调用可视化 -->
            <div
              v-if="msg.toolCalls && msg.toolCalls.length"
              class="tool-calls-area"
            >
              <div
                v-for="(tc, idx) in msg.toolCalls"
                :key="idx"
                class="tool-call-row"
                :class="{ loading: tc.status === 'loading' }"
              >
                <span v-if="tc.status === 'loading'" class="tool-spinner"
                  >⏳</span
                >
                <span v-else class="tool-done">✅</span>
                <span class="tool-label">{{ getToolLabel(tc.tool) }}</span>
                <span class="tool-summary">{{ tc.summary || "..." }}</span>
              </div>
            </div>

            <!-- 知识来源显示 -->
            <div
              v-if="msg.knowledgeSources && msg.knowledgeSources.length"
              class="knowledge-sources-info"
            >
              <span class="kb-icon">📚</span>
              <span class="kb-label">知识库检索结果：</span>
              <span
                v-for="(src, idx) in msg.knowledgeSources"
                :key="idx"
                class="kb-source-tag"
                >{{ src.title || src.source }}</span
              >
            </div>
            <div
              v-else-if="msg.hasKnowledge === false"
              class="knowledge-sources-info no-kb"
            >
              <span class="kb-icon">💡</span>
              <span>回答来自大模型（知识库暂无相关内容）</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="chat-input-bar">
        <!-- 快捷操作 -->
        <div class="quick-actions">
          <el-select
            v-if="
              userStore.userType === 'doctor' || userStore.userType === 'admin'
            "
            v-model="selectedPatientId"
            placeholder="选择患者（可选）"
            clearable
            size="small"
            style="width: 200px"
            @change="onPatientChange"
          >
            <el-option
              v-for="p in patientList"
              :key="p.id"
              :label="`${p.patient_code} ${p.real_name || p.username}`"
              :value="p.id"
            />
          </el-select>
          <el-button
            size="small"
            @click="quickDetect('single')"
            :disabled="agentStore.isLoading"
            >📷 单图检测</el-button
          >
          <el-button
            size="small"
            @click="quickDetect('batch')"
            :disabled="agentStore.isLoading"
            >📁 批量/ZIP</el-button
          >
        </div>
        <!-- 输入框 -->
        <div class="input-row">
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
            size="large"
            @keyup.enter.exact="sendMsg"
            :disabled="agentStore.isLoading"
          >
            <template #append>
              <el-button
                @click="sendMsg"
                :loading="agentStore.isLoading"
                type="primary"
                >发送</el-button
              >
            </template>
          </el-input>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { getMultiAgentParams, uploadImageApi } from "@/api/chat";
import { getPatients } from "@/api/patient";
import { useAgentStore } from "@/stores/agent";
import { useUserStore } from "@/stores/user";
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
const showSessions = ref(true);

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

// Day11: 工具名称中文映射
const TOOL_LABELS = {
  detect_single_image: "单图检测",
  detect_batch_images: "批量检测",
  detect_zip_file: "ZIP检测",
  query_system_info: "系统查询",
  generate_report: "生成报告",
  search_knowledge: "知识库检索",
};
function getToolLabel(tool) {
  return TOOL_LABELS[tool] || tool;
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

  // ── "生成报告" 快捷操作（保留直接调用）──
  if (text.includes("生成报告") && !files.length) {
    agentStore.addMessage({ role: "assistant", content: "", loading: true });
    scrollBottom();
    try {
      const request = await import("@/utils/request");
      const res = await request.default.post("/reports/generate", { task_id: 0 });
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
  agentStore.addMessage({ role: "assistant", content: "", loading: true, agentNodes: [] });
  scrollBottom();

  // ── 有图片：先上传，再走 Multi-Agent ──
  let imagePath = null;
  if (files.length > 0) {
    const last = agentStore.messages[agentStore.messages.length - 1];
    last.content = "📤 正在上传胸片...";
    try {
      const uploadRes = await uploadImageApi(files[0]);
      imagePath = uploadRes.image_path;
      last.content = "🔍 Multi-Agent 协作分析中...";
    } catch (e) {
      last.content = `上传失败：${e.response?.data?.detail || e.message}`;
      last.loading = false;
      scrollBottom();
      return;
    }
  }

  // ── 调用 Multi-Agent SSE ──
  const { url, body } = getMultiAgentParams({
    message: text || "请分析这张胸片",
    image_path: imagePath,
    session_id: agentStore.currentSessionId || undefined,
    patient_profile_id: selectedPatientId.value || undefined,
  });

  let fullContent = "";
  const stop = streamChat(url, body, {
    onMessage: (data) => {
      const last = agentStore.messages[agentStore.messages.length - 1];
      if (!last) return;

      // ── Multi-Agent 节点事件 ──
      if (data.type === "agent_node") {
        if (!last.agentNodes) last.agentNodes = [];
        const nodeLabels = {
          supervisor: "🧠 任务调度",
          detection: "🔬 病灶检测",
          diagnosis: "📋 综合诊断",
          report: "📄 报告生成",
          qa: "📚 知识问答",
          summarize: "📝 汇总输出",
        };
        last.agentNodes.push({
          node: data.node,
          label: nodeLabels[data.node] || data.node,
          status: data.status || "completed",
        });
        scrollBottom();
      }
      // ── 思考状态 ──
      else if (data.type === "thinking") {
        if (last.loading && (!last.content || last.content.includes("分析"))) {
          last.content = data.content || "正在分析...";
        }
      }
      // ── 文本块 ──
      else if (data.type === "text_chunk") {
        fullContent += data.content;
        agentStore.updateLastAssistantMessage(fullContent);
        if (data.knowledge_sources) {
          last.knowledgeSources = data.knowledge_sources;
        }
        if (data.has_knowledge !== undefined) {
          last.hasKnowledge = data.has_knowledge;
        }
        last.loading = false;
        scrollBottom();
      }
      // ── 完成 ──
      else if (data.type === "done") {
        if (data.session_id) {
          agentStore.setCurrentSessionId(data.session_id);
          agentStore.loadSessions();
        }
      }
      // ── 错误 ──
      else if (data.type === "error") {
        last.content = data.content;
        last.loading = false;
      }
    },
    onDone: () => {
      const last = agentStore.messages[agentStore.messages.length - 1];
      if (last && last.loading) last.loading = false;
      agentStore.setLoading(false);
    },
    onError: (err) => {
      const last = agentStore.messages[agentStore.messages.length - 1];
      if (last) {
        last.content = `处理出错：${err.message}`;
        last.loading = false;
      }
      agentStore.setLoading(false);
    },
  });
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

// 快捷检测 → 上传后走 Multi-Agent 协作流程
async function quickDetect(type) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = type === "single" ? "image/*" : "image/*,.zip";
  input.multiple = type !== "single";
  input.onchange = async (e) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    selectedFiles.value = files;
    inputText.value = "请分析这张胸片";
    await sendMsg();
  };
  input.click();

  input.click();
}

onMounted(() => {
  loadPatients();
  agentStore.loadSessions(); // 加载历史会话列表
  if (agentStore.messages.length === 0) {
    agentStore.addMessage({
      role: "assistant",
      content:
        "你好！我是**胸片X光AI辅助诊断助手** 🫁\n\n我可以帮你：\n- 📷 上传胸片进行 AI 病灶检测\n- 📁 批量检测多张胸片或 ZIP 包\n- 💬 自然语言分析解读检测结果\n\n支持 10 种胸部病变：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸\n\n---\n*请上传胸片或输入消息开始*",
    });
  }
});

/** 新建对话 */
function startNewChat() {
  agentStore.newChat();
  showSessions.value = false;
  agentStore.addMessage({
    role: "assistant",
    content:
      "你好！我是**胸片X光AI辅助诊断助手** 🫁\n\n我可以帮你：\n- 📷 上传胸片进行 AI 病灶检测\n- 📁 批量检测多张胸片或 ZIP 包\n- 💬 自然语言分析解读检测结果\n\n---\n*请上传胸片或输入消息开始*",
  });
}

/** 切换到历史会话 */
async function switchToSession(sessionId) {
  try {
    await agentStore.switchSession(sessionId);
    showSessions.value = false;
    scrollBottom();
  } catch {
    ElMessage.error("加载会话失败");
  }
}

/** 删除会话 */
async function handleDeleteSession(sessionId) {
  try {
    await ElMessageBox.confirm("确定删除该对话？删除后不可恢复。", "确认删除", {
      type: "warning",
    });
    const ok = await agentStore.deleteSession(sessionId);
    if (ok) ElMessage.success("已删除");
  } catch {
    // 用户取消
  }
}
</script>

<style lang="scss" scoped>
/* ═══════════════════════════════════════════════════════
   三栏布局：会话列表 | 聊天区
   ═══════════════════════════════════════════════════════ */
.chat-page {
  display: flex;
  height: 100%;
  background: $bg-color;
}

/* ── 左侧会话列表 ── */
.chat-sessions-panel {
  width: 340px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid #f0f2f5;
  display: flex;
  flex-direction: column;
  transition:
    width 0.3s ease,
    opacity 0.3s ease;
  overflow: hidden;
  &.collapsed {
    width: 0;
    border-right: none;
    opacity: 0;
  }
}

.sessions-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px;
  border-bottom: 1px solid #f0f2f5;
  h3 {
    font-size: 15px;
    font-weight: 600;
    color: $text-primary;
    margin: 0;
  }
}

.sessions-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-row {
  padding: 12px 14px;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 2px;
  &:hover {
    background: #f5f7fa;
  }
  &.active {
    background: rgba(42, 157, 143, 0.08);
  }
}

.session-row-title {
  font-size: 14px;
  font-weight: 500;
  color: $text-primary;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-row-meta {
  font-size: 12px;
  color: $text-secondary;
}

.sessions-empty {
  text-align: center;
  padding: 40px 20px;
  color: $text-secondary;
  font-size: 13px;
}

/* ── 右侧聊天主区 ── */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  position: relative;
}

.session-toggle-btn {
  position: absolute;
  top: 50%;
  left: -14px;
  transform: translateY(-50%);
  z-index: 5;
  width: 28px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  border: 1px solid #e8ecf0;
  border-radius: 0 8px 8px 0;
  cursor: pointer;
  font-size: 12px;
  color: $text-secondary;
  transition: all 0.2s;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  &:hover {
    background: #f0faf7;
    color: $primary-color;
    border-color: $primary-color;
  }
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 28px 40px;
}

/* ── 消息气泡 ── */
.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
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
    width: 34px;
    height: 34px;
    font-size: 18px;
    background: linear-gradient(135deg, #e6f7f5, #d0f0e8);
    border-radius: 10px;
  }
}

.msg-body {
  max-width: 75%;
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

.msg-bubble {
  padding: 14px 20px;
  border-radius: 14px;
  line-height: 1.75;
  font-size: 15px;
  word-break: break-word;
}
.user-bubble {
  background: linear-gradient(135deg, $primary-dark, $primary-color);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.assistant-bubble {
  background: #fff;
  color: $text-primary;
  border: 1px solid #eef1f5;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}

.msg-text {
  :deep(p) {
    margin: 0 0 8px;
    &:last-child {
      margin: 0;
    }
  }
  :deep(strong) {
    color: $primary-dark;
  }
  :deep(code) {
    background: #f5f6f8;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 13px;
    font-family: $font-mono;
  }
}

.msg-attachment {
  margin-top: 8px;
  img {
    max-width: 240px;
    border-radius: 10px;
    border: 1px solid #eef1f5;
  }
}

.msg-actions {
  margin-top: 10px;
  display: flex;
  gap: 12px;
  .action-link {
    font-size: 13px;
    color: $primary-color;
    text-decoration: none;
    &:hover {
      text-decoration: underline;
    }
  }
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 4px 0;
  span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #c0c8d0;
    animation: typing 1.4s infinite ease-in-out;
    &:nth-child(2) {
      animation-delay: 0.2s;
    }
    &:nth-child(3) {
      animation-delay: 0.4s;
    }
  }
}

@keyframes typing {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-6px);
    opacity: 1;
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

/* ── 输入区 ── */
.chat-input-bar {
  padding: 12px 40px 20px;
  background: #fff;
  border-top: 1px solid #f0f2f5;
}

.tool-calls-area {
  margin-top: 10px;
}

.tool-call-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #f8fafb;
  border-radius: 8px;
  margin-bottom: 4px;
  font-size: 13px;
  color: $text-regular;
  &.loading {
    opacity: 0.7;
  }
}

.tool-spinner,
.tool-done {
  font-size: 14px;
  flex-shrink: 0;
}
.tool-label {
  font-weight: 600;
  color: $primary-dark;
}
.tool-summary {
  color: $text-secondary;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-sources-info {
  margin-top: 10px;
  padding: 8px 12px;
  background: #f0fdfa;
  border-radius: 8px;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  &.no-kb {
    background: #fffbeb;
  }
}

.kb-icon {
  font-size: 14px;
}
.kb-label {
  color: $text-secondary;
}
.kb-source-tag {
  background: #e6f7f5;
  color: $primary-dark;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
}

.quick-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.input-row {
  display: flex;
  align-items: center;
  gap: 8px;
  .attach-btn {
    flex-shrink: 0;
    font-size: 16px;
  }
}

/* ── Multi-Agent 流程可视化 ── */
.agent-nodes-area {
  margin-top: 10px;
  padding: 10px 14px;
  background: linear-gradient(135deg, #f0f4ff, #f8fafd);
  border-radius: 10px;
  border: 1px solid #e0e8f5;
}

.agent-flow-label {
  font-size: 12px;
  font-weight: 600;
  color: #5b6e8c;
  margin-bottom: 8px;
}

.agent-flow {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.agent-node-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  background: #e8eef8;
  color: #4a5f80;
  transition: all 0.3s;
  &.completed {
    background: #e6f7f5;
    color: #2a9d8f;
    border: 1px solid #c3e8e1;
  }
  &.loading {
    background: #fff7e6;
    color: #d48806;
    border: 1px solid #ffe58f;
    animation: nodePulse 1.5s ease-in-out infinite;
  }
}

.agent-node-icon {
  font-size: 14px;
}

.agent-node-label {
  white-space: nowrap;
}

@keyframes nodePulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
</style>
