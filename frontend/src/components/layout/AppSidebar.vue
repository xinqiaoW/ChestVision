<template>
  <aside class="app-sidebar">
    <el-menu
      :default-active="activeMenu"
      :router="true"
      :collapse="collapsed"
      background-color="#FFFFFF"
      text-color="#595959"
      active-text-color="#{$primary-color}"
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
  </aside>
</template>

<script setup>
import {
  Camera,
  ChatDotRound,
  Clock,
  Cpu,
  DataAnalysis,
} from "@element-plus/icons-vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const collapsed = ref(false);

const activeMenu = computed(() => "/" + route.path.split("/")[1]);

const menuItems = [
  { path: "/chat", title: "智能对话", icon: ChatDotRound },
  { path: "/detection", title: "检测工作台", icon: Camera },
  { path: "/training", title: "模型训练", icon: Cpu },
  { path: "/history", title: "历史记录", icon: Clock },
  { path: "/dashboard", title: "数据看板", icon: DataAnalysis },
];
</script>

<style lang="scss" scoped>
.app-sidebar {
  width: $sidebar-width;
  height: 100%;
  background: $sidebar-bg;
  border-right: 1px solid #eceff4;
  overflow-y: auto;

  .el-menu {
    border-right: none;
    height: 100%;
    padding: 8px;
  }

  .el-menu-item {
    height: 44px;
    line-height: 44px;
    margin: 2px 0;
    border-radius: $border-radius-sm;
    font-size: 14px;

    &.is-active {
      background-color: $sidebar-active-bg !important;
      color: $sidebar-active-text !important;
      font-weight: 600;
    }

    &:hover {
      background-color: #f5f7fa !important;
    }
  }
}
</style>
