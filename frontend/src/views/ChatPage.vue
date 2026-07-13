<template>
  <div class="chat-page">
    <!-- 消息列表 -->
    <div class="message-list" ref="msgListRef">
      <div
        v-for="(msg, i) in agentStore.messages"
        :key="i"
        :class="['message-item', `message-${msg.role}`]"
      >
        <!-- 用户消息 -->
        <div v-if="msg.role === 'user'" class="message-bubble user-bubble">
          <div class="message-content">{{ msg.content }}</div>
          <div v-if="msg.image" class="msg-attachment">
            <img :src="msg.imagePreview" alt="附件" />
          </div>
        </div>

        <!-- AI 消息 -->
        <div
          v-else-if="msg.role === 'assistant'"
          class="message-bubble assistant-bubble"
        >
          <div v-if="msg.loading" class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
          <div
            v-else
            class="message-content markdown-body"
            v-html="renderMd(msg.content)"
          ></div>
          <DetectionResultCard
            v-if="msg.detectionResult"
            :result="msg.detectionResult"
          />
        </div>

        <!-- 工具调用提示 -->
        <div v-if="msg.toolCall" class="tool-call-info">
          <el-tag size="small" type="info">🔧 {{ msg.toolCall.tool }}</el-tag>
        </div>
      </div>
    </div>

    <!-- 快捷操作栏 -->
    <div class="quick-actions">
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
import DetectionResultCard from "@/components/DetectionResultCard.vue";
import { useAgentStore } from "@/stores/agent";
import { renderMarkdown } from "@/utils/markdown";
import request from "@/utils/request";
import { streamChat } from "@/utils/stream";
import { ElMessage } from "element-plus";
import { nextTick, onMounted, ref } from "vue";

const agentStore = useAgentStore();
const inputText = ref("");
const selectedFiles = ref([]);
const msgListRef = ref(null);
const fileInputRef = ref(null);

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
  return renderMarkdown(text || "");
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

  // AI 加载占位
  agentStore.addMessage({ role: "assistant", content: "", loading: true });
  scrollBottom();

  // 上传所有文件
  const paths = [];
  for (const f of files) {
    try {
      const fd = new FormData();
      fd.append("file", f);
      const up = await request.post("/chat/upload", fd, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 60000,
      });
      paths.push(up.image_path);
    } catch (e) {
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content = `${f.name} 上传失败：${e.response?.data?.detail || e.message}`;
      last.loading = false;
      return;
    }
  }

  // SSE — 多文件时注入所有路径
  let enhancedMsg =
    text ||
    (files.length > 1
      ? `请批量检测这${files.length}张胸片`
      : "请帮我分析这张胸片");
  if (paths.length === 1) {
    enhancedMsg += `\n[附件图片路径: ${paths[0]}]`;
  } else if (paths.length > 1) {
    enhancedMsg += `\n[附件图片路径: ${paths.join(", ")}]`;
  }

  // SSE 流式对话
  let fullContent = "";
  const stop = streamChat(
    "/api/chat/stream",
    { message: enhancedMsg, image_path: paths[0] || undefined },
    {
      onMessage: (data) => {
        if (data.type === "text_chunk") {
          fullContent += data.content;
          agentStore.updateLastAssistantMessage(fullContent);
          scrollBottom();
        } else if (data.type === "tool_call") {
          const last = agentStore.messages[agentStore.messages.length - 1];
          last.toolCall = { tool: data.tool, input: data.input };
        } else if (data.type === "detection_card") {
          // 完整检测结果（含 base64 图片），直接渲染卡片
          const last = agentStore.messages[agentStore.messages.length - 1];
          last.detectionResult = data.data;
          last.loading = false;
          scrollBottom();
        } else if (data.type === "tool_result") {
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

function stopChat() {
  agentStore.abort();
  const last = agentStore.messages[agentStore.messages.length - 1];
  if (last?.loading) {
    last.loading = false;
    last.content += "\n[已停止]";
  }
}

// 快捷检测
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
      content: "正在检测中...",
      loading: true,
    });
    scrollBottom();

    try {
      const fd = new FormData();
      if (isZip) {
        fd.append("file", files[0]);
      } else if (files.length === 1) {
        fd.append("file", files[0]);
      } else {
        files.forEach((f) => fd.append("files", f));
      }

      const api = isZip
        ? detectZip
        : files.length === 1
          ? detectSingle
          : detectBatch;
      const result = await api(fd);
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content = result.error
        ? `检测失败：${result.error}`
        : `检测完成！发现 ${result.total_objects ?? 0} 个病灶。`;
      last.loading = false;
      if (!result.error) last.detectionResult = result;
    } catch (err) {
      const last = agentStore.messages[agentStore.messages.length - 1];
      last.content = `检测失败：${err.message}`;
      last.loading = false;
    }
  };
  input.click();
}

onMounted(() => {
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
  background: #f5f5f5;
}
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.message-item {
  display: flex;
  margin-bottom: 16px;
}
.message-user {
  justify-content: flex-end;
}
.message-assistant {
  justify-content: flex-start;
}
.message-bubble {
  max-width: 78%;
  padding: 14px 18px;
  border-radius: $border-radius-md;
  line-height: 1.65;
  word-break: break-word;
  font-size: 14px;
  animation: msgIn 0.3s ease;
}
.user-bubble {
  background: linear-gradient(135deg, $primary-color, $primary-light);
  color: #fff;
  border-bottom-right-radius: $spacing-xs;
  box-shadow: 0 2px 8px rgba($primary-color, 0.25);
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
    max-width: 200px;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
  }
}
.typing-indicator {
  display: flex;
  gap: 4px;
  span {
    width: 6px;
    height: 6px;
    background: #999;
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
  padding: 12px 20px;
  border-top: 1px solid #eceff4;
  background: $bg-white;
}
.input-area {
  display: flex;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid #eceff4;
  background: $bg-white;
  .el-input {
    flex: 1;
  }
}
.tool-call-info {
  margin-top: 8px;
  padding: 4px 8px;
  background: #f5f5f5;
  border-radius: 4px;
  font-size: 12px;
  color: $text-secondary;
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
