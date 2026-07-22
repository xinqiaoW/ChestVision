<template>
  <aside class="app-sidebar" :class="{ collapsed }">
    <!-- Logo 区域 -->
    <div class="sidebar-logo" @click="$router.push('/')">
      <img src="@/assets/xjtulogob.png" alt="logo" class="sidebar-logo-img" />
      <Transition name="fade">
        <div v-show="!collapsed" class="logo-info">
          <span class="logo-text">ChestVision</span>
          <span class="logo-subtitle">智能影像分析平台</span>
        </div>
      </Transition>
    </div>

    <!-- 导航菜单 -->
    <el-menu
      :default-active="activeMenu"
      :router="true"
      :collapse="collapsed"
      background-color="transparent"
      text-color="#8b8fa3"
      active-text-color="#2A9D8F"
    >
      <el-menu-item
        v-for="item in menuItems"
        :key="item.path"
        :index="item.path"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <span>{{ item.title }}</span>
      </el-menu-item>
    </el-menu>

    <!-- 底部：折叠按钮 + 用户区 -->
    <div class="sidebar-footer">
      <div class="collapse-btn" @click="collapsed = !collapsed">
        <el-icon
          ><component :is="collapsed ? 'DArrowRight' : 'DArrowLeft'"
        /></el-icon>
      </div>
      <div class="sidebar-user" v-if="!collapsed">
        <el-avatar :size="40">{{
          userStore.username?.charAt(0)?.toUpperCase()
        }}</el-avatar>
        <div class="user-info">
          <span class="user-name">Dr. {{ userStore.username }}</span>
          <span class="user-role">{{ roleLabel }}</span>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { useUserStore } from "@/stores/user";
import {
  Camera,
  ChatDotRound,
  Clock,
  Cpu,
  DataAnalysis,
  Document,
  User,
} from "@element-plus/icons-vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const userStore = useUserStore();
const collapsed = ref(false);

const activeMenu = computed(() => "/" + route.path.split("/")[1]);

const roleLabel = computed(() => {
  const map = { admin: "管理员", doctor: "医生", patient: "患者" };
  return map[userStore.userType] || "用户";
});

const allMenuItems = [
  {
    path: "/chat",
    title: "智能对话",
    icon: ChatDotRound,
    roles: ["admin", "doctor", "patient"],
  },
  {
    path: "/detection",
    title: "检测工作台",
    icon: Camera,
    roles: [],
  },
  {
    path: "/patients",
    title: "患者管理",
    icon: User,
    roles: ["admin", "doctor"],
  },
  {
    path: "/medical-records",
    title: "病例管理",
    icon: Document,
    roles: ["admin", "doctor", "patient"],
  },
  {
    path: "/history",
    title: "历史记录",
    icon: Clock,
    roles: ["admin", "doctor", "patient"],
  },
  {
    path: "/dashboard",
    title: "数据看板",
    icon: DataAnalysis,
    roles: ["admin", "doctor"],
  },
  { path: "/training", title: "模型训练", icon: Cpu, roles: ["admin"] },
];

const menuItems = computed(() =>
  allMenuItems.filter((item) => item.roles.includes(userStore.userType)),
);
</script>

<style lang="scss" scoped>
.app-sidebar {
  width: $sidebar-width;
  height: 100%;
  background: $sidebar-bg;
  display: flex;
  flex-direction: column;
  transition: width 0.25s ease;
  overflow: hidden;

  &.collapsed {
    width: $sidebar-collapsed-width;
  }
}

// ── Logo 区域 ──────────────────────────────────────
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px;
  cursor: pointer;
  background: $sidebar-logo-bg;
  border-bottom: 1px solid $sidebar-divider;
  min-height: 60px;

  .logo-icon {
    font-size: 28px;
    flex-shrink: 0;
    line-height: 1;
  }

  .sidebar-logo-img {
    height: 36px;
    width: auto;
    flex-shrink: 0;
  }

  .logo-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow: hidden;
  }

  .logo-text {
    font-size: 17px;
    font-weight: 700;
    color: #e8eaf0;
    letter-spacing: 0.5px;
    white-space: nowrap;
  }

  .logo-subtitle {
    font-size: 11px;
    color: $sidebar-text;
    white-space: nowrap;
    letter-spacing: 0.3px;
  }
}

// ── 导航菜单 ──────────────────────────────────────
.el-menu {
  flex: 1;
  border-right: none !important;
  padding: 8px 10px;
  overflow-y: auto;
  overflow-x: hidden;

  .el-menu-item {
    height: 44px;
    line-height: 44px;
    margin: 2px 0;
    border-radius: $border-radius-sm;
    font-size: 14px;
    position: relative;
    transition: all 0.2s ease;

    // 默认状态
    background: transparent !important;
    color: $sidebar-text !important;

    // 激活状态：左侧青色指示条 + 半透明背景
    &.is-active {
      background: $sidebar-active-bg !important;
      color: $sidebar-active-text !important;
      font-weight: 600;

      &::before {
        content: "";
        position: absolute;
        left: 0;
        top: 10px;
        bottom: 10px;
        width: 3px;
        background: $sidebar-active-bar;
        border-radius: 0 2px 2px 0;
      }
    }

    // 悬停状态
    &:hover {
      background: rgba(255, 255, 255, 0.06) !important;
      color: $sidebar-text-hover !important;
    }

    // 图标间距
    .el-icon {
      margin-right: 12px;
      font-size: 18px;
    }
  }
}

// ── 底部区域 ──────────────────────────────────────
.sidebar-footer {
  padding: 10px 12px 12px;
  border-top: 1px solid $sidebar-divider;
  background: $sidebar-footer-bg;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.collapse-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  border-radius: $border-radius-sm;
  cursor: pointer;
  color: $sidebar-text;
  font-size: 14px;
  transition: all 0.2s;

  &:hover {
    background: $sidebar-collapse-hover;
    color: $sidebar-text-hover;
  }
}

.sidebar-user {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 4px;

  .user-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow: hidden;
  }

  .user-name {
    font-size: 14px;
    color: #e0e4ea;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .user-role {
    font-size: 12px;
    color: $sidebar-text;
    white-space: nowrap;
    font-weight: 400;
  }
}

// ── 折叠时菜单项居中 ──────────────────────────────
.app-sidebar.collapsed .el-menu .el-menu-item {
  justify-content: center;
  padding: 0 !important;

  .el-icon {
    margin-right: 0;
  }

  &.is-active::before {
    left: 2px;
    top: 12px;
    bottom: 12px;
  }
}

// ── 过渡动画 ──────────────────────────────────────
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
