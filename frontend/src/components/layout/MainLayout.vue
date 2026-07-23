<template>
  <div class="main-layout">
    <!-- 顶部导航栏 -->
    <AppHeader />

    <!-- 下方区域：侧边栏 + 内容区 -->
    <div class="layout-body">
      <AppSidebar />

      <!-- 页面内容区 -->
      <main class="layout-content">
        <router-view />
      </main>
    </div>

    <!-- 用户须知免责声明弹窗（首次登录必读） -->
    <DisclaimerDialog
      :visible="showDisclaimer"
      @confirmed="onDisclaimerConfirmed"
    />
  </div>
</template>

<script setup>
import DisclaimerDialog from "@/components/DisclaimerDialog.vue";
import { useUserStore } from "@/stores/user";
import { ref, watch } from "vue";
import AppHeader from "./AppHeader.vue";
import AppSidebar from "./AppSidebar.vue";

const userStore = useUserStore();

const showDisclaimer = ref(false);
const hasChecked = ref(false);

watch(
  () => userStore.user?.id,
  (userId) => {
    if (!userId || hasChecked.value) return;
    hasChecked.value = true;
    showDisclaimer.value = true;
  },
  { immediate: true },
);

function onDisclaimerConfirmed() {
  showDisclaimer.value = false;
}
</script>

<style lang="scss" scoped>
.main-layout {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.layout-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.layout-content {
  flex: 1;
  background: $bg-color;
  overflow-y: auto;
  padding: $spacing-lg;
}
</style>
