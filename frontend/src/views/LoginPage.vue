<template>
  <div class="login-page">
    <!-- 左上角 logo -->
    <img src="@/assets/xjtulogo.png" alt="logo" class="corner-logo" />
    <div class="bg-glow bg-glow-top"></div>
    <div class="bg-glow bg-glow-bottom"></div>
    <div class="page-top-info">
      <div class="top-logo">🫁</div>
      <h1 class="top-title">ChestVision</h1>
      <p class="top-desc">胸片 X 光 智能分析与辅助诊断系统</p>
    </div>
    <div class="page-bottom-info">
      <div class="bottom-item">
        <span class="bottom-icon">🧠</span
        ><span>AI 病灶检测 · 14 类胸部病变精准识别</span>
      </div>
      <div class="bottom-item">
        <span class="bottom-icon">💬</span
        ><span>多 Agent 智能影像报告生成</span>
      </div>
      <div class="bottom-item">
        <span class="bottom-icon">📊</span><span>检测趋势与病灶分布可视化</span>
      </div>
    </div>
    <div class="login-card">
      <div class="card-header">
        <div class="header-inner">
          <div class="header-icon"><span class="icon-lung">🫁</span></div>
          <div class="header-text">
            <h1 class="system-title">ChestVision</h1>
            <p class="system-desc">基于深度学习的胸部 X 光影像辅助诊断平台</p>
          </div>
        </div>
      </div>
      <div class="fixed-area login-area" :class="{ 'slide-out': !isLogin }">
        <div class="form-content">
          <h2 class="form-title">登录</h2>
          <p class="form-sub">使用您的账号密码登录</p>
          <el-form
            ref="loginFormRef"
            :model="loginForm"
            :rules="loginRules"
            label-width="0"
            size="large"
            @submit.prevent="handleLogin"
          >
            <el-form-item prop="username"
              ><el-input
                v-model="loginForm.username"
                placeholder="邮箱 / 工号"
                prefix-icon="User"
            /></el-form-item>
            <el-form-item prop="password"
              ><el-input
                v-model="loginForm.password"
                type="password"
                placeholder="密码"
                prefix-icon="Lock"
                show-password
                @keyup.enter="handleLogin"
            /></el-form-item>
            <div class="form-options">
              <el-checkbox v-model="rememberMe">记住我</el-checkbox>
              <el-button
                link
                class="forgot-link"
                native-type="button"
                @click="handleForgotPassword"
                >忘记密码？</el-button
              >
            </div>
            <el-form-item
              ><el-button
                type="primary"
                class="submit-btn"
                :loading="loading"
                @click="handleLogin"
                >登录系统</el-button
              ></el-form-item
            >
          </el-form>
        </div>
      </div>
      <div class="fixed-area register-area" :class="{ 'slide-out': isLogin }">
        <div class="form-content">
          <h2 class="form-title">创建账号</h2>
          <p class="form-sub">注册后即可使用全部功能</p>
          <el-form
            ref="registerFormRef"
            :model="registerForm"
            :rules="registerRules"
            label-width="0"
            size="large"
            @submit.prevent="handleRegister"
          >
            <el-form-item prop="name"
              ><el-input
                v-model="registerForm.name"
                placeholder="姓名"
                prefix-icon="User"
            /></el-form-item>
            <el-form-item prop="email"
              ><el-input
                v-model="registerForm.email"
                placeholder="邮箱"
                prefix-icon="Message"
            /></el-form-item>
            <el-form-item prop="userType">
              <el-select
                v-model="registerForm.userType"
                placeholder="选择用户类型"
                style="width: 100%"
                :teleported="false"
              >
                <el-option
                  label="病人 — 上传个人胸片、查看报告"
                  value="patient"
                />
                <el-option
                  label="医生 — 管理病人、编辑病例、分析诊断"
                  value="doctor"
                />
                <el-option
                  label="管理员 — 系统管理、分配医患关系"
                  value="admin"
                />
              </el-select>
            </el-form-item>
            <el-form-item prop="password"
              ><el-input
                v-model="registerForm.password"
                type="password"
                placeholder="密码（至少6位）"
                prefix-icon="Lock"
                show-password
            /></el-form-item>
            <el-form-item prop="confirmPassword"
              ><el-input
                v-model="registerForm.confirmPassword"
                type="password"
                placeholder="确认密码"
                prefix-icon="Lock"
                show-password
                @keyup.enter="handleRegister"
            /></el-form-item>
            <el-form-item
              ><el-button
                type="primary"
                class="submit-btn register-btn"
                :loading="registering"
                @click="handleRegister"
                >注册</el-button
              ></el-form-item
            >
          </el-form>
        </div>
      </div>
      <div class="overlay-panel" :class="{ 'slide-left': !isLogin }">
        <div class="overlay-bg bg-login" :class="{ hidden: !isLogin }"></div>
        <div class="overlay-bg bg-register" :class="{ hidden: isLogin }"></div>
        <div class="overlay-content">
          <div class="guide-text" :class="{ hidden: !isLogin }">
            <h2>你好，朋友！</h2>
            <p>注册您的个人信息以使用网站的全部功能</p>
            <el-button class="guide-btn" round @click="toggleLogin"
              >注册</el-button
            >
          </div>
          <div class="guide-text" :class="{ hidden: isLogin }">
            <h2>欢迎回来！</h2>
            <p>输入您的个人信息以使用网站的全部功能</p>
            <el-button class="guide-btn" round @click="toggleLogin"
              >登录</el-button
            >
          </div>
        </div>
      </div>
    </div>
    <el-dialog
      v-model="forgotVisible"
      title="重置密码"
      width="400px"
      :close-on-click-modal="false"
      center
    >
      <el-form
        ref="forgotFormRef"
        :model="forgotForm"
        :rules="forgotRules"
        label-width="0"
        @submit.prevent="handleForgotSubmit"
      >
        <el-form-item prop="username"
          ><el-input
            v-model="forgotForm.username"
            placeholder="请输入用户名"
            prefix-icon="User"
        /></el-form-item>
        <el-form-item prop="email"
          ><el-input
            v-model="forgotForm.email"
            placeholder="请输入注册邮箱"
            prefix-icon="Message"
        /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="forgotVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="forgotLoading"
          @click="handleForgotSubmit"
          >发送重置链接</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { forgotPasswordApi, registerApi } from "@/api/auth";
