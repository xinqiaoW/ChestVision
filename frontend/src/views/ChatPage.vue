<template>
  <div class="chat-page" :class="{ 'welcome-mode': !hasMessages }">
    <!-- 左侧：会话列表（始终可见） -->
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

    <!-- 右侧主区域 -->
    <div class="chat-main">
      <!-- 折叠按钮 -->
      <div
        class="session-toggle-btn"
        @click="showSessions = !showSessions"
        :title="showSessions ? '收起对话列表' : '展开对话列表'"
      >
        <span>{{ showSessions ? "◀" : "▶" }}</span>
      </div>

      <!-- ═══════════════════════════════════════════════════
           欢迎页：首次进入，无对话消息时显示
           ═══════════════════════════════════════════════════ -->
      <div v-if="!hasMessages" class="welcome-screen">
        <div class="welcome-content">
          <div class="welcome-logo">🫁</div>
          <h1 class="welcome-greeting">你好，{{ userStore.username }}</h1>
          <p class="welcome-tagline">开启智能医疗问答</p>
          <p class="welcome-desc">
            我是
            <strong>ChestVision</strong>
            智能影像分析平台，<br />基于深度学习辅助胸部 X 光影像诊断
          </p>

          <!-- 快捷提问 -->
          <div class="welcome-suggestions">
            <div
              class="suggestion-item"
              @click="sendSuggestion('帮我分析一张胸片')"
            >
              <span class="sug-icon">🔬</span>
              <span>上传胸片进行 AI 分析</span>
            </div>
            <div
              class="suggestion-item"
              @click="sendSuggestion('胸部 X 光常见病变有哪些？')"
            >
              <span class="sug-icon">📚</span>
              <span>了解胸部常见病变</span>
            </div>
            <div
              class="suggestion-item"
              @click="sendSuggestion('你能做什么？')"
            >
              <span class="sug-icon">✨</span>
              <span>你能做什么</span>
            </div>
          </div>

          <!-- 快捷操作 -->
          <div class="welcome-actions">
            <el-select
              v-if="
                userStore.userType === 'doctor' ||
                userStore.userType === 'admin'
              "
              v-model="selectedPatientId"
              placeholder="选择患者（可选）"
              clearable
              size="default"
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
              @click="quickDetect('single')"
              :disabled="agentStore.isLoading"
              >📷 单图检测</el-button
            >
            <el-button
              @click="quickDetect('batch')"
              :disabled="agentStore.isLoading"
              >📁 批量检测</el-button
            >
          </div>

          <!-- 输入区 -->
          <div class="welcome-input-row">
            <el-button
              class="attach-btn"
              @click="triggerFile"
              :disabled="agentStore.isLoading"
              circle
              >＋</el-button
            >
            <input
              ref="fileInputRef"
              type="file"
              accept="image/*"
              style="display: none"
              @change="onFileSelect"
            />
            <el-input
              v-model="inputText"
              placeholder="输入您的问题，或上传胸片进行 AI 分析..."
              size="large"
              @keyup.enter.exact="sendMsg"
              :disabled="agentStore.isLoading"
              class="welcome-input"
            >
              <template #append>
                <el-button
                  class="send-btn"
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

      <!-- ═══════════════════════════════════════════════════
           正常对话模式：消息列表 + 输入区
           ═══════════════════════════════════════════════════ -->
      <div v-else class="chat-body">
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
                    >📥 下载 PDF 报告</a
                  >
                </div>
                <DetectionResultCard
                  v-if="msg.detectionResult"
                  :result="msg.detectionResult"
                />
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
                      an.node === "supervisor" ||
                      an.node === "supervisor_answer"
                        ? "🧠"
                        : an.node === "detection"
                          ? "🔬"
                          : an.node === "diagnosis"
                            ? "📋"
                            : an.node === "report"
                              ? "📄"
                              : an.node === "case_analysis"
                                ? "🗂️"
                                : an.node === "qa"
                                  ? "📚"
                                  : "📝"
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
                userStore.userType === 'doctor' ||
                userStore.userType === 'admin'
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
              >＋</el-button
            >
            <input
              ref="fileInputRef"
              type="file"
              accept="image/*"
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
                  class="send-btn"
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

    <DoctorRecommendationDialog
      v-model="recommendationVisible"
      :task-id="recommendationTaskId"
      :patient-profile-id="selectedPatientId"
      :session-id="agentStore.currentSessionId"
    />
  </div>
