export async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error("当前后端版本过旧，请关闭旧服务后重新运行 python web_app.py");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 ${response.status}`);
  return data;
}
