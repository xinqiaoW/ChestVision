<template>
  <div class="register-page">
    <div class="register-brand">
      <div class="brand-content">
        <div class="brand-icon">🫁</div>
        <h1>ChestVision</h1>
        <p class="brand-subtitle">加入我们，开启智能诊断之旅</p>
      </div>
    </div>
    <div class="register-form-area">
      <div class="register-card">
        <div class="register-header">
          <h2>创建账号</h2>
          <p>注册后即可使用胸片X光智能分析系统</p>
        </div>
        <el-form
          ref="formRef"
          :model="registerForm"
          :rules="registerRules"
          label-width="0"
          size="large"
          @submit.prevent="handleRegister"
        >
          <el-form-item prop="username">
            <el-input
              v-model="registerForm.username"
              placeholder="用户名"
              prefix-icon="User"
            />
          </el-form-item>
          <el-form-item prop="email">
            <el-input
              v-model="registerForm.email"
              placeholder="邮箱"
              prefix-icon="Message"
            />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="registerForm.password"
              type="password"
              placeholder="密码（至少6位）"
              prefix-icon="Lock"
              show-password
            />
          </el-form-item>
          <el-form-item prop="confirmPassword">
            <el-input
              v-model="registerForm.confirmPassword"
              type="password"
              placeholder="确认密码"
              prefix-icon="Lock"
              show-password
              @keyup.enter="handleRegister"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              class="register-btn"
              :loading="loading"
              @click="handleRegister"
              round
              >注 册</el-button
            >
          </el-form-item>
        </el-form>
        <div class="register-footer">
          <span>已有账号？</span>
          <router-link to="/login">立即登录</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { registerApi } from "@/api/auth";
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";

const router = useRouter();
const formRef = ref(null);
const loading = ref(false);

/** 注册表单数据 */
const registerForm = reactive({
  username: "",
  email: "",
  password: "",
  confirmPassword: "",
});

/** 确认密码验证器 */
const validateConfirmPassword = (rule, value, callback) => {
  if (value !== registerForm.password) {
    callback(new Error("两次输入的密码不一致"));
  } else {
    callback();
  }
};

/** 表单验证规则 */
const registerRules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    { min: 3, max: 50, message: "用户名长度为 3-50 个字符", trigger: "blur" },
  ],
  email: [
    { required: true, message: "请输入邮箱", trigger: "blur" },
    { type: "email", message: "请输入有效的邮箱地址", trigger: "blur" },
  ],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" },
    { min: 6, message: "密码至少 6 个字符", trigger: "blur" },
  ],
  confirmPassword: [
    { required: true, message: "请确认密码", trigger: "blur" },
    { validator: validateConfirmPassword, trigger: "blur" },
  ],
};

/** 处理注册 */
async function handleRegister() {
  const valid = await formRef.value.validate().catch(() => false);
  if (!valid) return;

  loading.value = true;
  try {
    await registerApi({
      username: registerForm.username,
      email: registerForm.email,
      password: registerForm.password,
    });

    ElMessage.success("注册成功，请登录");
    router.push("/login");
  } catch {
    // 错误已在 Axios 拦截器中统一处理
  } finally {
    loading.value = false;
  }
}
</script>

<style lang="scss" scoped>
.register-page {
  display: flex;
  height: 100vh;
  background: $bg-white;
}
.register-brand {
  flex: 1;
  background: linear-gradient(
    135deg,
    $primary-dark,
    $primary-color,
    $primary-light
  );
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
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
}
.brand-subtitle {
  font-size: 16px;
  opacity: 0.85;
}
.register-form-area {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: $bg-color;
}
.register-card {
  width: 400px;
  padding: 40px;
  background: $bg-white;
  border-radius: $border-radius-lg;
  box-shadow: $shadow-md;
}
.register-header {
  text-align: center;
  margin-bottom: 28px;
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
.register-btn {
  width: 100%;
  height: 44px;
  font-size: 16px;
  font-weight: 600;
}
.register-footer {
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
