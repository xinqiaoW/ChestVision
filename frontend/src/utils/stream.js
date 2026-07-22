/**
 * SSE (Server-Sent Events) 流式处理工具
 * 用于 Day 11 智能体对话的流式渲染
 *
 * 使用方式：
 *   const stop = streamChat(
 *     '/api/chat/stream',
 *     { message: '你好' },
 *     {
 *       onMessage: (chunk) => { content += chunk },
 *       onDone: () => { console.log('完成') },
 *       onError: (err) => { console.error(err) },
 *     }
 *   )
 */

/**
 * 发起 SSE 流式请求
 *
 * @param {string} url - 请求地址（相对路径，会经过 Vite proxy）
 * @param {Object} body - 请求体
 * @param {Object} callbacks - 回调函数
 * @param {Function} callbacks.onMessage - 收到消息片段时的回调
 * @param {Function} callbacks.onDone - 流结束时的回调
 * @param {Function} callbacks.onError - 错误时的回调
 * @returns {Function} stop - 调用此函数可中断连接
 */
export function streamChat(url, body, callbacks) {
  const { onMessage, onDone, onError } = callbacks;

  // 从 localStorage 获取 Token
  const token = localStorage.getItem("chestx_token");

  // 使用 fetch + ReadableStream 实现 SSE
  const controller = new AbortController();

  fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          if (buffer.trim()) processSSE(buffer, onMessage);
          onDone?.();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const messages = buffer.split("\n\n");
        buffer = messages.pop() || "";

        for (const msg of messages) {
          if (msg.trim()) {
            const shouldStop = processSSE(msg, onMessage);
            if (shouldStop) {
              onDone?.();
              return;
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") onError?.(err);
    });

  return () => controller.abort();
}

function processSSE(message, onMessage) {
  const lines = message.split("\n");
  for (const line of lines) {
    if (line.startsWith("data: ")) {
      const data = line.slice(6);
      if (data === "[DONE]") return true;
      try {
        const parsed = JSON.parse(data);
        onMessage?.(parsed);
        // 后端可能由内部 Agent 发出阶段性 done；继续读取，直到 SSE 真正结束。
      } catch {
        onMessage?.({ type: "text_chunk", content: data });
      }
    }
  }
  return false;
}
