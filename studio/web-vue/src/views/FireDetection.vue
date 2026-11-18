<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { api, postJson, formatTime } from '../api';
import CameraPreview from '../components/CameraPreview.vue';
import UiContentState from '../components/UiContentState.vue';
import UiModal from '../components/UiModal.vue';
import { preferences } from '../composables/preferences';
import { registerPageRefresher } from '../composables/pageRefresh';

const status = ref(null), records = ref([]), error = ref(''), loading = ref(true), busy = ref(false);
const fileInput = ref(null), uploadFile = ref(null), uploadUrl = ref(''), uploadResult = ref(null), uploadBusy = ref(false), uploadError = ref(''), showAnnotated = ref(true);
const deleteConfirmOpen = ref(false), deleteBusy = ref(false), pendingDelete = ref(null);
let timer, unregister, alive = true, pending = false;
const camera = computed(() => status.value?.cameras?.[0] || {});
const inference = computed(() => camera.value.inference || {});
const running = computed(() => status.value?.active_mode === 'astra_fire');
const alerting = computed(() => running.value && inference.value.fire_alert === true);
// Keep connecting while capture restarts; online status can lag the first frame.
const streamUrl = computed(() => running.value ? camera.value.stream_url : '');
const stateLabel = computed(() => !running.value ? '未启动' : !camera.value.online ? '等待相机画面' : alerting.value ? '发现明火' : '未发现明火');
const uploadImage = computed(() => showAnnotated.value && uploadResult.value?.annotated_image ? uploadResult.value.annotated_image : uploadUrl.value);

function clearUpload() {
  if (uploadUrl.value.startsWith('blob:')) URL.revokeObjectURL(uploadUrl.value);
  uploadFile.value = null;
  uploadUrl.value = '';
  uploadResult.value = null;
  uploadError.value = '';
  if (fileInput.value) fileInput.value.value = '';
}

function selectUpload(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return ElMessage.warning('请选择 JPEG、PNG 或 WebP 图片。');
  if (file.size > 10 * 1024 * 1024) return ElMessage.warning('图片不能超过 10 MB。');
  clearUpload();
  uploadFile.value = file;
  uploadUrl.value = URL.createObjectURL(file);
}

function dropUpload(event) {
  if (!uploadBusy.value && event.dataTransfer?.files?.length) selectUpload({ target: { files: event.dataTransfer.files, value: '' } });
}

async function detectUpload() {
  if (!uploadFile.value || uploadBusy.value) return;
  uploadBusy.value = true;
  uploadError.value = '';
  try {
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('图片读取失败。'));
      reader.readAsDataURL(uploadFile.value);
    });
    uploadResult.value = await postJson('/api/fire-image-detect', { image });
    showAnnotated.value = true;
  } catch (e) {
    uploadError.value = e.message || '图片检测失败，请重试。';
  } finally {
    uploadBusy.value = false;
  }
}

function askDelete(record) {
  pendingDelete.value = record;
  deleteConfirmOpen.value = true;
}

async function deleteRecord() {
  const record = pendingDelete.value;
  if (!record || deleteBusy.value) return;
  deleteBusy.value = true;
  try {
    await api(`/api/fire-evidence/${record.evidence_id}`, { method: 'DELETE' });
    records.value = records.value.filter(item => item.evidence_id !== record.evidence_id);
    ElMessage.success('火情记录已删除');
    deleteConfirmOpen.value = false;
    pendingDelete.value = null;
  } catch (e) {
    ElMessage.error(e.message || '删除失败，请重试');
  } finally {
    deleteBusy.value = false;
  }
}

async function load() {
  if (pending || !alive || (document.hidden && preferences.pauseBackground)) return;
  pending = true;
  try {
    const [nextStatus, evidence] = await Promise.all([
      api('/api/cameras', { signal: AbortSignal.timeout(8000) }),
      api('/api/fire-evidence', { signal: AbortSignal.timeout(8000) }),
    ]);
    if (!alive) return;
    status.value = nextStatus;
    records.value = Array.isArray(evidence.records) ? evidence.records.slice(0, 12) : [];
    error.value = '';
  } catch (e) {
    if (alive) error.value = e.message || '火情检测状态暂时无法读取';
  } finally {
    pending = false;
    loading.value = false;
  }
}

async function changeMode(mode) {
  if (busy.value || status.value?.switching) return;
  busy.value = true;
  try {
    await postJson('/api/camera-mode', { mode });
    await load();
  } catch (e) {
    ElMessage.error(e.message || '相机模式切换失败');
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(load, 2000);
  unregister = registerPageRefresher('fire', load);
});
onBeforeUnmount(() => {
  alive = false;
  window.clearInterval(timer);
  unregister?.();
  if (uploadUrl.value.startsWith('blob:')) URL.revokeObjectURL(uploadUrl.value);
});
</script>