</template>

<script setup>
import { getMultiAgentParams, uploadImageApi } from "@/api/chat";
import { detectZip } from "@/api/detection";
import { getPatients } from "@/api/patient";
import DetectionResultCard from "@/components/DetectionResultCard.vue";
import DoctorRecommendationDialog from "@/components/DoctorRecommendationDialog.vue";
import { useAgentStore } from "@/stores/agent";
import { useUserStore } from "@/stores/user";
import request from "@/utils/request";
import { streamChat } from "@/utils/stream";
import { ElMessage, ElMessageBox } from "element-plus";
import MarkdownIt from "markdown-it";
import { computed, nextTick, onMounted, ref } from "vue";

const md = new MarkdownIt({ breaks: true, html: false });

const agentStore = useAgentStore();
const userStore = useUserStore();

/** 是否有用户对话消息（决定显示欢迎页还是对话页） */
const hasMessages = computed(() => agentStore.messages.length > 0);
const inputText = ref("");
const selectedFiles = ref([]);
const msgListRef = ref(null);
const fileInputRef = ref(null);
const selectedPatientId = ref(null);
const patientList = ref([]);
const showSessions = ref(true);
const recommendationVisible = ref(false);
const recommendationTaskId = ref(null);
const latestDetectionTaskId = ref(null);

const REPORT_INTENT_PATTERN =
  /(?:生成|制作|撰写|写|丰富|详细|完整|深度|增强|导出|下载|查看|打开).{0,16}(?:PDF|报告)|(?:PDF|报告).{0,16}(?:生成|制作|撰写|写|丰富|详细|完整|深度|增强|导出|下载|查看|打开)/i;
const REPORT_REFINEMENT_PATTERN =
  /(?:再|更)(?:详细|丰富|完整|深入)|补充.{0,8}(?:分析|建议|内容)/;

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