import { useUserStore } from "@/stores/user";
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

const router = useRouter();
const route = useRoute();
const userStore = useUserStore();
const isLogin = ref(true);
const rememberMe = ref(false);

const loginFormRef = ref(null);
const loading = ref(false);
const loginForm = reactive({ username: "", password: "" });
const loginRules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    { min: 3, max: 50, message: "用户名长度为 3-50 个字符", trigger: "blur" },
  ],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" },
    { min: 6, message: "密码至少 6 个字符", trigger: "blur" },
  ],
};

async function handleLogin() {
  const valid = await loginFormRef.value.validate().catch(() => false);
  if (!valid) return;
  loading.value = true;
  try {
    await userStore.login({
      username: loginForm.username,
      password: loginForm.password,
    });
    ElMessage.success("登录成功");
    router.push(route.query.redirect || "/");
  } catch {
    /* 拦截器已处理 */
  } finally {
    loading.value = false;
  }
}

function toggleLogin() {
  isLogin.value = !isLogin.value;
}

const forgotVisible = ref(false);
const forgotLoading = ref(false);
const forgotFormRef = ref(null);
const forgotForm = reactive({ username: "", email: "" });
const forgotRules = {
  username: [{ required: true, message: "请输入用户名", trigger: "blur" }],
  email: [
    { required: true, message: "请输入邮箱", trigger: "blur" },
    { type: "email", message: "请输入有效邮箱", trigger: "blur" },
  ],
};

function handleForgotPassword() {
  forgotForm.username = "";
  forgotForm.email = "";
  forgotVisible.value = true;
}

async function handleForgotSubmit() {
  const valid = await forgotFormRef.value.validate().catch(() => false);
  if (!valid) return;
  forgotLoading.value = true;
  try {
    await forgotPasswordApi({
      username: forgotForm.username,
      email: forgotForm.email,
    });
    ElMessage.success("重置链接已发送，请查收邮件");
    forgotVisible.value = false;
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || "操作失败");
  } finally {
    forgotLoading.value = false;
  }
}

const registerFormRef = ref(null);
const registering = ref(false);
const registerForm = reactive({
  name: "",
  email: "",
  password: "",
  confirmPassword: "",
  userType: "patient",
});
const registerRules = {
  name: [
    { required: true, message: "请输入姓名", trigger: "blur" },
    { min: 2, max: 20, message: "姓名长度 2-20 个字符", trigger: "blur" },
  ],
  email: [
    { required: true, message: "请输入邮箱", trigger: "blur" },
    { type: "email", message: "请输入有效的邮箱地址", trigger: "blur" },
  ],
  userType: [{ required: true, message: "请选择用户类型", trigger: "change" }],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" },
    { min: 6, message: "密码至少 6 个字符", trigger: "blur" },
  ],
  confirmPassword: [
    { required: true, message: "请确认密码", trigger: "blur" },
    {
      validator: (rule, value, callback) =>
        callback(
          value !== registerForm.password
            ? new Error("两次输入的密码不一致")
            : undefined,
        ),
      trigger: "blur",
    },
  ],
};

