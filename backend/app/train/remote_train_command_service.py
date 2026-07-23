"""远程训练容器命令与 DLC payload 构造。"""

from __future__ import annotations

from typing import Any

from app.core.logger import get_logger
from app.entity.db_models import DatasetUpload, RemoteTrainingJob, TrainingTask
from app.train.remote_train_utils import _dlc_oss_uri, _object_parent_prefix


logger = get_logger(__name__)


class RemoteTrainingCommandMixin:
    """构造 DLC 环境变量、训练命令和 CreateJob payload。"""

    def _build_job_envs(
        self,
        task: TrainingTask,
        upload: DatasetUpload,
        output_prefix: str,
        callback_token: str,
    ) -> dict[str, str]:
        """构造注入 DLC 容器的环境变量。

        这里只放任务 ID、OSS 前缀和训练参数等小型配置，不放长期 AccessKey。
        """
        return {
            "TASK_ID": str(task.id),
            "TASK_UUID": task.task_uuid,
            "DATASET_ID": upload.dataset_uuid or "",
            "DATASET_PREFIX": upload.processed_prefix
            or _object_parent_prefix(upload.raw_object_key),
            "RAW_OBJECT_KEY": upload.raw_object_key,
            "RAW_DATASET_FILENAME": upload.raw_object_key.rsplit("/", 1)[-1],
            "OUTPUT_PREFIX": output_prefix,
            "OSS_BUCKET": self.settings.oss_bucket,
            "CALLBACK_TOKEN": callback_token,
            "METRICS_CALLBACK_URL": self.settings.remote_metrics_callback_url,
            "ERROR_CALLBACK_URL": self.settings.remote_error_callback_url,
            "CALLBACK_TIMEOUT_SECONDS": "5",
            "MODEL_NAME": task.model_name,
            "EPOCHS": str(task.epochs),
            "IMG_SIZE": str(task.img_size),
            "BATCH_SIZE": str(task.batch_size),
        }

    def _build_user_command(self, task: TrainingTask) -> str:
        """生成 Ultralytics 训练命令。

        DLC 会把数据集输入前缀和 output prefix 分别挂载到固定目录。
        当前支持两种输入：
        - processed 前缀下已有 data.yaml。
        - 数据集对象所在前缀下只有 dataset.zip，命令先安全解压到容器临时目录。
        """
        dataset = self.settings.pai_dataset_mount_path.rstrip("/")
        output = self.settings.pai_output_mount_path.rstrip("/")
        model = task.model_name
        if not model.endswith(".pt"):
            model = model + ".pt"
        optimizer = task.optimizer or "SGD"
        lr0 = task.lr0 if task.lr0 is not None else 0.01
        return (
            "set -e; "
            f"mkdir -p {output}/weights {output}/dataset; "
            f"python - <<'PY'\n"
            f"import json, os, platform, shutil, sys, time, traceback, urllib.request, zipfile\n"
            f"mount_dir = {dataset!r}\n"
            f"output_dir = {output!r}\n"
            f"work_dir = '/tmp/remote_train_dataset'\n"
            f"def write_error(stage, exc, extra=None):\n"
            f"    payload = {{\n"
            f"        'ok': False,\n"
            f"        'stage': stage,\n"
            f"        'error_type': type(exc).__name__,\n"
            f"        'error': str(exc),\n"
            f"        'task_uuid': os.environ.get('TASK_UUID'),\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'raw_object_key': os.environ.get('RAW_OBJECT_KEY'),\n"
            f"        'dataset_prefix': os.environ.get('DATASET_PREFIX'),\n"
            f"        'output_prefix': os.environ.get('OUTPUT_PREFIX'),\n"
            f"        'mount_dir': mount_dir,\n"
            f"        'output_dir': output_dir,\n"
            f"        'python': sys.version,\n"
            f"        'platform': platform.platform(),\n"
            f"        'traceback': traceback.format_exc(),\n"
            f"    }}\n"
            f"    if extra:\n"
            f"        payload.update(extra)\n"
            f"    text = json.dumps(payload, ensure_ascii=False, indent=2)\n"
            f"    try:\n"
            f"        url = os.environ.get('ERROR_CALLBACK_URL')\n"
            f"        token = os.environ.get('CALLBACK_TOKEN')\n"
            f"        task_uuid = os.environ.get('TASK_UUID')\n"
            f"        if url and token and task_uuid:\n"
            f"            body = dict(payload)\n"
            f"            body.update({{'task_uuid': task_uuid, 'token': token}})\n"
            f"            data = json.dumps(body, ensure_ascii=False).encode('utf-8')\n"
            f"            request = urllib.request.Request(url, data=data, headers={{'Content-Type': 'application/json'}}, method='POST')\n"
            f"            timeout = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '5'))\n"
            f"            with urllib.request.urlopen(request, timeout=timeout) as response:\n"
            f"                response.read()\n"
            f"    except Exception as callback_exc:\n"
            f"        print('error callback failed: ' + type(callback_exc).__name__, flush=True)\n"
            f"    print('REMOTE_TRAIN_ERROR ' + text, flush=True)\n"
            f"    try:\n"
            f"        os.makedirs(os.path.join(output_dir, 'dataset'), exist_ok=True)\n"
            f"        open(os.path.join(output_dir, 'dataset', 'train_error.json'), 'w').write(text)\n"
            f"    except Exception as write_exc:\n"
            f"        print('REMOTE_TRAIN_ERROR_WRITE_FAILED ' + repr(write_exc), flush=True)\n"
            f"def list_tree(root, max_entries=80, max_depth=3):\n"
            f"    entries = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return entries\n"
            f"    for current, dirs, files in os.walk(root):\n"
            f"        rel = os.path.relpath(current, root)\n"
            f"        depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
            f"        if depth >= max_depth:\n"
            f"            dirs[:] = []\n"
            f"        prefix = '' if rel == '.' else rel + '/'\n"
            f"        for name in sorted(dirs):\n"
            f"            entries.append(prefix + name + '/')\n"
            f"        for name in sorted(files):\n"
            f"            entries.append(prefix + name)\n"
            f"        if len(entries) >= max_entries:\n"
            f"            return entries[:max_entries]\n"
            f"    return entries\n"
            f"def find_named(root, names):\n"
            f"    wanted = {{name.lower() for name in names if name}}\n"
            f"    matches = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return matches\n"
            f"    for current, _, files in os.walk(root):\n"
            f"        for name in files:\n"
            f"            if name.lower() in wanted:\n"
            f"                matches.append(os.path.join(current, name))\n"
            f"    return sorted(matches, key=lambda path: (len(path), path))\n"
            f"def find_suffix(root, suffix):\n"
            f"    matches = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return matches\n"
            f"    for current, _, files in os.walk(root):\n"
            f"        for name in files:\n"
            f"            if name.lower().endswith(suffix):\n"
            f"                matches.append(os.path.join(current, name))\n"
            f"    return sorted(matches, key=lambda path: (len(path), path))\n"
            f"try:\n"
            f"    env_summary = {{key: os.environ.get(key) for key in ['TASK_ID', 'TASK_UUID', 'DATASET_ID', 'DATASET_PREFIX', 'RAW_OBJECT_KEY', 'RAW_DATASET_FILENAME', 'OUTPUT_PREFIX', 'MODEL_NAME', 'EPOCHS', 'IMG_SIZE', 'BATCH_SIZE']}}\n"
            f"    print('remote train env: ' + json.dumps(env_summary, ensure_ascii=False), flush=True)\n"
            f"    print('dataset mount dir: ' + mount_dir, flush=True)\n"
            f"    print('dataset mount exists: ' + str(os.path.exists(mount_dir)), flush=True)\n"
            f"    print('dataset mount isdir: ' + str(os.path.isdir(mount_dir)), flush=True)\n"
            f"    print('dataset mount entries: ' + json.dumps(list_tree(mount_dir), ensure_ascii=False), flush=True)\n"
            f"    print('output mount dir: ' + output_dir, flush=True)\n"
            f"    print('output mount exists: ' + str(os.path.exists(output_dir)), flush=True)\n"
            f"    print('output mount isdir: ' + str(os.path.isdir(output_dir)), flush=True)\n"
            f"    data_yaml_candidates = find_named(mount_dir, ['data.yaml'])\n"
            f"    if data_yaml_candidates:\n"
            f"        data_yaml = data_yaml_candidates[0]\n"
            f"    else:\n"
            f"        zip_name = os.environ.get('RAW_DATASET_FILENAME', 'dataset.zip')\n"
            f"        zip_candidates = find_named(mount_dir, [zip_name, 'dataset.zip']) or find_suffix(mount_dir, '.zip')\n"
            f"        print('dataset zip candidates: ' + json.dumps(zip_candidates[:20], ensure_ascii=False), flush=True)\n"
            f"        if not zip_candidates:\n"
            f"            raise FileNotFoundError('数据集 ZIP 不存在')\n"
            f"        zip_path = zip_candidates[0]\n"
            f"        print('dataset zip path: ' + zip_path, flush=True)\n"
            f"        if os.path.exists(work_dir):\n"
            f"            shutil.rmtree(work_dir)\n"
            f"        os.makedirs(work_dir, exist_ok=True)\n"
            f"        with zipfile.ZipFile(zip_path) as zf:\n"
            f"            zip_entries = zf.namelist()[:80]\n"
            f"            print('dataset zip entries: ' + json.dumps(zip_entries, ensure_ascii=False), flush=True)\n"
            f"            for member in zf.infolist():\n"
            f"                name = member.filename\n"
            f"                parts = [part for part in name.split('/') if part]\n"
            f"                if name.startswith('/') or '..' in parts:\n"
            f"                    raise ValueError('ZIP 包含不安全路径')\n"
            f"            zf.extractall(work_dir)\n"
            f"        print('extracted dataset entries: ' + json.dumps(list_tree(work_dir), ensure_ascii=False), flush=True)\n"
            f"        data_yaml_candidates = find_named(work_dir, ['data.yaml'])\n"
            f"        if not data_yaml_candidates:\n"
            f"            raise FileNotFoundError('data.yaml 不存在')\n"
            f"        data_yaml = data_yaml_candidates[0]\n"
            f"    print('resolved data.yaml: ' + data_yaml, flush=True)\n"
            f"    report = {{\n"
            f"        'ok': True,\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'upload_id': os.environ.get('RAW_OBJECT_KEY'),\n"
            f"        'data_yaml': data_yaml,\n"
            f"        'mount_dir': mount_dir,\n"
            f"        'mount_entries': list_tree(mount_dir),\n"
            f"        'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),\n"
            f"    }}\n"
            f"    open(os.path.join(output_dir, 'dataset', 'validation_report.json'), 'w').write(json.dumps(report, ensure_ascii=False, indent=2))\n"
            f"    open('/tmp/remote_data_yaml_path', 'w').write(data_yaml)\n"
            f"except Exception as exc:\n"
            f"    write_error('prepare_dataset', exc, {{'mount_entries': list_tree(mount_dir), 'output_entries': list_tree(output_dir), 'work_entries': list_tree(work_dir)}})\n"
            f"    raise\n"
            f"PY\n"
            f"python - <<'PY'\n"
            f"import json, os, platform, sys, traceback, urllib.request\n"
            f"data_yaml = open('/tmp/remote_data_yaml_path', encoding='utf-8').read().strip()\n"
            f"output_dir = {output!r}\n"
            f"model_name = {model!r}\n"
            f"epochs = {int(task.epochs)}\n"
            f"img_size = {int(task.img_size)}\n"
            f"batch_size = {int(task.batch_size)}\n"
            f"optimizer = {optimizer!r}\n"
            f"lr0 = {float(lr0)!r}\n"
            f"def write_error(stage, exc):\n"
            f"    payload = {{\n"
            f"        'ok': False,\n"
            f"        'stage': stage,\n"
            f"        'error_type': type(exc).__name__,\n"
            f"        'error': str(exc),\n"
            f"        'task_uuid': os.environ.get('TASK_UUID'),\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'data_yaml': data_yaml,\n"
            f"        'model_name': model_name,\n"
            f"        'epochs': epochs,\n"
            f"        'img_size': img_size,\n"
            f"        'batch_size': batch_size,\n"
            f"        'optimizer': optimizer,\n"
            f"        'lr0': lr0,\n"
            f"        'python': sys.version,\n"
            f"        'platform': platform.platform(),\n"
            f"        'traceback': traceback.format_exc(),\n"
            f"    }}\n"
            f"    text = json.dumps(payload, ensure_ascii=False, indent=2)\n"
            f"    try:\n"
            f"        url = os.environ.get('ERROR_CALLBACK_URL')\n"
            f"        token = os.environ.get('CALLBACK_TOKEN')\n"
            f"        task_uuid = os.environ.get('TASK_UUID')\n"
            f"        if url and token and task_uuid:\n"
            f"            body = dict(payload)\n"
            f"            body.update({{'task_uuid': task_uuid, 'token': token}})\n"
            f"            data = json.dumps(body, ensure_ascii=False).encode('utf-8')\n"
            f"            request = urllib.request.Request(url, data=data, headers={{'Content-Type': 'application/json'}}, method='POST')\n"
            f"            timeout = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '5'))\n"
            f"            with urllib.request.urlopen(request, timeout=timeout) as response:\n"
            f"                response.read()\n"
            f"    except Exception as callback_exc:\n"
            f"        print('error callback failed: ' + type(callback_exc).__name__, flush=True)\n"
            f"    print('REMOTE_TRAIN_ERROR ' + text, flush=True)\n"
            f"    try:\n"
            f"        os.makedirs(output_dir, exist_ok=True)\n"
            f"        open(os.path.join(output_dir, 'train_error.json'), 'w').write(text)\n"
            f"    except Exception as write_exc:\n"
            f"        print('REMOTE_TRAIN_ERROR_WRITE_FAILED ' + repr(write_exc), flush=True)\n"
            f"def to_float(value):\n"
            f"    if value is None:\n"
            f"        return None\n"
            f"    try:\n"
            f"        if hasattr(value, 'item'):\n"
            f"            value = value.item()\n"
            f"        return float(value)\n"
            f"    except Exception:\n"
            f"        return None\n"
            f"def first_float(*values):\n"
            f"    for value in values:\n"
            f"        parsed = to_float(value)\n"
            f"        if parsed is not None:\n"
            f"            return parsed\n"
            f"    return None\n"
            f"def collect_metrics(trainer):\n"
            f"    data = {{}}\n"
            f"    raw = getattr(trainer, 'metrics', {{}}) or {{}}\n"
            f"    if isinstance(raw, dict):\n"
            f"        data.update(raw)\n"
            f"    for attr in ('tloss', 'loss_items'):\n"
            f"        try:\n"
            f"            items = trainer.label_loss_items(getattr(trainer, attr), prefix='train')\n"
            f"            if isinstance(items, dict):\n"
            f"                data.update(items)\n"
            f"        except Exception:\n"
            f"            pass\n"
            f"    return data\n"
            f"def get_lr(trainer):\n"
            f"    lr = getattr(trainer, 'lr', None)\n"
            f"    if isinstance(lr, dict):\n"
            f"        values = [lr.get('lr/pg0'), lr.get('pg0')] + list(lr.values())\n"
            f"        return first_float(*values)\n"
            f"    return to_float(lr)\n"
            f"def post_metric(payload):\n"
            f"    url = os.environ.get('METRICS_CALLBACK_URL')\n"
            f"    token = os.environ.get('CALLBACK_TOKEN')\n"
            f"    task_uuid = os.environ.get('TASK_UUID')\n"
            f"    if not url or not token or not task_uuid:\n"
            f"        return\n"
            f"    payload.update({{'task_uuid': task_uuid, 'token': token, 'total_epochs': epochs}})\n"
            f"    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')\n"
            f"    request = urllib.request.Request(url, data=body, headers={{'Content-Type': 'application/json'}}, method='POST')\n"
            f"    timeout = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '5'))\n"
            f"    try:\n"
            f"        with urllib.request.urlopen(request, timeout=timeout) as response:\n"
            f"            response.read()\n"
            f"    except Exception as exc:\n"
            f"        print('metric callback failed: ' + type(exc).__name__, flush=True)\n"
            f"def on_train_epoch_end(trainer):\n"
            f"    epoch = int(getattr(trainer, 'epoch', 0)) + 1\n"
            f"    data = collect_metrics(trainer)\n"
            f"    post_metric({{\n"
            f"        'epoch': epoch,\n"
            f"        'box_loss': first_float(data.get('train/box_loss'), data.get('metrics/box_loss'), data.get('box_loss')),\n"
            f"        'cls_loss': first_float(data.get('train/cls_loss'), data.get('metrics/cls_loss'), data.get('cls_loss')),\n"
            f"        'dfl_loss': first_float(data.get('train/dfl_loss'), data.get('metrics/dfl_loss'), data.get('dfl_loss')),\n"
            f"        'precision': first_float(data.get('metrics/precision(B)'), data.get('precision')),\n"
            f"        'recall': first_float(data.get('metrics/recall(B)'), data.get('recall')),\n"
            f"        'map50': first_float(data.get('metrics/mAP50(B)'), data.get('map50')),\n"
            f"        'map50_95': first_float(data.get('metrics/mAP50-95(B)'), data.get('map50_95')),\n"
            f"        'lr': get_lr(trainer),\n"
            f"    }})\n"
            f"try:\n"
            f"    print('training config: ' + json.dumps({{'data_yaml': data_yaml, 'model_name': model_name, 'epochs': epochs, 'img_size': img_size, 'batch_size': batch_size, 'optimizer': optimizer, 'lr0': lr0}}, ensure_ascii=False), flush=True)\n"
            f"    from ultralytics import YOLO\n"
            f"    model = YOLO(model_name)\n"
            f"    model.add_callback('on_train_epoch_end', on_train_epoch_end)\n"
            f"    model.train(data=data_yaml, epochs=epochs, imgsz=img_size, batch=batch_size, optimizer=optimizer, lr0=lr0, project=output_dir, name='run', exist_ok=True, verbose=True, save=True, plots=False)\n"
            f"except Exception as exc:\n"
            f"    write_error('train_yolo', exc)\n"
            f"    raise\n"
            f"PY\n"
            f"python - <<'PY'\n"
            f"import json, os, shutil, time, traceback, urllib.request\n"
            f"output_dir = {output!r}\n"
            f"def list_tree(root, max_entries=80, max_depth=3):\n"
            f"    entries = []\n"
            f"    if not os.path.exists(root):\n"
            f"        return entries\n"
            f"    for current, dirs, files in os.walk(root):\n"
            f"        rel = os.path.relpath(current, root)\n"
            f"        depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
            f"        if depth >= max_depth:\n"
            f"            dirs[:] = []\n"
            f"        prefix = '' if rel == '.' else rel + '/'\n"
            f"        for name in sorted(dirs):\n"
            f"            entries.append(prefix + name + '/')\n"
            f"        for name in sorted(files):\n"
            f"            entries.append(prefix + name)\n"
            f"        if len(entries) >= max_entries:\n"
            f"            return entries[:max_entries]\n"
            f"    return entries\n"
            f"def fail(message):\n"
            f"    payload = {{\n"
            f"        'ok': False,\n"
            f"        'stage': 'collect_artifacts',\n"
            f"        'error': message,\n"
            f"        'task_uuid': os.environ.get('TASK_UUID'),\n"
            f"        'dataset_id': os.environ.get('DATASET_ID'),\n"
            f"        'output_dir': output_dir,\n"
            f"        'output_entries': list_tree(output_dir),\n"
            f"        'run_entries': list_tree(os.path.join(output_dir, 'run')),\n"
            f"        'traceback': traceback.format_exc(),\n"
            f"    }}\n"
            f"    text = json.dumps(payload, ensure_ascii=False, indent=2)\n"
            f"    try:\n"
            f"        url = os.environ.get('ERROR_CALLBACK_URL')\n"
            f"        token = os.environ.get('CALLBACK_TOKEN')\n"
            f"        task_uuid = os.environ.get('TASK_UUID')\n"
            f"        if url and token and task_uuid:\n"
            f"            body = dict(payload)\n"
            f"            body.update({{'task_uuid': task_uuid, 'token': token}})\n"
            f"            data = json.dumps(body, ensure_ascii=False).encode('utf-8')\n"
            f"            request = urllib.request.Request(url, data=data, headers={{'Content-Type': 'application/json'}}, method='POST')\n"
            f"            timeout = float(os.environ.get('CALLBACK_TIMEOUT_SECONDS', '5'))\n"
            f"            with urllib.request.urlopen(request, timeout=timeout) as response:\n"
            f"                response.read()\n"
            f"    except Exception as callback_exc:\n"
            f"        print('error callback failed: ' + type(callback_exc).__name__, flush=True)\n"
            f"    print('REMOTE_TRAIN_ERROR ' + text, flush=True)\n"
            f"    try:\n"
            f"        open(os.path.join(output_dir, 'train_error.json'), 'w').write(text)\n"
            f"    except Exception as write_exc:\n"
            f"        print('REMOTE_TRAIN_ERROR_WRITE_FAILED ' + repr(write_exc), flush=True)\n"
            f"    raise FileNotFoundError(message)\n"
            f"print('artifact output entries: ' + json.dumps(list_tree(output_dir), ensure_ascii=False), flush=True)\n"
            f"results_src = os.path.join(output_dir, 'run', 'results.csv')\n"
            f"best_src = os.path.join(output_dir, 'run', 'weights', 'best.pt')\n"
            f"last_src = os.path.join(output_dir, 'run', 'weights', 'last.pt')\n"
            f"if not os.path.exists(results_src):\n"
            f"    fail('results.csv 不存在: ' + results_src)\n"
            f"if not os.path.exists(best_src):\n"
            f"    fail('best.pt 不存在: ' + best_src)\n"
            f"os.makedirs(os.path.join(output_dir, 'weights'), exist_ok=True)\n"
            f"shutil.copyfile(results_src, os.path.join(output_dir, 'results.csv'))\n"
            f"shutil.copyfile(best_src, os.path.join(output_dir, 'weights', 'best.pt'))\n"
            f"if os.path.exists(last_src):\n"
            f"    shutil.copyfile(last_src, os.path.join(output_dir, 'weights', 'last.pt'))\n"
            f"payload={{'task_uuid':os.environ.get('TASK_UUID'),"
            f"'dataset_id':os.environ.get('DATASET_ID'),"
            f"'finished_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}}\n"
            f"open(os.path.join(output_dir, '_SUCCESS'),'w').write(json.dumps(payload))\n"
            f"PY"
        )

    def _build_create_job_payload(self, remote_job: RemoteTrainingJob) -> dict[str, Any]:
        """构造 PAI-DLC CreateJob payload。

        重点字段：
        - JobSpecs.Image：完整镜像地址，不是 PAI 自定义镜像 ID。
        - DataSources：把 OSS 数据集和输出前缀挂载到容器目录。
        - ImageConfig：只有 ACR 需要认证时才传。
        """
        job_spec: dict[str, Any] = {
            "Type": "Worker",
            "Image": self.settings.pai_image_uri,
            "PodCount": self.settings.pai_pod_count,
        }
        if self.settings.pai_ecs_spec:
            job_spec["EcsSpec"] = self.settings.pai_ecs_spec
        if self.settings.pai_acr_username and self.settings.pai_acr_password:
            job_spec["ImageConfig"] = {
                "DockerRegistry": self.settings.pai_acr_registry,
                "Username": self.settings.pai_acr_username,
                "Password": self.settings.pai_acr_password,
            }

        data_sources = [
            {
                "Uri": _dlc_oss_uri(
                    self.settings.oss_bucket,
                    self.settings.pai_oss_endpoint,
                    remote_job.input_dataset_prefix,
                    self.settings.pai_oss_uri_host,
                ),
                "MountPath": self.settings.pai_dataset_mount_path,
            },
            {
                "Uri": _dlc_oss_uri(
                    self.settings.oss_bucket,
                    self.settings.pai_oss_endpoint,
                    remote_job.output_prefix,
                    self.settings.pai_oss_uri_host,
                ),
                "MountPath": self.settings.pai_output_mount_path,
            },
        ]

        payload: dict[str, Any] = {
            "WorkspaceId": self.settings.pai_workspace_id,
            "DisplayName": f"chestx-train-{remote_job.training_task.task_uuid}",
            "JobType": self.settings.pai_job_type,
            "JobSpecs": [job_spec],
            "UserCommand": remote_job.user_command,
            "Envs": remote_job.envs or {},
            "JobMaxRunningTimeMinutes": self.settings.pai_job_max_running_minutes,
            "DataSources": data_sources,
        }
        if self.settings.pai_resource_id:
            payload["ResourceId"] = self.settings.pai_resource_id
        logger.info("PAI-DLC CreateJob DataSources: %s", data_sources)
        return payload
