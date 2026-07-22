export API=http://127.0.0.1:8000
export TOKEN=$(
  curl -s -X POST "$API/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"test_admin","password":"test_admin"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])"
)

curl -s "$API/api/auth/me" -H "Authorization: Bearer $TOKEN"

curl -s -X POST "$API/api/training/remote/uploads" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "scene_name": "remote_test_scene",
    "dataset_name": "remote_test_dataset",
    "filename": "dataset.zip",
    "content_type": "application/zip",
    "expected_size": 1024
  }' | tee /tmp/remote-upload.json


npm run dev -- --host 0.0.0.0  --port 80