<template>
  <div class="fire-page">
    <div class="fire-toolbar">
      <span class="badge" :class="alerting ? 'red' : running && camera.online ? 'green' : 'amber'" role="status">
        {{ stateLabel }}
      </span>
      <span v-if="running && camera.fps" class="subtle">{{ Number(camera.fps).toFixed(1) }} 帧/秒</span>
      <div class="action-row">
        <button class="btn primary" type="button" :disabled="busy || running" @click="changeMode('astra_fire')">
          <UiIcon name="camera" :size="15"/>{{ busy ? '切换中…' : '启动检测' }}
        </button>
        <button class="btn" type="button" :disabled="busy || !running" @click="changeMode('astra_raw')">
          <UiIcon name="x" :size="15"/>切回原始画面
        </button>
      </div>
    </div>

    <UiCard title="本地图片检测" glyph="upload" class="fire-upload-card">
      <div class="fire-upload-layout">
        <div class="fire-upload-zone" @dragover.prevent @drop.prevent="dropUpload">
          <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp" aria-label="选择火情检测图片" :disabled="uploadBusy" @change="selectUpload">
          <button v-if="!uploadUrl" class="fire-upload-empty" type="button" :disabled="uploadBusy" @click="fileInput?.click()">
            <UiIcon name="upload" :size="25"/><b>选择火情检测图片</b><span>点击选择，或将图片拖放到这里</span><small>JPEG、PNG 或 WebP，最大 10 MB</small>
          </button>
          <div v-else class="fire-upload-preview">
            <img :src="uploadImage" alt="火情图片检测预览">
            <div v-if="uploadBusy" class="fire-upload-progress"><UiContentState loading title="正在进行本地火情检测"/></div>
            <div v-else class="fire-upload-image-actions">
              <button v-if="uploadResult" class="btn" type="button" @click="showAnnotated = !showAnnotated">{{showAnnotated ? '查看原图' : '查看标注'}}</button>
              <button class="btn" type="button" @click="fileInput?.click()">更换图片</button>
              <button class="btn" type="button" aria-label="移除图片" @click="clearUpload"><UiIcon name="x" :size="15"/></button>
            </div>
          </div>
        </div>
        <div class="fire-upload-details">
          <span class="fire-upload-name" :title="uploadFile?.name">{{uploadFile?.name || '尚未选择图片'}}</span>
          <p class="fire-upload-note">使用板端火焰模型检测这张图片。测试结果仅在当前页面预览，不会保存到火情证据。</p>
          <div v-if="uploadResult" class="fire-upload-result" role="status">
            <span class="badge" :class="uploadResult.fire_alert ? 'red' : 'green'">{{uploadResult.fire_alert ? '检测到明火' : '未检测到明火'}}</span>
            <span>火焰数 <b>{{uploadResult.fire_count}}</b></span>
            <span>推理耗时 <b>{{Number(uploadResult.inference_ms).toFixed(1)}} ms</b></span>
          </div>
          <p v-if="uploadError" class="fire-error" role="alert">{{uploadError}}</p>
          <button class="btn primary fire-upload-submit" type="button" :disabled="!uploadFile || uploadBusy" @click="detectUpload">
            <UiIcon :name="uploadBusy ? 'camera' : 'play'" :size="15"/>{{uploadBusy ? '检测中…' : '开始图片检测'}}
          </button>
        </div>
      </div>
    </UiCard>

    <div class="fire-grid">
      <UiCard title="实时画面" glyph="camera" class="fire-camera-card">
        <CameraPreview v-if="running"
          :src="streamUrl"
          :generation="status?.generation"
          :mode="status?.active_mode"
          :online="Boolean(camera.online)"
          :switching="Boolean(status?.switching || busy)"
          :enabled="true"
          :pause-background="preferences.pauseBackground"
          :fit="preferences.cameraFit"
          :width="camera.width || 640"
          :height="camera.height || 480"
        />
        <UiContentState v-else icon="camera" title="火情检测未启动" description="切换到火情检测模式后显示实时画面"/>
        <div class="fire-metrics">
          <div><small>当前状态</small><b :class="alerting ? 'red' : ''">{{ stateLabel }}</b></div>
          <div><small>确认火焰数</small><b class="number" :class="alerting ? 'red' : ''">{{ running ? inference.fire_count ?? 0 : '—' }}</b></div>
          <div><small>推理耗时</small><b class="number">{{ inference.inference_ms == null ? '—' : `${Number(inference.inference_ms).toFixed(1)} ms` }}</b></div>
        </div>
        <p v-if="inference.error" class="fire-error" role="status">{{ inference.error }}</p>
        <p v-else-if="error" class="fire-error" role="status">{{ error }}</p>
      </UiCard>

      <UiCard title="火情证据" glyph="alert" class="fire-evidence-card">
        <div v-if="records.length" class="fire-records">
          <div v-for="record in records" :key="record.evidence_id" class="fire-record">
            <a :href="record.image_url" target="_blank" rel="noopener" :aria-label="`查看${formatTime(record.created_at, { seconds: true })}的火情原图`">
              <img :src="record.image_url" alt="火情检测告警画面" loading="lazy">
            </a>
            <span><b>发现明火 · {{ record.fire_count }} 处</b><time>{{ formatTime(record.created_at, { seconds: true }) }}</time></span>
            <button class="btn danger fire-record-delete" type="button" :aria-label="`删除${formatTime(record.created_at, { seconds: true })}的火情记录`" @click="askDelete(record)">
              <UiIcon name="trash" :size="14"/>删除
            </button>
          </div>
        </div>
        <UiContentState v-else-if="loading" loading title="正在读取火情证据" description="正在连接相机服务"/>
        <UiContentState v-else icon="file" title="暂无火情记录" description="确认的明火告警画面会保存在这里"/>
      </UiCard>
    </div>
    <UiModal v-model="deleteConfirmOpen" title="确认删除火情记录" width="min(420px,92vw)">
      <p class="delete-confirm-copy">删除后将无法恢复这条火情记录及对应图片。</p>
      <template #footer>
        <button class="btn" type="button" :disabled="deleteBusy" @click="deleteConfirmOpen = false">取消</button>
        <button class="btn danger" type="button" :disabled="deleteBusy" @click="deleteRecord"><UiIcon name="trash" :size="14"/>{{deleteBusy ? '删除中…' : '确认删除'}}</button>
      </template>
    </UiModal>
  </div>