/** 欢迎页快捷提问 */
function sendSuggestion(text) {
  inputText.value = text;
  sendMsg();
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
  agentStore.setLoading(true);

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

  // ── PDF/报告意图：必须调用真实报告 API，不让模型虚构附件 ──
  if (
    (REPORT_INTENT_PATTERN.test(text) ||
      (latestDetectionTaskId.value && REPORT_REFINEMENT_PATTERN.test(text))) &&
    !files.length
  ) {
    agentStore.addMessage({ role: "assistant", content: "", loading: true });
    scrollBottom();
    let reportContent = "";
    const stopReport = streamChat(
      "/api/reports/generate/stream",
      {
        task_id: latestDetectionTaskId.value || 0,
        instructions: text,
      },
      {
        onMessage: (data) => {
          const aiMsg = agentStore.messages[agentStore.messages.length - 1];
          if (!aiMsg) return;
          if (data.type === "thinking") {
            aiMsg.content = data.content || "正在生成深度报告...";
          } else if (data.type === "text_chunk") {
            reportContent += data.content || "";
            aiMsg.content = reportContent;
            aiMsg.loading = false;
            scrollBottom();
          } else if (data.type === "report_ready") {
            aiMsg.downloadPdfUrl = data.pdf_url;
            latestDetectionTaskId.value = data.task_id;
            scrollBottom();
          } else if (data.type === "error") {
            aiMsg.content = data.content || "生成报告失败";
            aiMsg.loading = false;
          }
        },
        onDone: () => {
          const aiMsg = agentStore.messages[agentStore.messages.length - 1];
          if (aiMsg) aiMsg.loading = false;
          agentStore.setLoading(false);
          scrollBottom();
        },
        onError: (error) => {
          const aiMsg = agentStore.messages[agentStore.messages.length - 1];
          if (aiMsg) {
            aiMsg.content = `生成报告失败：${error.message}`;
            aiMsg.loading = false;
          }
          agentStore.setLoading(false);
        },
      },
    );
    agentStore.abortController = stopReport;
    return;
  }

  // AI 加载占位
  agentStore.addMessage({
    role: "assistant",
    content: "",
    loading: true,
    agentNodes: [],
  });
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
      agentStore.setLoading(false);
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
          supervisor_answer: "🧠 Supervisor 统一回答",
          detection: "🔬 病灶检测",
          diagnosis: "📋 综合诊断",
          report: "📄 报告生成",
          case_analysis: "🗂️ 历史病例分析",
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
      // ── 检测结果卡片 + AI 医生推荐 ──
      else if (data.type === "detection_card") {
        const result = data.data || {};
        last.detectionResult = result;
        if (result.task_id) {
          latestDetectionTaskId.value = result.task_id;
        }
        if (result.total_objects > 0 && result.task_id) {
          recommendationTaskId.value = result.task_id;
          recommendationVisible.value = true;
        }
        scrollBottom();
      }
      // ── 真实 PDF 已由报告 Agent 生成 ──
      else if (data.type === "report_ready") {
        last.downloadPdfUrl = data.pdf_url;
        if (data.task_id) {
          latestDetectionTaskId.value = data.task_id;
        }
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
  try {
    const token = localStorage.getItem("chestx_token");
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        // 非 JSON 错误响应，使用 HTTP 状态码。
      }
      throw new Error(detail);
    }

    const blob = await res.blob();
    if (blob.type !== "application/pdf") {
      throw new Error("服务端未返回 PDF 文件");
    }

    const disposition = res.headers.get("Content-Disposition") || "";
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const fallbackName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
    const filename = encodedName
      ? decodeURIComponent(encodedName)
      : fallbackName || "ChestVision_胸片分析报告.pdf";
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    ElMessage.success("PDF 报告已下载");
  } catch (error) {
    ElMessage.error(`PDF 下载失败：${error.message}`);
  }
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

    const isZip = files.some((f) => f.name.endsWith(".zip"));
    agentStore.setLoading(true);

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

      let result;
      let recommendableResult = null;
      if (isZip) {
        result = await detectZip(fd);
        if (result.total_objects > 0 && result.task_id) {
          recommendableResult = result;
        }
      } else {
        const results = [];
        for (const file of files) {
          const imageForm = new FormData();
          imageForm.append("file", file);
          const item = await request.post("/detection/detect", imageForm, {
            headers: { "Content-Type": "multipart/form-data" },
            params: {
              patient_profile_id: selectedPatientId.value || undefined,
            },
            timeout: 120000,
          });
          results.push(item);
        }
        recommendableResult = results.find(
          (item) => item.total_objects > 0 && item.task_id,
        );
        result =
          results.length === 1
            ? {
                ...results[0],
                detections: results[0].objects,
                inference_time: results[0].inference_time_ms,
              }
            : {
                total_objects: results.reduce(
                  (sum, item) => sum + item.total_objects,
                  0,
                ),
                detections: results.flatMap((item) => item.objects),
                inference_time: results.reduce(
                  (sum, item) => sum + item.inference_time_ms,
                  0,
                ),
                annotated_image_base64: "",
              };
      }
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content =
        result.total_objects > 0
          ? `检测完成！发现 ${result.total_objects} 个病灶`
          : "检测完成，未发现明显病灶";
      last.loading = false;
      last.detectionResult = result;
      const completedTaskId =
        recommendableResult?.task_id || result.task_id || null;
      if (completedTaskId) {
        latestDetectionTaskId.value = completedTaskId;
      }
      if (recommendableResult) {
        recommendationTaskId.value = recommendableResult.task_id;
        recommendationVisible.value = true;
      }
      agentStore.setLoading(false);
    } catch (err) {
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content = `检测失败：${err.message}`;
      last.loading = false;
      agentStore.setLoading(false);
    }
  };
  input.click();
}

onMounted(async () => {
  loadPatients();
  agentStore.loadSessions(); // 加载历史会话列表
  if (agentStore.currentSessionId && agentStore.messages.length === 0) {
    try {
      await agentStore.loadSessionMessages(agentStore.currentSessionId);
    } catch {
      // 保存的会话已被删除或无权访问，回到真正的新会话状态。
      agentStore.newChat();
    }
  }
  if (agentStore.messages.length === 0) {
    // 欢迎页模式：不添加初始消息，显示居中欢迎界面
  }
});