async function handleRegister() {
  const valid = await registerFormRef.value.validate().catch(() => false);
  if (!valid) return;
  registering.value = true;
  try {
    await registerApi({
      username: registerForm.name,
      email: registerForm.email,
      password: registerForm.password,
      user_type: registerForm.userType,
    });
    ElMessage.success("注册成功，请登录");
    isLogin.value = true;
  } catch {
    /* 拦截器已处理 */
  } finally {
    registering.value = false;
  }
}
</script>

<style scoped>
.login-page {
  --accent: #2a9d8f;
  --accent-d: #1b7a6e;
  --card-bg: rgba(255, 255, 255, 0.92);
  --input-bg: #f5f6f8;
  --text-main: #1a1a2e;
  --text-sub: #6b7280;
  --border: rgba(0, 0, 0, 0.08);
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    url("@/assets/xjtubk.png") center / cover no-repeat,
    #0f1219;
  z-index: 1000;
  overflow: hidden;
}

/* 背景暗色遮罩，让文字更清晰 */
.login-page::before {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 0;
  pointer-events: none;
}

/* 左上角 logo */
.corner-logo {
  position: absolute;
  top: 24px;
  left: 28px;
  height: 160px;
  width: auto;
  z-index: 3;
  filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.5)) brightness(2);
}

.bg-glow {
  display: none;
}

.page-top-info {
  position: absolute;
  top: 40px;
  left: 50%;
  transform: translateX(-50%);
  text-align: center;
  z-index: 1;
}
.top-logo {
  font-size: 36px;
  margin-bottom: 6px;
}
.top-title {
  font-size: 26px;
  font-weight: 800;
  color: #fff;
  margin: 0 0 6px;
  letter-spacing: 0.5px;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.top-desc {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
  margin: 0;
  font-weight: 600;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
}

.page-bottom-info {
  position: absolute;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 32px;
  z-index: 1;
}
.bottom-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
  font-weight: 600;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
}
.bottom-icon {
  font-size: 16px;
}

.login-card {
  position: relative;
  width: 900px;
  max-width: 96vw;
  min-height: 560px;
  background: var(--card-bg);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--border);
  border-radius: 24px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.15);
  overflow: hidden;
  z-index: 2;
}

.card-header {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 72px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  z-index: 15;
  display: flex;
  align-items: center;
  padding: 0 32px;
  box-sizing: border-box;
  border-bottom: 1px solid var(--border);
}
.header-inner {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
}
.header-icon {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  background: linear-gradient(135deg, var(--accent-d), var(--accent));
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.icon-lung {
  font-size: 24px;
  line-height: 1;
}
.header-text {
  display: flex;
  flex-direction: column;
}
.system-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-main);
  margin: 0;
  letter-spacing: -0.3px;
}
.system-desc {
  font-size: 12px;
  color: var(--text-sub);
  margin: 2px 0 0;
}

.fixed-area {
  position: absolute;
  top: 72px;
  height: calc(100% - 72px);
  width: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px 36px 36px;
  box-sizing: border-box;
  z-index: 1;
  overflow: hidden;
}
.login-area {
  left: 0;
}
.register-area {
  left: 50%;
  border-left: 1px solid var(--border);
}

.form-content {
  width: 100%;
  max-width: 310px;
  transition: transform 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  transform: translateX(0);
}
.login-area.slide-out .form-content {
  transform: translateX(-120%);
}
.register-area.slide-out .form-content {
  transform: translateX(120%);
}

.form-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-main);
  margin-bottom: 6px;
}
.form-sub {
  font-size: 13px;
  color: var(--text-sub);
  margin-bottom: 24px;
}

.form-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: -6px 0 18px;
}
.form-options :deep(.el-checkbox__label) {
  color: var(--text-sub);
  font-size: 13px;
}
.form-options :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: var(--accent);
  border-color: var(--accent);
}
.forgot-link {
  font-size: 13px;
  color: var(--text-sub);
  padding: 0;
  height: auto;
}
.forgot-link:hover {
  color: var(--accent);
}

