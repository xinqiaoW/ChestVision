<template>
  <aside class="app-sidebar" :class="{ collapsed }">
    <!-- Logo 区域 -->
    <div class="sidebar-logo" @click="$router.push('/')">
      <span class="logo-icon">🫁</span>
      <span v-show="!collapsed" class="logo-text">ChestVision</span>
    </div>

    <!-- 导航菜单 -->
    <el-menu
      :default-active="activeMenu"
      :router="true"
      :collapse="collapsed"
      background-color="transparent"
      text-color="#595959"
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
        <el-avatar :size="28">{{ userStore.username?.charAt(0) }}</el-avatar>
        <span class="user-name">{{ userStore.username }}</span>
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
  border-right: 1px solid #eceff4;
  display: flex;
  flex-direction: column;
  transition: width 0.25s ease;
  &.collapsed {
    width: $sidebar-collapsed-width;
  }
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 18px;
  cursor: pointer;
  border-bottom: 1px solid #f0f0f0;
  .logo-icon {
    font-size: 28px;
  }
  .logo-text {
    font-size: 16px;
    font-weight: 800;
    color: $text-primary;
    letter-spacing: -0.5px;
  }
}

.el-menu {
  flex: 1;
  border-right: none !important;
  padding: 8px;

  .el-menu-item {
    height: 42px;
    line-height: 42px;
    margin: 2px 0;
    border-radius: $border-radius-sm;
    font-size: 13px;
    transition: all 0.2s;

    &.is-active {
      background: linear-gradient(135deg, #e6f7f5, #f0faf8) !important;
      color: $primary-color !important;
      font-weight: 600;
      box-shadow: inset 3px 0 0 $primary-color;
    }
    &:hover {
      background: #f5f7fa !important;
    }
  }
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.collapse-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 36px;
  border-radius: $border-radius-sm;
  cursor: pointer;
  color: $text-secondary;
  transition: all 0.2s;
  &:hover {
    background: #f5f7fa;
    color: $primary-color;
  }
}

.sidebar-user {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px;
  .user-name {
    font-size: 13px;
    color: $text-regular;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
