// 内置浏览器 JS 片段执行器：技能侧经 Bash 以一次性 node 进程驱动浏览器控制服务。
// 用法：node browser-runner.mjs <js文件路径>
// 每次调用都是全新进程：变量、import、模块缓存不跨调用保留；
// 观察输出统一走 nodeRepl.write(...)，代码末尾显式 return 的值也会被打印；
// 图片输出经 nodeRepl.emitImage(...) 落盘为临时 PNG 并打印路径。
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const skillDir = join(scriptDir, "..");
const docsRoot = join(skillDir, "docs");
const BRIDGE_SYMBOL = Symbol.for("common-code.browser-control-bridge");

function fail(message) {
  console.error(`browser-runner: ${message}`);
  process.exit(1);
}

// 读桥接文件（Electron 主进程启动控制服务后写入）；缺失或不可连按「宿主未运行」回退
async function loadBridge() {
  const bridgePath = process.env.COMMON_CODE_BROWSER_BRIDGE_FILE;
  if (!bridgePath) {
    fail("内置浏览器不可用：缺少 COMMON_CODE_BROWSER_BRIDGE_FILE（需要桌面端运行）。");
  }
  let info;
  try {
    info = JSON.parse(readFileSync(bridgePath, "utf8"));
  } catch {
    fail(`内置浏览器不可用：桥接文件缺失或陈旧（${bridgePath}）。需要桌面端运行。`);
  }
  const base = `http://127.0.0.1:${info.port}`;
  const headers = { Authorization: `Bearer ${info.token}` };
  // 连通性探测：端口失效（宿主已退出）与未启动同样处理
  try {
    await fetch(`${base}/browsers`, { headers, signal: AbortSignal.timeout(2000) });
  } catch {
    fail(`内置浏览器不可用：控制服务无响应（${base}）。需要桌面端运行。`);
  }
  return {
    async list() {
      const res = await fetch(`${base}/browsers`, { headers });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error?.message || "读取浏览器描述符失败");
      return data.browsers;
    },
    async execute(browserId, browserGeneration, command) {
      const res = await fetch(`${base}/execute`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ browserId, browserGeneration, command }),
        signal: AbortSignal.timeout(15000),
      });
      if (res.status === 401) throw new Error("控制服务鉴权失败：桥接令牌陈旧");
      return await res.json();
    },
    documentationRoot: docsRoot,
    assertAvailable() {},
  };
}

// 图片落盘计数（工作区临时目录，供文件读取工具查看）
let imageSeq = 0;
function makeNodeRepl() {
  return {
    // 文本/JSON 观察输出：对象序列化，字符串原样
    write(value) {
      if (typeof value === "string") console.log(value);
      else console.log(JSON.stringify(value, null, 2));
    },
    // 截图回传：写工作区临时 PNG，打印路径由智能体用文件读取工具查看
    emitImage(imageLike) {
      let bytes;
      let ext = "png";
      if (imageLike instanceof Uint8Array || Buffer.isBuffer(imageLike)) {
        bytes = imageLike;
      } else if (imageLike && imageLike.data) {
        bytes = Buffer.from(imageLike.data);
        if (String(imageLike.mimeType || "").includes("jpeg")) ext = "jpg";
      } else {
        fail("emitImage 参数不是可识别的图片数据");
      }
      const dir = join(process.cwd(), ".browser-shots");
      mkdirSync(dir, { recursive: true });
      const file = join(dir, `shot-${Date.now()}-${++imageSeq}.${ext}`);
      writeFileSync(file, bytes);
      console.log(`[image] ${file}`);
      return file;
    },
    setResponseMeta() {},
    requestMeta: null,
    cwd: process.cwd(),
    homeDir: process.env.USERPROFILE || process.env.HOME || "",
    tmpDir: process.env.TEMP || "/tmp",
  };
}

async function main() {
  const codePath = process.argv[2];
  if (!codePath) fail("用法：node browser-runner.mjs <js文件路径>");
  let code;
  try {
    code = readFileSync(codePath, "utf8");
  } catch (e) {
    fail(`读取 JS 片段失败：${e.message}`);
  }

  const bridge = await loadBridge();
  // SDK 经该符号取桥并装配 agent.browsers
  globalThis[BRIDGE_SYMBOL] = bridge;
  globalThis.nodeRepl = makeNodeRepl();

  const clientUrl = pathToFileURL(join(scriptDir, "browser-client.mjs")).href;
  const { setupBrowserRuntime } = await import(clientUrl);
  setupBrowserRuntime({ globals: globalThis });

  // 用户代码以 async 函数体执行：await 可用，显式 return 或 nodeRepl.write 产出结果
  const fn = new Function("nodeRepl", "agent", `return (async () => {\n${code}\n})()`);
  try {
    const result = await fn(globalThis.nodeRepl, globalThis.agent);
    // 显式 return 的值补打印（write 输出在前）
    if (result !== undefined) {
      console.log(JSON.stringify(result, null, 2));
    }
  } catch (e) {
    fail(`执行失败：${e && e.message ? e.message : String(e)}`);
  }
}

main();