.submit-btn {
  width: 100%;
  height: 46px;
  font-size: 15px;
  font-weight: 600;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-d), var(--accent));
  color: #fff;
  letter-spacing: 0.5px;
  transition: all 0.25s;
  box-shadow: 0 4px 18px rgba(86, 212, 193, 0.25);
}
.submit-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 24px rgba(86, 212, 193, 0.35);
}
.submit-btn:active {
  transform: translateY(0);
}
.register-btn {
  background: linear-gradient(135deg, #5b3fcf, #7b5fe0);
  box-shadow: 0 4px 18px rgba(123, 95, 224, 0.25);
}
.register-btn:hover {
  box-shadow: 0 6px 24px rgba(123, 95, 224, 0.35);
}

:deep(.el-input__wrapper) {
  background: var(--input-bg) !important;
  border: 1px solid #e0e3e8 !important;
  border-radius: 10px !important;
  box-shadow: none !important;
  transition: all 0.25s;
}
:deep(.el-input__wrapper:hover) {
  border-color: #c0c5ce !important;
}
.login-area :deep(.el-input__wrapper.is-focus) {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px rgba(42, 157, 143, 0.12) !important;
}
.register-area :deep(.el-input__wrapper.is-focus) {
  border-color: #4a7fd9 !important;
  box-shadow: 0 0 0 3px rgba(74, 127, 217, 0.12) !important;
}
:deep(.el-input__inner) {
  color: #2c3e50;
  font-size: 14px;
}
:deep(.el-input__inner::placeholder) {
  color: #a0a8b4;
}
:deep(.el-input__prefix),
:deep(.el-input__suffix) {
  color: #a0a8b4;
}
:deep(.el-form-item) {
  margin-bottom: 18px;
}
:deep(.el-select .el-input__wrapper) {
  background: var(--input-bg) !important;
}

.overlay-panel {
  position: absolute;
  top: 72px;
  width: 50%;
  height: calc(100% - 72px);
  left: 50%;
  z-index: 10;
  transition: left 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}
.overlay-panel.slide-left {
  left: 0;
}

.overlay-bg {
  position: absolute;
  inset: 0;
  transition: opacity 0.4s ease;
}
.bg-login {
  background: linear-gradient(135deg, #1a5c4e, #2a8c7a);
}
.bg-register {
  background: linear-gradient(135deg, #3b286e, #5b3fcf);
}
.overlay-bg.hidden {
  opacity: 0;
}

.overlay-content {
  position: relative;
  z-index: 2;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.guide-text {
  position: absolute;
  text-align: center;
  color: #fff;
  max-width: 240px;
  padding: 20px;
  transition:
    opacity 0.35s,
    visibility 0.35s;
  opacity: 1;
  visibility: visible;
}
.guide-text.hidden {
  opacity: 0;
  visibility: hidden;
}
.guide-text h2 {
  font-size: 26px;
  font-weight: 700;
  margin-bottom: 10px;
}
.guide-text p {
  font-size: 14px;
  opacity: 0.85;
  margin-bottom: 24px;
  line-height: 1.6;
}

.guide-btn {
  background: transparent;
  border: 2px solid rgba(255, 255, 255, 0.5);
  color: #fff;
  padding: 10px 36px;
  font-size: 14px;
  font-weight: 600;
  border-radius: 40px;
  transition: all 0.3s;
}
.guide-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: #fff;
}

.el-select-dropdown,
.el-popper.is-pure {
  z-index: 3000 !important;
}

@media (max-width: 768px) {
  .page-top-info {
    top: 20px;
  }
  .top-title {
    font-size: 20px;
  }
  .page-bottom-info {
    display: none;
  }
  .bg-glow {
    display: none;
  }
  .login-card {
    min-height: auto;
    border-radius: 20px;
  }
  .card-header {
    height: 58px;
    padding: 0 18px;
  }
  .header-icon {
    width: 34px;
    height: 34px;
    border-radius: 10px;
  }
  .icon-lung {
    font-size: 18px;
  }
  .system-title {
    font-size: 15px;
  }
  .system-desc {
    font-size: 10px;
  }
  .fixed-area {
    top: 58px;
    height: calc(100% - 58px);
    position: relative;
    width: 100%;
    left: 0 !important;
    border-left: none !important;
    padding: 24px 20px;
  }
  .register-area {
    border-top: 1px solid var(--border);
  }
  .overlay-panel {
    display: none;
  }
  .form-content {
    transform: none !important;
    max-width: 100%;
  }
}
</style>