</template>

<style scoped>
/* Frames are absolutely positioned; reserve space even after the placeholder leaves. */
.fire-camera-card :deep(.camera-body){flex:none;width:100%;padding:0;aspect-ratio:var(--camera-aspect-ratio,4 / 3);overflow:hidden}
.fire-page{display:grid;gap:14px;min-width:0}.fire-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.fire-toolbar .action-row{margin-left:auto}.fire-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,1fr);gap:14px;align-items:start}.fire-camera-card,.fire-evidence-card{min-width:0}.fire-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:14px 2px 0}.fire-metrics>div{display:grid;gap:5px;min-width:0}.fire-metrics small{color:var(--muted);font-size:11px}.fire-metrics b{overflow-wrap:anywhere}.fire-error{margin-top:10px;color:#dc2626;font-size:12px}.fire-records{display:grid;gap:10px}.fire-record{display:grid;grid-template-columns:112px minmax(0,1fr) auto;gap:12px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);color:inherit}.fire-record:last-child{border-bottom:0}.fire-record img{display:block;width:112px;aspect-ratio:4/3;object-fit:cover;border-radius:6px;background:#101827}.fire-record span{display:grid;gap:6px;min-width:0}.fire-record b{color:#dc2626;font-size:13px}.fire-record time{color:var(--muted);font-size:11px}.fire-record-delete{min-height:30px;padding:5px 8px;font-size:11px;gap:4px}.fire-upload-layout{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(240px,1fr);gap:20px;align-items:start}.fire-upload-zone{position:relative;min-width:0;min-height:260px;border:1px dashed var(--line);border-radius:7px;background:var(--input);overflow:hidden}.fire-upload-zone>input{display:none}.fire-upload-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;width:100%;min-height:260px;padding:20px;border:0;background:transparent;color:var(--muted);cursor:pointer}.fire-upload-empty svg{color:var(--cyan)}.fire-upload-empty b{color:var(--ink);font-weight:500}.fire-upload-empty small{font-size:11px}.fire-upload-preview{position:relative;height:320px;background:#080d12}.fire-upload-preview>img{display:block;width:100%;height:100%;object-fit:contain}.fire-upload-image-actions{position:absolute;right:10px;bottom:10px;display:flex;gap:7px}.fire-upload-image-actions .btn{background:#151b23ed}.fire-upload-progress{position:absolute;inset:0;display:flex;background:#080d12ed}.fire-upload-progress :deep(.content-state){min-height:0}.fire-upload-details{display:flex;flex-direction:column;gap:14px;min-width:0}.fire-upload-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:12px}.fire-upload-note{margin:0;color:var(--muted);font-size:12px;line-height:1.7}.fire-upload-result{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:11px;border:1px solid var(--line);border-radius:6px;font-size:12px}.fire-upload-result b{font-variant-numeric:tabular-nums}.fire-upload-submit{align-self:flex-start;margin-top:auto}@media(max-width:850px){.fire-grid{grid-template-columns:1fr}}@media(max-width:700px){.fire-upload-layout{grid-template-columns:1fr}.fire-upload-preview,.fire-upload-empty{height:220px;min-height:220px}.fire-upload-submit{width:100%}}@media(max-width:520px){.fire-toolbar .action-row{width:100%;margin-left:0}.fire-metrics{grid-template-columns:1fr 1fr}.fire-record{grid-template-columns:88px minmax(0,1fr) auto;gap:8px}.fire-record img{width:88px}.fire-record-delete{padding:5px 6px}}
.delete-confirm-copy{margin:0;color:var(--muted);font-size:13px;line-height:1.7}
</style>
