/**
 * 智能体对话状态管理
 * 管理对话会话列表、当前会话消息等
 * 支持后端持久化：会话列表从 DB 加载，消息保存到 DB
 */
import {
  deleteSessionApi,
  getSessionMessagesApi,
  getSessionsApi,
} from "@/api/chat";
import { defineStore } from "pinia";

const SESSION_STORAGE_KEY = "chestvision_current_session_id";

function restoreSessionId() {
  if (typeof sessionStorage === "undefined") return null;
  const value = Number(sessionStorage.getItem(SESSION_STORAGE_KEY));
  return Number.isInteger(value) && value > 0 ? value : null;
}

export const useAgentStore = defineStore("agent", {
  state: () => ({
    // 当前会话 ID（后端 ChatSession.id）
    currentSessionId: restoreSessionId(),

    // 当前会话的消息列表
    messages: [],

    // 会话列表（从后端加载）
    sessions: [],

    // 是否正在加载会话列表
    sessionsLoading: false,

    // 是否正在等待 AI 响应
    isLoading: false,

    // 中断函数（用于取消 SSE 流式请求）
    abortController: null,
  }),

  getters: {
    /** 消息数量 */
    messageCount: (state) => state.messages.length,

    /** 是否有会话 */
    hasSession: (state) => state.sessions.length > 0,

    /** 当前会话标题 */
    currentTitle: (state) => {
      const s = state.sessions.find((s) => s.id === state.currentSessionId);
      return s?.title || "新对话";
    },
  },

  actions: {
    /** 添加一条消息 */
    addMessage(message) {
      this.messages.push(message);
    },

    /** 更新最后一条 AI 消息（用于流式追加） */
    updateLastAssistantMessage(content) {
      const lastMsg = this.messages[this.messages.length - 1];
      if (lastMsg && lastMsg.role === "assistant") {
        lastMsg.content = content;
      }
    },

    /** 设置加载状态 */
    setLoading(loading) {
      this.isLoading = loading;
    },

    /** 中断当前流式请求 */
    abort() {
      if (this.abortController) {
        this.abortController();
        this.abortController = null;
        this.isLoading = false;
      }
    },

    /** 新建对话（本地 + 后端下次请求自动创建） */
    newChat() {
      this.currentSessionId = null;
      this.messages = [];
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
      this.abort();
    },

    /** 清除所有状态 */
    clear() {
      this.currentSessionId = null;
      this.messages = [];
      this.sessions = [];
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
      this.abort();
    },

    /** 记录 SSE 返回的 session_id */
    setCurrentSessionId(sessionId) {
      if (sessionId && !this.currentSessionId) {
        this.currentSessionId = sessionId;
        sessionStorage.setItem(SESSION_STORAGE_KEY, String(sessionId));
      }
    },

    // ═══════════════════════════════════════════════════════
    // 后端会话管理
    // ═══════════════════════════════════════════════════════

    /** 从后端加载会话列表 */
    async loadSessions() {
      this.sessionsLoading = true;
      try {
        const res = await getSessionsApi({ status: "all", limit: 50 });
        this.sessions = res.sessions || [];
      } catch {
        /* 页面层通过 sessionsLoading=false + 空列表自行处理 */
      } finally {
        this.sessionsLoading = false;
      }
    },

    /** 加载指定会话的消息历史 */
    async loadSessionMessages(sessionId) {
      try {
        const res = await getSessionMessagesApi(sessionId);
        const msgs = res.messages || [];
        this.messages = msgs.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          createdAt: m.created_at,
        }));
        this.currentSessionId = sessionId;
        sessionStorage.setItem(SESSION_STORAGE_KEY, String(sessionId));
      } catch (e) {
        throw e;
      }
    },

    /** 切换到指定会话 */
    async switchSession(sessionId) {
      if (sessionId === this.currentSessionId && this.messages.length) return;
      await this.loadSessionMessages(sessionId);
    },

    /** 删除指定会话 */
    async deleteSession(sessionId) {
      try {
        await deleteSessionApi(sessionId);
        this.sessions = this.sessions.filter((s) => s.id !== sessionId);
        if (this.currentSessionId === sessionId) {
          this.newChat();
        }
        return true;
      } catch {
        return false;
      }
    },
  },
});