/** 新建对话 */
function startNewChat() {
  agentStore.newChat();
  latestDetectionTaskId.value = null;
  showSessions.value = false;
}

/** 切换到历史会话 */
async function switchToSession(sessionId) {
  try {
    await agentStore.switchSession(sessionId);
    latestDetectionTaskId.value = null;
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
   整体布局
   ═══════════════════════════════════════════════════════ */
.chat-page {
  display: flex;
  height: 100%;
  background: $bg-color;

  &.welcome-mode {
    background:
      radial-gradient(
        ellipse 700px 500px at 10% 15%,
        rgba(42, 157, 143, 0.1) 0%,
        transparent 50%
      ),
      radial-gradient(
        ellipse 500px 400px at 90% 85%,
        rgba(42, 157, 143, 0.08) 0%,
        transparent 50%
      ),
      linear-gradient(180deg, #f0faf7 0%, $bg-color 60%);
    position: relative;
    overflow: hidden;

    /* 胸片网格纹理 */
    &::before {
      content: "";
      position: absolute;
      inset: 0;
      background-image: radial-gradient(
        circle,
        rgba(42, 157, 143, 0.06) 1px,
        transparent 1px
      );
      background-size: 32px 32px;
      pointer-events: none;
    }

    /* 右下角装饰环 */
    &::after {
      content: "";
      position: absolute;
      bottom: -80px;
      right: -60px;
      width: 260px;
      height: 260px;
      border-radius: 50%;
      border: 1.5px solid rgba(42, 157, 143, 0.12);
      pointer-events: none;
    }
  }
}

/* ═══════════════════════════════════════════════════════
   欢迎页
   ═══════════════════════════════════════════════════════ */
.welcome-screen {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  min-height: 0;
  padding: 40px;
}

.welcome-content {
  text-align: center;
  max-width: 640px;
  width: 100%;
  animation: welcomeFadeIn 0.6s ease;
}

@keyframes welcomeFadeIn {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.welcome-logo {
  font-size: 56px;
  margin-bottom: 20px;
  animation: logoPulse 2s ease-in-out infinite;
}

@keyframes logoPulse {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.15);
  }
}

.welcome-greeting {
  font-size: 28px;
  font-weight: 700;
  color: $text-primary;
  margin: 0 0 8px;
}

.welcome-tagline {
  font-size: 16px;
  color: $primary-color;
  font-weight: 600;
  margin: 0 0 16px;
}

.welcome-desc {
  font-size: 15px;
  color: $text-secondary;
  line-height: 1.7;
  margin: 0 0 32px;

  strong {
    color: $primary-dark;
  }
}

/* ── 快捷提问卡片 ── */
.welcome-suggestions {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-bottom: 32px;
  flex-wrap: wrap;
}

.suggestion-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px;
  background: #fff;
  border: 1px solid #e8ecf0;
  border-radius: 12px;
  cursor: pointer;
  font-size: 14px;
  color: $text-regular;
  transition: all 0.25s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);

  &:hover {
    border-color: $primary-color;
    color: $primary-color;
    box-shadow: 0 4px 14px rgba(42, 157, 143, 0.12);
    transform: translateY(-2px);
  }

  .sug-icon {
    font-size: 18px;
    flex-shrink: 0;
  }
}

/* ── 快捷操作按钮 ── */
.welcome-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

/* ── 欢迎页输入区 ── */
.welcome-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 720px;
  margin: 0 auto;

  .attach-btn {
    flex-shrink: 0;
    width: 42px;
    height: 42px;
    border: 1px solid #e0e4e8;
    background: #fff;
    font-size: 20px;
    font-weight: 300;
    color: $text-secondary;
    transition: all 0.2s;
    &:hover {
      border-color: $primary-color;
      color: $primary-color;
    }
  }

  .welcome-input {
    flex: 1;
  }

  :deep(.send-btn) {
    color: #fff !important;
    font-weight: 600;
    letter-spacing: 1px;
  }
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

/* 对话主体：消息列表 + 输入框，继承 flex 列布局，输入框始终在底部 */
.chat-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.session-toggle-btn {
  position: absolute;
  top: 50%;
  left: 0;
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

  :deep(.send-btn) {
    color: #fff !important;
    font-weight: 600;
    letter-spacing: 1px;
  }
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
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
}
</style>
