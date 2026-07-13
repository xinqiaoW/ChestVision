<template>
  <div class="login-page">
    <!-- 左侧品牌区 -->
    <div class="login-brand">
      <div class="brand-content">
        <div class="brand-icon">🫁</div>
        <h1>ChestVision</h1>
        <p class="brand-subtitle">胸片X光智能分析系统</p>
        <div class="brand-features">
          <div class="feature-item">
            <span class="feature-icon">🧠</span>
            <span>AI 病灶检测 · 10 种胸部病变</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">💬</span>
            <span>智能对话 · LLM 分析解读</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">📊</span>
            <span>批量检测 · 报告生成</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 右侧登录区 -->
    <div class="login-form-area">
      <div class="login-card">
        <div class="login-header">
          <h2>欢迎回来</h2>
          <p>登录您的账号以继续</p>
        </div>

        <el-form
          ref="formRef"
          :model="loginForm"
          :rules="loginRules"
          label-width="0"
          size="large"
          @submit.prevent="handleLogin"
        >
          <el-form-item prop="username">
            <el-input
              v-model="loginForm.username"
              placeholder="用户名"
              prefix-icon="User"
            />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="loginForm.password"
              type="password"
              placeholder="密码"
              prefix-icon="Lock"
              show-password
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              class="login-btn"
              :loading="loading"
              @click="handleLogin"
              round
              >登 录</el-button
            >
          </el-form-item>
        </el-form>

        <div class="login-footer">
          <span>还没有账号？</span>
          <router-link to="/register">立即注册</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useUserStore } from "@/stores/user";
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

const router = useRouter();
const route = useRoute();
const userStore = useUserStore();

const formRef = ref(null);
const loading = ref(false);

/** 登录表单数据 */
const loginForm = reactive({
  username: "",
  password: "",
});

/** 表单验证规则 */
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

/** 处理登录 */
async function handleLogin() {
  const valid = await formRef.value.validate().catch(() => false);
  if (!valid) return;

  loading.value = true;
  try {
    await userStore.login({
      username: loginForm.username,
      password: loginForm.password,
    });

    ElMessage.success("登录成功");

    // 跳转到目标页面（如果有 redirect 参数）或首页
    const redirect = route.query.redirect || "/";
    router.push(redirect);
  } catch {
    // 错误已在 Axios 拦截器中统一处理
  } finally {
    loading.value = false;
  }
}
</script>

<style lang="scss" scoped>
.login-page {
  display: flex;
  height: 100vh;
  background: $bg-white;
}

.login-brand {
  flex: 1;
  background: linear-gradient(
    135deg,
    $primary-dark 0%,
    $primary-color 50%,
    $primary-light 100%
  );
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;

  &::before {
    content: "";
    position: absolute;
    width: 600px;
    height: 600px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.05);
    top: -200px;
    right: -200px;
  }
  &::after {
    content: "";
    position: absolute;
    width: 400px;
    height: 400px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.03);
    bottom: -100px;
    left: -100px;
  }
}

.brand-content {
  text-align: center;
  color: #fff;
  position: relative;
  z-index: 1;
}

.brand-icon {
  font-size: 72px;
  margin-bottom: 16px;
}

.brand-content h1 {
  font-size: 36px;
  font-weight: 700;
  margin-bottom: 8px;
  letter-spacing: -0.5px;
}

.brand-subtitle {
  font-size: 16px;
  opacity: 0.85;
  margin-bottom: 40px;
}

.brand-features {
  display: flex;
  flex-direction: column;
  gap: 16px;
  text-align: left;
  padding: 0 40px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 15px;
  .feature-icon {
    font-size: 20px;
  }
}

.login-form-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: $bg-color;
}

.login-card {
  width: 400px;
  padding: 48px 40px;
  background: $bg-white;
  border-radius: $border-radius-lg;
  box-shadow: $shadow-md;
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
  h2 {
    font-size: 24px;
    font-weight: 600;
    color: $text-primary;
    margin-bottom: 8px;
  }
  p {
    font-size: 14px;
    color: $text-secondary;
  }
}

.login-btn {
  width: 100%;
  height: 44px;
  font-size: 16px;
  font-weight: 600;
}

.login-footer {
  text-align: center;
  font-size: 14px;
  color: $text-secondary;
  a {
    color: $primary-color;
    font-weight: 600;
    margin-left: 4px;
    text-decoration: none;
  }
}
</style>
