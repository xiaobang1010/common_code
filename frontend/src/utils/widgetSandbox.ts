/**
 * Widget iframe 沙箱：文档构建、流式/完成两阶段消毒。
 * 与后端 show_widget 工具配套，逻辑与既定实现保持一致。
 */

export const CANVAS_FALLBACK_HEIGHT_PX = 320;
export const CANVAS_MIN_HEIGHT_PX = 120;
export const INLINE_PX_HEIGHT_RE = /^(\d+(?:\.\d+)?)px$/;

// 外部资源白名单（CSP 强制）：仅这四个 CDN 域可加载脚本/字体/图片
export const CDN_WHITELIST = [
	"cdnjs.cloudflare.com",
	"esm.sh",
	"cdn.jsdelivr.net",
	"unpkg.com",
];

const PRESET_CSS_CLASSES = `
/* 文本类 */
.t { font-family: var(--font-sans, system-ui, -apple-system, sans-serif); font-size: 14px; fill: var(--color-text-primary, currentColor); }
.ts { font-family: var(--font-sans, system-ui, -apple-system, sans-serif); font-size: 12px; fill: var(--color-text-secondary, currentColor); }
.th { font-family: var(--font-sans, system-ui, -apple-system, sans-serif); font-size: 14px; font-weight: 500; fill: var(--color-text-primary, currentColor); }

/* 容器类 */
.box { fill: var(--color-background-secondary, #f5f5f5); stroke: var(--color-border-tertiary, #e0e0e0); stroke-width: 1; rx: 6; }
.node { cursor: pointer; }
.node:hover .box { fill: var(--color-background-tertiary, #ebebeb); }

/* 线条类 */
.arr { stroke: var(--color-text-secondary, #666); stroke-width: 1.5; fill: none; marker-end: url(#arrowhead); }
.leader { stroke: var(--color-border-tertiary, #ccc); stroke-width: 0.5; stroke-dasharray: 4 2; fill: none; }

/* 色板类 — 浅色默认值，暗色通过 .dark 祖先类覆盖 */
.c-purple { --node-bg: #EEEDFE; --node-border: #7F77DD; --node-text: #26215C; --node-text-sub: #534AB7; }
.c-teal   { --node-bg: #E1F5EE; --node-border: #1D9E75; --node-text: #04342C; --node-text-sub: #0F6E56; }
.c-coral  { --node-bg: #FAECE7; --node-border: #D85A30; --node-text: #4A1B0C; --node-text-sub: #9B3318; }
.c-pink   { --node-bg: #FBEAF0; --node-border: #D4537E; --node-text: #4B1528; --node-text-sub: #9C2D56; }
.c-gray   { --node-bg: #F1EFE8; --node-border: #888780; --node-text: #2C2C2A; --node-text-sub: #5C5C5A; }
.c-blue   { --node-bg: #E6F1FB; --node-border: #378ADD; --node-text: #042C53; --node-text-sub: #1B5C99; }
.c-green  { --node-bg: #EAF3DE; --node-border: #639922; --node-text: #173404; --node-text-sub: #3A6B10; }
.c-amber  { --node-bg: #FAEEDA; --node-border: #BA7517; --node-text: #412402; --node-text-sub: #7A4A10; }
.c-red    { --node-bg: #FCEBEB; --node-border: #E24B4A; --node-text: #501313; --node-text-sub: #9B2222; }

/* 暗色模式：覆盖色板变量（深背景 + 浅文字）
 * 双路触发：.dark 类（widget:theme 消息注入）+ prefers-color-scheme 媒体查询（不依赖消息时序）。
 * 媒体查询确保首次渲染时主题已就绪；.dark 类确保宿主显式切换主题时能覆盖系统偏好。 */
.dark .c-purple { --node-bg: #26215C; --node-border: #7F77DD; --node-text: #EEEDFE; --node-text-sub: #B8B4F5; }
.dark .c-teal   { --node-bg: #04342C; --node-border: #1D9E75; --node-text: #E1F5EE; --node-text-sub: #7DDDC0; }
.dark .c-coral  { --node-bg: #4A1B0C; --node-border: #D85A30; --node-text: #FAECE7; --node-text-sub: #F0A080; }
.dark .c-pink   { --node-bg: #4B1528; --node-border: #D4537E; --node-text: #FBEAF0; --node-text-sub: #F0A0C0; }
.dark .c-gray   { --node-bg: #2C2C2A; --node-border: #888780; --node-text: #F1EFE8; --node-text-sub: #B8B6B0; }
.dark .c-blue   { --node-bg: #042C53; --node-border: #378ADD; --node-text: #E6F1FB; --node-text-sub: #90C4F0; }
.dark .c-green  { --node-bg: #173404; --node-border: #639922; --node-text: #EAF3DE; --node-text-sub: #A0D060; }
.dark .c-amber  { --node-bg: #412402; --node-border: #BA7517; --node-text: #FAEEDA; --node-text-sub: #E0A860; }
.dark .c-red    { --node-bg: #501313; --node-border: #E24B4A; --node-text: #FCEBEB; --node-text-sub: #F09090; }

@media (prefers-color-scheme: dark) {
    .c-purple { --node-bg: #26215C; --node-border: #7F77DD; --node-text: #EEEDFE; --node-text-sub: #B8B4F5; }
    .c-teal   { --node-bg: #04342C; --node-border: #1D9E75; --node-text: #E1F5EE; --node-text-sub: #7DDDC0; }
    .c-coral  { --node-bg: #4A1B0C; --node-border: #D85A30; --node-text: #FAECE7; --node-text-sub: #F0A080; }
    .c-pink   { --node-bg: #4B1528; --node-border: #D4537E; --node-text: #FBEAF0; --node-text-sub: #F0A0C0; }
    .c-gray   { --node-bg: #2C2C2A; --node-border: #888780; --node-text: #F1EFE8; --node-text-sub: #B8B6B0; }
    .c-blue   { --node-bg: #042C53; --node-border: #378ADD; --node-text: #E6F1FB; --node-text-sub: #90C4F0; }
    .c-green  { --node-bg: #173404; --node-border: #639922; --node-text: #EAF3DE; --node-text-sub: #A0D060; }
    .c-amber  { --node-bg: #412402; --node-border: #BA7517; --node-text: #FAEEDA; --node-text-sub: #E0A860; }
    .c-red    { --node-bg: #501313; --node-border: #E24B4A; --node-text: #FCEBEB; --node-text-sub: #F09090; }
}

/* hex 兜底：模型常把浅色板的 hex 直接写进 fill 属性而绕过 .c-* 类，深色下类覆盖不生效。
 * 按属性选择器把浅色板 hex 在暗色下重映射为对应深色板；!important 兼防同元素内联 style。
 * 与类覆盖同构：.dark 类 + prefers-color-scheme 双路触发。 */
.dark rect[fill="#EEEDFE" i], .dark path[fill="#EEEDFE" i], .dark circle[fill="#EEEDFE" i], .dark ellipse[fill="#EEEDFE" i] { fill: #26215C !important; }
.dark rect[fill="#E1F5EE" i], .dark path[fill="#E1F5EE" i], .dark circle[fill="#E1F5EE" i], .dark ellipse[fill="#E1F5EE" i] { fill: #04342C !important; }
.dark rect[fill="#FAECE7" i], .dark path[fill="#FAECE7" i], .dark circle[fill="#FAECE7" i], .dark ellipse[fill="#FAECE7" i] { fill: #4A1B0C !important; }
.dark rect[fill="#FBEAF0" i], .dark path[fill="#FBEAF0" i], .dark circle[fill="#FBEAF0" i], .dark ellipse[fill="#FBEAF0" i] { fill: #4B1528 !important; }
.dark rect[fill="#F1EFE8" i], .dark path[fill="#F1EFE8" i], .dark circle[fill="#F1EFE8" i], .dark ellipse[fill="#F1EFE8" i] { fill: #2C2C2A !important; }
.dark rect[fill="#E6F1FB" i], .dark path[fill="#E6F1FB" i], .dark circle[fill="#E6F1FB" i], .dark ellipse[fill="#E6F1FB" i] { fill: #042C53 !important; }
.dark rect[fill="#EAF3DE" i], .dark path[fill="#EAF3DE" i], .dark circle[fill="#EAF3DE" i], .dark ellipse[fill="#EAF3DE" i] { fill: #173404 !important; }
.dark rect[fill="#FAEEDA" i], .dark path[fill="#FAEEDA" i], .dark circle[fill="#FAEEDA" i], .dark ellipse[fill="#FAEEDA" i] { fill: #412402 !important; }
.dark rect[fill="#FCEBEB" i], .dark path[fill="#FCEBEB" i], .dark circle[fill="#FCEBEB" i], .dark ellipse[fill="#FCEBEB" i] { fill: #501313 !important; }
.dark text[fill="#26215C" i] { fill: #EEEDFE !important; }
.dark text[fill="#04342C" i] { fill: #E1F5EE !important; }
.dark text[fill="#4A1B0C" i] { fill: #FAECE7 !important; }
.dark text[fill="#4B1528" i] { fill: #FBEAF0 !important; }
.dark text[fill="#2C2C2A" i] { fill: #F1EFE8 !important; }
.dark text[fill="#042C53" i] { fill: #E6F1FB !important; }
.dark text[fill="#173404" i] { fill: #EAF3DE !important; }
.dark text[fill="#412402" i] { fill: #FAEEDA !important; }
.dark text[fill="#501313" i] { fill: #FCEBEB !important; }
.dark text[fill="#534AB7" i] { fill: #B8B4F5 !important; }
.dark text[fill="#0F6E56" i] { fill: #7DDDC0 !important; }
.dark text[fill="#9B3318" i] { fill: #F0A080 !important; }
.dark text[fill="#9C2D56" i] { fill: #F0A0C0 !important; }
.dark text[fill="#5C5C5A" i] { fill: #B8B6B0 !important; }
.dark text[fill="#1B5C99" i] { fill: #90C4F0 !important; }
.dark text[fill="#3A6B10" i] { fill: #A0D060 !important; }
.dark text[fill="#7A4A10" i] { fill: #E0A860 !important; }
.dark text[fill="#9B2222" i] { fill: #F09090 !important; }

@media (prefers-color-scheme: dark) {
    rect[fill="#EEEDFE" i], path[fill="#EEEDFE" i], circle[fill="#EEEDFE" i], ellipse[fill="#EEEDFE" i] { fill: #26215C !important; }
    rect[fill="#E1F5EE" i], path[fill="#E1F5EE" i], circle[fill="#E1F5EE" i], ellipse[fill="#E1F5EE" i] { fill: #04342C !important; }
    rect[fill="#FAECE7" i], path[fill="#FAECE7" i], circle[fill="#FAECE7" i], ellipse[fill="#FAECE7" i] { fill: #4A1B0C !important; }
    rect[fill="#FBEAF0" i], path[fill="#FBEAF0" i], circle[fill="#FBEAF0" i], ellipse[fill="#FBEAF0" i] { fill: #4B1528 !important; }
    rect[fill="#F1EFE8" i], path[fill="#F1EFE8" i], circle[fill="#F1EFE8" i], ellipse[fill="#F1EFE8" i] { fill: #2C2C2A !important; }
    rect[fill="#E6F1FB" i], path[fill="#E6F1FB" i], circle[fill="#E6F1FB" i], ellipse[fill="#E6F1FB" i] { fill: #042C53 !important; }
    rect[fill="#EAF3DE" i], path[fill="#EAF3DE" i], circle[fill="#EAF3DE" i], ellipse[fill="#EAF3DE" i] { fill: #173404 !important; }
    rect[fill="#FAEEDA" i], path[fill="#FAEEDA" i], circle[fill="#FAEEDA" i], ellipse[fill="#FAEEDA" i] { fill: #412402 !important; }
    rect[fill="#FCEBEB" i], path[fill="#FCEBEB" i], circle[fill="#FCEBEB" i], ellipse[fill="#FCEBEB" i] { fill: #501313 !important; }
    text[fill="#26215C" i] { fill: #EEEDFE !important; }
    text[fill="#04342C" i] { fill: #E1F5EE !important; }
    text[fill="#4A1B0C" i] { fill: #FAECE7 !important; }
    text[fill="#4B1528" i] { fill: #FBEAF0 !important; }
    text[fill="#2C2C2A" i] { fill: #F1EFE8 !important; }
    text[fill="#042C53" i] { fill: #E6F1FB !important; }
    text[fill="#173404" i] { fill: #EAF3DE !important; }
    text[fill="#412402" i] { fill: #FAEEDA !important; }
    text[fill="#501313" i] { fill: #FCEBEB !important; }
    text[fill="#534AB7" i] { fill: #B8B4F5 !important; }
    text[fill="#0F6E56" i] { fill: #7DDDC0 !important; }
    text[fill="#9B3318" i] { fill: #F0A080 !important; }
    text[fill="#9C2D56" i] { fill: #F0A0C0 !important; }
    text[fill="#5C5C5A" i] { fill: #B8B6B0 !important; }
    text[fill="#1B5C99" i] { fill: #90C4F0 !important; }
    text[fill="#3A6B10" i] { fill: #A0D060 !important; }
    text[fill="#7A4A10" i] { fill: #E0A860 !important; }
    text[fill="#9B2222" i] { fill: #F09090 !important; }
}

/* 语义类（.box / .t / .th / .ts）消费色板变量 */
.c-purple .box, .c-teal .box, .c-coral .box, .c-pink .box, .c-gray .box,
.c-blue .box, .c-green .box, .c-amber .box, .c-red .box {
    fill: var(--node-bg); stroke: var(--node-border);
}
.c-purple .t, .c-teal .t, .c-coral .t, .c-pink .t, .c-gray .t,
.c-blue .t, .c-green .t, .c-amber .t, .c-red .t,
.c-purple .th, .c-teal .th, .c-coral .th, .c-pink .th, .c-gray .th,
.c-blue .th, .c-green .th, .c-amber .th, .c-red .th {
    fill: var(--node-text);
}
.c-purple .ts, .c-teal .ts, .c-coral .ts, .c-pink .ts, .c-gray .ts,
.c-blue .ts, .c-green .ts, .c-amber .ts, .c-red .ts {
    fill: var(--node-text-sub);
}

/* 兜底：模型直接输出 SVG 原生元素（无语义类）时自动应用色板变量。
 * 仅覆盖封闭形状（rect/circle/ellipse）；path 可能是箭头/线条，不统一覆盖。
 * SVG presentation attribute（fill="..."）优先级低于 CSS，无需 !important。 */
.c-purple rect, .c-teal rect, .c-coral rect, .c-pink rect, .c-gray rect,
.c-blue rect, .c-green rect, .c-amber rect, .c-red rect,
.c-purple circle, .c-teal circle, .c-coral circle, .c-pink circle, .c-gray circle,
.c-blue circle, .c-green circle, .c-amber circle, .c-red circle,
.c-purple ellipse, .c-teal ellipse, .c-coral ellipse, .c-pink ellipse, .c-gray ellipse,
.c-blue ellipse, .c-green ellipse, .c-amber ellipse, .c-red ellipse {
    fill: var(--node-bg); stroke: var(--node-border);
}
/* text 的 fill 属性同为 presentation attribute，CSS 可直接覆盖，无需 !important */
.c-purple text, .c-teal text, .c-coral text, .c-pink text, .c-gray text,
.c-blue text, .c-green text, .c-amber text, .c-red text {
    fill: var(--node-text);
}
`;

const DEFAULT_CSS_VARIABLES = `
:root {
    /* 背景色 */
    --color-background-primary: #ffffff;
    --color-background-secondary: #f5f5f5;
    --color-background-tertiary: #ebebeb;

    /* 文本色 */
    --color-text-primary: #1a1a1a;
    --color-text-secondary: #666666;
    --color-text-tertiary: #999999;

    /* 边框色 */
    --color-border-primary: rgba(0, 0, 0, 0.4);
    --color-border-secondary: rgba(0, 0, 0, 0.3);
    --color-border-tertiary: rgba(0, 0, 0, 0.15);

    /* 字体 */
    --font-sans: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --font-serif: Georgia, 'Times New Roman', serif;
    --font-mono: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace;

    /* 圆角 */
    --border-radius-sm: 4px;
    --border-radius-md: 8px;
    --border-radius-lg: 12px;
    --border-radius-xl: 16px;
}

.dark {
    --color-background-primary: #1a1a1a;
    --color-background-secondary: #2a2a2a;
    --color-background-tertiary: #333333;

    --color-text-primary: #e0e0e0;
    --color-text-secondary: #a0a0a0;
    --color-text-tertiary: #707070;

    --color-border-primary: rgba(255, 255, 255, 0.4);
    --color-border-secondary: rgba(255, 255, 255, 0.3);
    --color-border-tertiary: rgba(255, 255, 255, 0.15);
}
`;

export const DANGEROUS_TAGS = [
	"iframe",
	"object",
	"embed",
	"meta",
	"link",
	"base",
	"form",
];

/**
 * 构建 iframe 沙箱 HTML：CSP 白名单、内置预置类与变量、错误上报、
 * sendPrompt/openLink 桥、ResizeObserver 高度上报、morph 增量渲染、截图协议。
 */
export function buildSandboxHtml(options?: { nonce?: string }): string {
	const nonce = options?.nonce?.trim();
	const safeNonce = nonce ? nonce.replace(/["'<>]/g, "") : "";
	const scriptSource = safeNonce ? `'nonce-${safeNonce}'` : "'unsafe-inline'";
	const scriptNonceAttr = safeNonce ? ` nonce="${safeNonce}"` : "";
	return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${[
		"default-src 'none'",
		`script-src ${scriptSource} 'unsafe-eval' blob: ${CDN_WHITELIST.map((d) => `https://${d}`).join(" ")}`,
		"style-src 'unsafe-inline'",
		`img-src data: blob: ${CDN_WHITELIST.map((d) => `https://${d}`).join(" ")}`,
		`font-src ${CDN_WHITELIST.map((d) => `https://${d}`).join(" ")}`,
		`connect-src ${CDN_WHITELIST.map((d) => `https://${d}`).join(" ")}`
	].join("; ")}">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 100%; height: auto; overflow: visible; }
/* iframe viewport 兜底：宿主 Math.ceil(reportHeight) 可能露出底部 0~1px，<html> 背景避免 dark 下白条。
   优先使用宿主对话面板同源 --cc-panel-bg；缺失时回退 sandbox 内置 --color-background-primary。 */
html {
    background-color: var(--cc-panel-bg, var(--color-background-primary, transparent));
}
/* 融入 chat UI 的滚动条样式：细、半透明、hover 时加深。
   注意：iframe srcDoc 是独立文档，宿主的 CSS variables 不会继承进来，
   所以这里使用中性的半透明灰，保证明/暗主题下都协调。 */
html, body {
    scrollbar-width: thin;
    scrollbar-color: rgba(128, 128, 128, 0.3) transparent;
}
/* 永远预留滚动条槽位：滚动条出现与否不改变内容宽度，
   否则「滚动条占宽→重排变矮→上报更小→iframe 更矮→滚动条常驻」形成双稳态死循环 */
html { scrollbar-gutter: stable; }
html::-webkit-scrollbar,
body::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}
html::-webkit-scrollbar-track,
body::-webkit-scrollbar-track {
    background: transparent;
}
html::-webkit-scrollbar-thumb,
body::-webkit-scrollbar-thumb {
    background: rgba(128, 128, 128, 0.3);
    border-radius: 3px;
}
html::-webkit-scrollbar-thumb:hover,
body::-webkit-scrollbar-thumb:hover {
    background: rgba(128, 128, 128, 0.5);
}
html::-webkit-scrollbar-corner,
body::-webkit-scrollbar-corner {
    background: transparent;
}
body {
    font-family: var(--font-sans, system-ui, -apple-system, sans-serif);
    background: transparent;
    color: var(--color-text-primary, #1a1a1a);
    line-height: 1.5;
}
#root {
    width: 100%;
    min-height: 20px;
    /* 融入对话流：不再在 iframe 内部出现独立纵向滚动条。
       高度由外层通过 widget:resize 推断；绝对上限由宿主侧 clamp 保护。 */
    overflow: visible;
    overflow-x: hidden;
    /* widget_code 自管边距（规范已约束"无顶部 padding"），宿主不再塞 12px */
    padding: 0;
    /* 纯 SVG/透明 widget 未自绘背景时，由 #root 提供与对话面板一致的底色；
       widget 自带背景层按 CSS 层级覆盖此兜底。 */
    background-color: var(--cc-panel-bg, var(--color-background-primary, transparent));
}
/* 确保 script 标签在流式阶段不被渲染为可见文本 */
script { display: none !important; }
/* SVG 箭头标记 */
svg defs marker#arrowhead { fill: var(--color-text-secondary, #666); }
${PRESET_CSS_CLASSES}
${DEFAULT_CSS_VARIABLES}
</style>
</head>
<body>
<div id="root"></div>
<script${scriptNonceAttr}>${`
var s='${`(function() {
    'use strict';
    var root = document.getElementById('root');
    var pendingResize = false;
    var lastHeight = 0;
    var currentPhase = 'bootstrap';
    var MIN_WIDGET_HEIGHT = 20;
    var MAX_WIDGET_HEIGHT = 2000;
    var CANVAS_FALLBACK_HEIGHT_PX = ${CANVAS_FALLBACK_HEIGHT_PX};
    var CANVAS_MIN_HEIGHT_PX = ${CANVAS_MIN_HEIGHT_PX};
    var CAPTURE_MAX_HEIGHT = 16384;
    var INLINE_PX_HEIGHT_RE = ${INLINE_PX_HEIGHT_RE};
    var trustedTypesPolicy = null;

    function initTrustedTypesPolicy() {
        if (trustedTypesPolicy || typeof window === 'undefined' || !window.trustedTypes || typeof window.trustedTypes.createPolicy !== 'function') {
            return;
        }
        try {
            trustedTypesPolicy = window.trustedTypes.createPolicy('default', {
                createHTML: function(value) { return value; },
                createScript: function(value) { return value; },
                createScriptURL: function(value) { return value; }
            });
        } catch (_error) {
            trustedTypesPolicy = null;
        }
    }

    function toTrustedScript(code) {
        initTrustedTypesPolicy();
        if (trustedTypesPolicy && typeof trustedTypesPolicy.createScript === 'function') {
            try {
                return trustedTypesPolicy.createScript(code);
            } catch (_error) {
                return code;
            }
        }
        return code;
    }

    function toTrustedHTML(html) {
        initTrustedTypesPolicy();
        if (trustedTypesPolicy && typeof trustedTypesPolicy.createHTML === 'function') {
            try {
                return trustedTypesPolicy.createHTML(html);
            } catch (_error) {
                return html;
            }
        }
        return html;
    }

    function getRuntimeContext() {
        return {
            href: location.href,
            userAgent: navigator.userAgent,
            readyState: document.readyState,
            visibilityState: document.visibilityState,
            phase: currentPhase,
            timestamp: new Date().toISOString()
        };
    }

    function stringifyUnknown(value) {
        if (value == null) {
            return '';
        }
        if (typeof value === 'string') {
            return value;
        }
        if (typeof value === 'number' || typeof value === 'boolean') {
            return String(value);
        }
        try {
            return JSON.stringify(value);
        } catch (_unusedError) {
            return Object.prototype.toString.call(value);
        }
    }

    function reportError(phase, message, detail, meta) {
        var payload = {
            type: 'widget:error',
            phase: phase || currentPhase,
            message: message || 'Unknown error',
            detail: detail || '',
            stack: meta && meta.stack ? String(meta.stack) : '',
            source: meta && meta.source ? String(meta.source) : '',
            line: meta && typeof meta.line === 'number' ? meta.line : undefined,
            column: meta && typeof meta.column === 'number' ? meta.column : undefined,
            runtime: getRuntimeContext(),
            extra: meta && meta.extra ? meta.extra : undefined
        };
        parent.postMessage(payload, '*');
    }

    // 全局错误捕获
    window.onerror = function(message, source, lineno, colno, error) {
        var fallbackDetail = source ? (source + ':' + lineno + ':' + colno) : '';
        var stack = error && error.stack ? String(error.stack) : '';
        var detail = stack || fallbackDetail;
        reportError(currentPhase, String(message || 'window.onerror'), detail, {
            stack: stack,
            source: source,
            line: typeof lineno === 'number' ? lineno : undefined,
            column: typeof colno === 'number' ? colno : undefined,
            extra: {
                errorName: error && error.name ? error.name : ''
            }
        });
        return true;
    };

    // Promise 未捕获错误
    window.onunhandledrejection = function(event) {
        var reason = event && event.reason;
        var isError = reason instanceof Error;
        var message = isError ? reason.message : stringifyUnknown(reason);
        var stack = isError && reason.stack ? String(reason.stack) : '';
        var detail = stack || stringifyUnknown(reason);

        reportError(currentPhase, 'Unhandled Promise rejection: ' + (message || 'unknown'), detail, {
            stack: stack,
            extra: {
                reasonType: Object.prototype.toString.call(reason)
            }
        });
    };

    // 全局函数：发送消息到对话
    window.sendPrompt = function(text) {
        if (typeof text !== 'string') text = String(text || '');
        parent.postMessage({
            type: 'widget:sendMessage',
            text: text.slice(0, 500)
        }, '*');
    };

    // 全局函数：打开外部链接
    window.openLink = function(url) {
        if (typeof url !== 'string') return;
        if (/^(javascript|data):/i.test(url)) return;
        parent.postMessage({ type: 'widget:link', href: url }, '*');
    };

    // 拦截链接点击
    document.addEventListener('click', function(e) {
        var a = e.target.closest ? e.target.closest('a[href]') : null;
        if (a) {
            e.preventDefault();
            e.stopPropagation();
            openLink(a.href);
        }
    }, true);

    // 高度自适应（ResizeObserver + rAF 节流）
    // 上报加 2px 余量：内容高与 iframe 高只差 1~2px 时滚动条会「出现->占宽->更高->常驻」振荡，
    // 且宿主防抖守卫会拒绝连续微增，导致 iframe 永久卡短几像素、滚动条常驻。
    function reportHeight() {
        if (!root) {
            return;
        }
        var h = Math.ceil(root.getBoundingClientRect().height) + 2;
        h = Math.min(Math.max(h, MIN_WIDGET_HEIGHT), MAX_WIDGET_HEIGHT);
        if (h !== lastHeight && h > 0) {
            lastHeight = h;
            parent.postMessage({ type: 'widget:resize', height: h }, '*');
        }
    }

    var ro = new ResizeObserver(function() {
        if (pendingResize) return;
        pendingResize = true;
        requestAnimationFrame(function() {
            pendingResize = false;
            reportHeight();
        });
    });
    if (root) {
        ro.observe(root);
    }

    // 判定一个 <script> 是否为「可执行 JS」。遵循浏览器原生规则：type 为空 / javascript /
    // module 系才会被执行；application/json、text/template 等是数据块，浏览器不执行、保留在
    // DOM 供页面脚本读取（如图表库运行时靠 querySelector 读取同级数据脚本里的配置）。
    // 若把数据脚本也 eval，既会因非 JS 语法报错，也会因脚本被移除导致库读不到配置。
    function isExecutableScript(script) {
        var type = (script.getAttribute('type') || '').trim().toLowerCase();
        if (!type) {
            return true;
        }
        return (
            type === 'text/javascript' ||
            type === 'application/javascript' ||
            type === 'application/ecmascript' ||
            type === 'text/ecmascript' ||
            type === 'module'
        );
    }

    function shouldStabilizeCanvas(scripts) {
        for (var i = 0; i < scripts.length; i++) {
            var script = scripts[i];
            var src = (script.getAttribute('src') || '').toLowerCase();
            var code = (script.textContent || '').toLowerCase();
            if (src.indexOf('chart') >= 0 || src.indexOf('chart.js') >= 0 || code.indexOf('new chart(') >= 0) {
                return true;
            }
        }
        return false;
    }

    /**
     * 高度来源优先级：父容器 inline px -> canvas height 属性 -> 实测高度 -> 兜底高度，
     * 最终 clamp 到 [CANVAS_MIN_HEIGHT_PX, MAX_WIDGET_HEIGHT]。
     */
    function stabilizeCanvasContainers(scope, enable) {
        if (!enable) {
            return;
        }

        var canvases = scope.querySelectorAll('canvas');
        for (var i = 0; i < canvases.length; i++) {
            var canvas = canvases[i];
            if (!canvas || canvas.getAttribute('data-widget-canvas-stable') === '1') {
                continue;
            }

            var parent = canvas.parentElement;
            if (!parent) {
                continue;
            }

            var parentInlineHeight = NaN;
            if (parent.style && typeof parent.style.height === 'string') {
                var parentHeightStr = parent.style.height.trim();
                var parentMatch = INLINE_PX_HEIGHT_RE.exec(parentHeightStr);
                if (parentMatch) {
                    parentInlineHeight = parseFloat(parentMatch[1]);
                }
            }
            var heightAttr = parseInt(canvas.getAttribute('height') || '', 10);
            var measured = Math.ceil(canvas.getBoundingClientRect ? canvas.getBoundingClientRect().height : 0);
            var stableHeight = Number.isFinite(parentInlineHeight) && parentInlineHeight > 0 ? parentInlineHeight :
                Number.isFinite(heightAttr) && heightAttr > 0 ? heightAttr :
                measured > 0 ? measured :
                CANVAS_FALLBACK_HEIGHT_PX;
            stableHeight = Math.min(Math.max(stableHeight, CANVAS_MIN_HEIGHT_PX), MAX_WIDGET_HEIGHT);

            var wrapper = document.createElement('div');
            wrapper.className = 'widget-canvas-shell';
            wrapper.style.position = 'relative';
            wrapper.style.width = '100%';
            wrapper.style.height = stableHeight + 'px';
            wrapper.style.minHeight = stableHeight + 'px';
            wrapper.style.maxHeight = stableHeight + 'px';
            wrapper.style.overflow = 'hidden';

            parent.insertBefore(wrapper, canvas);
            wrapper.appendChild(canvas);

            canvas.style.width = '100%';
            canvas.style.height = '100%';
            canvas.style.display = 'block';
            canvas.setAttribute('data-widget-canvas-stable', '1');
        }
    }

    function bindInlineHandlers(scope) {
        var elements = scope.querySelectorAll('*');
        for (var i = 0; i < elements.length; i++) {
            var element = elements[i];
            var attrs = element.getAttributeNames ? element.getAttributeNames() : [];
            for (var j = 0; j < attrs.length; j++) {
                var attr = attrs[j];
                if (!/^on/i.test(attr)) continue;
                var eventName = attr.slice(2).toLowerCase();
                var code = element.getAttribute(attr);
                if (!eventName || !code) continue;

                element.removeAttribute(attr);
                element.addEventListener(eventName, function(event) {
                    var target = event.currentTarget;
                    var handlerCode = target && target.__widgetInlineHandlerCode && target.__widgetInlineHandlerCode[event.type];
                    if (!handlerCode) return;
                    try {
                        var handler = new Function('event', toTrustedScript(handlerCode));
                        handler.call(target, event);
                    } catch (error) {
                        console.warn('[WidgetSandbox] inline handler execution failed.', error);
                        reportError('script', 'Inline handler failed: ' + error.message, error.stack || error.message, {
                            stack: error && error.stack ? error.stack : '',
                            extra: {
                                eventType: event && event.type ? event.type : ''
                            }
                        });
                    }
                });

                if (!element.__widgetInlineHandlerCode) {
                    element.__widgetInlineHandlerCode = {};
                }
                element.__widgetInlineHandlerCode[eventName] = code;
            }
        }
    }

    async function executeScriptsSequentially(scripts) {
        currentPhase = 'script';
        for (var i = 0; i < scripts.length; i++) {
            var script = scripts[i];
            var src = script.getAttribute('src');
            var code = script.textContent || '';

            try {
                if (src) {
                    var response = await fetch(src);
                    if (!response.ok) throw new Error('fetch failed: ' + response.status);
                    var externalCode = await response.text();
                    (0, eval)(toTrustedScript(externalCode));
                } else if (code.trim()) {
                    (0, eval)(toTrustedScript(code));
                }
            } catch (error) {
                console.warn('[WidgetSandbox] script execution failed.', { src: src, error: error });
                reportError('script', 'Script execution failed' + (src ? ' (' + src + ')' : ''), error.stack || error.message, {
                    stack: error && error.stack ? error.stack : '',
                    source: src || '',
                    extra: {
                        scriptType: src ? 'external' : 'inline'
                    }
                });
            }
        }
    }

    // 同步元素属性：仅写入有变化的属性、移除新内容里不存在的属性，
    // 避免无谓的 setAttribute 触发重排。
    function morphElementAttributes(fromEl, toEl) {
        var toAttrs = toEl.attributes;
        for (var i = 0; i < toAttrs.length; i++) {
            var attr = toAttrs[i];
            if (fromEl.getAttribute(attr.name) !== attr.value) {
                fromEl.setAttribute(attr.name, attr.value);
            }
        }
        var fromAttrs = fromEl.attributes;
        for (var j = fromAttrs.length - 1; j >= 0; j--) {
            var name = fromAttrs[j].name;
            if (!toEl.hasAttribute(name)) {
                fromEl.removeAttribute(name);
            }
        }
    }

    // 增量同步单个节点：类型/标签相同则原地更新，不同则整体替换。
    function morphNode(fromNode, toNode) {
        if (fromNode.nodeType !== toNode.nodeType || fromNode.nodeName !== toNode.nodeName) {
            fromNode.parentNode.replaceChild(toNode, fromNode);
            return;
        }
        // 文本 / 注释：内容变化时才改 nodeValue，不重建节点
        if (fromNode.nodeType === 3 || fromNode.nodeType === 8) {
            if (fromNode.nodeValue !== toNode.nodeValue) {
                fromNode.nodeValue = toNode.nodeValue;
            }
            return;
        }
        if (fromNode.nodeType !== 1) {
            return;
        }
        // 保留已存在的 canvas 节点（含已绘制内容与稳定高度），避免清空画布导致闪烁/图表丢失
        if (fromNode.nodeName === 'CANVAS') {
            return;
        }
        morphElementAttributes(fromNode, toNode);
        morphChildren(fromNode, toNode);
    }

    // 增量同步子节点列表：按位置逐个 morph，保留未变节点，仅删除/新增差异节点。
    // 取代「整体清空再重建」，消除 finalize/update 切换时的清空闪白。
    function morphChildren(fromEl, toEl) {
        var toNodes = Array.prototype.slice.call(toEl.childNodes);
        for (var i = 0; i < toNodes.length; i++) {
            var toNode = toNodes[i];
            var fromNode = fromEl.childNodes[i];
            if (!fromNode) {
                fromEl.appendChild(toNode);
            } else {
                morphNode(fromNode, toNode);
            }
        }
        while (fromEl.childNodes.length > toNodes.length) {
            fromEl.removeChild(fromEl.lastChild);
        }
    }

    // 渲染前校验：判断 temp（已解析的离屏内容）是否为「可正常渲染的有效 HTML」。
    // 流式中间帧偶发：上游内容尚未解码（实体转义残留如 &lt;div&gt;，或换行/制表符的字面量转义残片），
    // 此时 innerHTML 会把整段当作纯文本，morph 进画面后出现乱码、随后被正确帧纠正，造成闪烁。
    // 返回 false 时调用方跳过本帧、保留上一帧已渲染内容。
    // 注意：本函数被嵌入模板字符串注入到 iframe，正则中的反斜杠需在源码里双写以保留到注入脚本。
    function looksRenderable(sourceHtml, parsed) {
        var src = sourceHtml || '';
        if (!src) {
            return false;
        }

        // (A) 未解码 JSON 转义残留检测 —— 必须在 hasElement 判断之前。
        // 上游 JSON 字符串尚未解码时，HTML 里会残留「反斜杠+引号」(属性引号未解码)
        // 或「反斜杠+ n/t/r」(字面换行、制表)。正常 HTML 用裸引号和真实空白，绝不出现这些组合。
        // 这类脏帧仍能解析出真实元素节点，若直接 morph 进画面，残留字符会被当作可见文本画出来，
        // 下一帧解码后再纠正 -> 表现为闪烁。出现 >=2 处即判定整帧不可渲染，保留上一帧。
        // 用 charCode 遍历而非正则：本函数注入到 iframe 模板字符串，正则里的反斜杠转义极易写错。
        var BACKSLASH_CODE = 92;
        var undecodedHits = 0;
        for (var ci = 0; ci < src.length - 1; ci++) {
            if (src.charCodeAt(ci) === BACKSLASH_CODE) {
                var nextChar = src.charAt(ci + 1);
                if (nextChar === '"' || nextChar === 'n' || nextChar === 't' || nextChar === 'r') {
                    undecodedHits++;
                    if (undecodedHits >= 2) {
                        return false;
                    }
                }
            }
        }

        // (B) 已解析出真实元素结构 -> 视为可渲染（不误伤含代码块的合法内容）
        if (parsed.querySelector('*') !== null) {
            return true;
        }

        // (C) 无任何元素，但源串写了标签语法（< 紧跟字母 / 感叹号 / 斜杠）-> 实体转义未解码，整段退化为纯文本
        if (/<[a-zA-Z!/]/.test(src)) {
            return false;
        }

        return true;
    }

    function renderHtml(html, executeScripts, phase) {
        currentPhase = phase || 'update';
        if (!root) {
            reportError(phase, 'Render failed: root element not found', '', {
                source: 'sandbox',
                extra: {
                    htmlLength: html ? html.length : 0,
                    executeScripts: !!executeScripts
                }
            });
            return;
        }
        try {
            var temp = document.createElement('div');
            temp.innerHTML = toTrustedHTML(html || '');

            bindInlineHandlers(temp);

            var allScripts = Array.prototype.slice.call(temp.querySelectorAll('script'));
            // 只移除并执行可执行 JS 脚本；数据脚本（application/json 等）保留在 DOM，
            // 供 widget 库运行时读取。
            var scripts = allScripts.filter(isExecutableScript);
            var useCanvasStabilizer = shouldStabilizeCanvas(scripts);
            stabilizeCanvasContainers(temp, useCanvasStabilizer);
            scripts.forEach(function(script) {
                if (script.parentNode) {
                    script.parentNode.removeChild(script);
                }
            });

            // 渲染前校验（仅流式 update 帧）：内容未正确解析为 HTML（转义残留 / 字面量转义残片）时
            // 跳过本帧、保留上一帧已渲染内容，避免「乱码 -> 纠正」造成的闪烁。
            // finalize（executeScripts=true）为最终确定内容，不跳过。
            if (!executeScripts && !looksRenderable(html, temp)) {
                return;
            }

            // 增量同步：原地 patch 差异节点、保留未变节点（尤其 canvas），
            // 取代整体清空重建，消除流式最后一帧 -> finalize 的清空闪白。
            // morph 出现异常时兜底退回全量重建，保证内容正确。
            try {
                morphChildren(root, temp);
            } catch (morphError) {
                console.warn('[WidgetSandbox] morph failed, fallback to full rebuild.', morphError);
                while (root.firstChild) {
                    root.removeChild(root.firstChild);
                }
                while (temp.firstChild) {
                    root.appendChild(temp.firstChild);
                }
            }

            if (!executeScripts) {
                setTimeout(reportHeight, 10);
                return;
            }

            executeScriptsSequentially(scripts).finally(function() {
                setTimeout(reportHeight, 50);
                // 字体加载/异步绘图可能让布局在 finalize 后才稳定，补一次延迟上报
                setTimeout(reportHeight, 350);
            });
        } catch (error) {
            console.warn('[WidgetSandbox] render failed.', error);
            reportError(phase, 'Render failed: ' + error.message, error.stack || error.message, {
                stack: error && error.stack ? error.stack : '',
                extra: {
                    htmlLength: html ? html.length : 0,
                    executeScripts: !!executeScripts
                }
            });
        }
    }

    // 消息监听
    window.addEventListener('message', function(e) {
        var data = e.data;
        if (!data || typeof data.type !== 'string') return;

        switch (data.type) {
            case 'widget:host-ready':
                // Host 可能在消息监听挂载后才就绪，允许重试握手避免丢失初次 ready
                parent.postMessage({ type: 'widget:ready' }, '*');
                reportHeight();
                break;

            case 'widget:update':
                renderHtml(data.html || '', false, 'update');
                break;

            case 'widget:finalize':
                renderHtml(data.html || '', true, 'finalize');
                break;

            case 'widget:theme':
                // 清理上次注入的 widget-theme vars，避免主题切换后旧值在 documentElement.style 残留
                // （host 推送的 vars 列表可能因切换前后宿主样式表差异而不同）。仅清理本 handler 注入过的
                // 键，不影响 sandbox srcDoc 自身在 :root 定义的默认调色板。
                var prevInjected = window.__widgetInjectedThemeVars || [];
                for (var pi = 0; pi < prevInjected.length; pi++) {
                    document.documentElement.style.removeProperty(prevInjected[pi]);
                }

                var vars = data.vars || {};
                var style = document.documentElement.style;
                var nextInjected = [];
                for (var k in vars) {
                    if (vars.hasOwnProperty(k)) {
                        style.setProperty(k, vars[k]);
                        nextInjected.push(k);
                    }
                }
                window.__widgetInjectedThemeVars = nextInjected;

                if (data.isDark) {
                    document.documentElement.classList.add('dark');
                } else {
                    document.documentElement.classList.remove('dark');
                }
                break;

            case 'widget:capture':
                // 截图协议：将当前 DOM 渲染为 PNG 图片并回传给宿主
                captureWidgetAsImage(data.requestId || '');
                break;
        }
    });

    // 截图结果回传 helper
    function sendCaptureResult(requestId, dataUrl, width, height) {
        parent.postMessage({ type: 'widget:capture-result', requestId: requestId, dataUrl: dataUrl, width: width, height: height }, '*');
    }
    function sendCaptureError(requestId, error) {
        parent.postMessage({ type: 'widget:capture-result', requestId: requestId, error: error }, '*');
    }

    /**
     * 将 widget DOM 捕获为 PNG 图片。
     * 统一使用 foreignObject + SVG -> Canvas 方案，canvas 元素内容会被内联为 img 保留。
     */
    function captureWidgetAsImage(requestId) {
        if (!root) {
            sendCaptureError(requestId, 'No root element');
            return;
        }

        try {
            var rect = root.getBoundingClientRect();
            var width = Math.max(Math.ceil(rect.width), 800);
            var height = Math.ceil(rect.height);

            if (height <= 0) {
                sendCaptureError(requestId, 'Content has zero height');
                return;
            }

            if (height > CAPTURE_MAX_HEIGHT) {
                sendCaptureError(requestId, 'Content too tall to capture (' + height + 'px, limit ' + CAPTURE_MAX_HEIGHT + 'px)');
                return;
            }

            if (height > 8000) {
                parent.postMessage({ type: 'widget:capture-progress', requestId: requestId, message: 'large-content', height: height }, '*');
            }

            captureViaForeignObject(requestId, width, height);
        } catch (err) {
            sendCaptureError(requestId, 'Capture failed: ' + (err.message || err));
        }
    }

    /**
     * 混合合成截图方案：
     * 1. foreignObject 渲染 HTML/CSS 布局（canvas 替换为透明占位块）
     * 2. 渲染完成后，将原始 canvas 内容叠加绘制到对应位置
     * 这样既保留 HTML 标题/图例/文字，又保留 JS 图表的 canvas 绘制内容。
     */
    function captureViaForeignObject(requestId, width, height) {
        // 收集所有样式
        var cssText = '';
        for (var si = 0; si < document.styleSheets.length; si++) {
            try {
                var sheet = document.styleSheets[si];
                for (var ri = 0; ri < sheet.cssRules.length; ri++) {
                    cssText += sheet.cssRules[ri].cssText + '\\n';
                }
            } catch (_e) {
                // 跨域样式表无法读取规则，跳过
            }
        }

        var rootStyle = document.documentElement.getAttribute('style') || '';
        var darkClass = document.documentElement.classList.contains('dark') ? ' class="dark"' : '';

        // 克隆 DOM
        var clone = root.cloneNode(true);

        // 记录原始 canvas 的位置和内容，同时在克隆中替换为透明占位块
        var originalCanvases = root.querySelectorAll('canvas');
        var clonedCanvases = clone.querySelectorAll('canvas');
        var canvasOverlays = [];
        var rootRect = root.getBoundingClientRect();
        for (var ci = 0; ci < originalCanvases.length; ci++) {
            try {
                var originalCanvas = originalCanvases[ci];
                var clonedCanvas = clonedCanvases[ci];
                if (originalCanvas && clonedCanvas && clonedCanvas.parentNode) {
                    // 记录位置和 data URL 用于后续叠加
                    var cRect = originalCanvas.getBoundingClientRect();
                    canvasOverlays.push({
                        dataUrl: originalCanvas.toDataURL('image/png'),
                        x: cRect.left - rootRect.left,
                        y: cRect.top - rootRect.top,
                        w: cRect.width,
                        h: cRect.height
                    });

                    // 替换为同尺寸的透明占位 div（保持布局）
                    var placeholder = document.createElement('div');
                    placeholder.style.width = originalCanvas.offsetWidth + 'px';
                    placeholder.style.height = originalCanvas.offsetHeight + 'px';
                    placeholder.style.display = 'block';
                    clonedCanvas.parentNode.replaceChild(placeholder, clonedCanvas);
                }
            } catch (_canvasErr) {
                // canvas 可能被 tainted，跳过
            }
        }

        // 移除 script 标签
        var scripts = clone.querySelectorAll('script');
        for (var ssi = 0; ssi < scripts.length; ssi++) {
            scripts[ssi].parentNode.removeChild(scripts[ssi]);
        }

        var serializer = new XMLSerializer();
        var htmlContent = serializer.serializeToString(clone);

        // 构建 SVG foreignObject
        var svgNs = 'http://www.w3.org/2000/svg';
        var xhtmlNs = 'http://www.w3.org/1999/xhtml';
        var svgData = '<svg xmlns="' + svgNs + '" width="' + width + '" height="' + height + '">' +
            '<foreignObject width="100%" height="100%">' +
            '<html xmlns="' + xhtmlNs + '"' + darkClass + ' style="' + rootStyle.replace(/"/g, '&quot;') + '">' +
            '<head><style>' + cssText.split('</style>').join('</st' + 'yle>') + '</style></head>' +
            '<body style="margin:0;padding:0;overflow:hidden;">' + htmlContent + '</body>' +
            '</html>' +
            '</foreignObject>' +
            '</svg>';

        var img = new Image();
        img.onload = function() {
            try {
                var padding = 24;
                var canvas = document.createElement('canvas');
                var scale = height > 8000 ? 1 : 2;
                var totalWidth = width + padding * 2;
                var totalHeight = height + padding * 2;
                canvas.width = totalWidth * scale;
                canvas.height = totalHeight * scale;
                var ctx = canvas.getContext('2d');
                ctx.scale(scale, scale);

                // 背景色跟随主题
                var isDark = document.documentElement.classList.contains('dark');
                ctx.fillStyle = isDark ? '#1e1e1e' : '#ffffff';
                ctx.fillRect(0, 0, totalWidth, totalHeight);

                // 绘制 foreignObject 渲染的 HTML/CSS 内容（带 padding 偏移）
                ctx.drawImage(img, padding, padding, width, height);

                // 叠加绘制 canvas 元素内容到对应位置（加 padding 偏移）
                if (canvasOverlays.length > 0) {
                    var overlaysLoaded = 0;
                    var totalOverlays = canvasOverlays.length;

                    for (var oi = 0; oi < totalOverlays; oi++) {
                        (function(overlay) {
                            var overlayImg = new Image();
                            overlayImg.onload = function() {
                                ctx.drawImage(overlayImg, overlay.x + padding, overlay.y + padding, overlay.w, overlay.h);
                                overlaysLoaded++;
                                if (overlaysLoaded === totalOverlays) {
                                    sendCaptureResult(requestId, canvas.toDataURL('image/png'), totalWidth, totalHeight);
                                }
                            };
                            overlayImg.onerror = function() {
                                overlaysLoaded++;
                                if (overlaysLoaded === totalOverlays) {
                                    sendCaptureResult(requestId, canvas.toDataURL('image/png'), totalWidth, totalHeight);
                                }
                            };
                            overlayImg.src = overlay.dataUrl;
                        })(canvasOverlays[oi]);
                    }
                } else {
                    sendCaptureResult(requestId, canvas.toDataURL('image/png'), totalWidth, totalHeight);
                }
            } catch (canvasErr) {
                sendCaptureError(requestId, 'Canvas render failed: ' + (canvasErr.message || canvasErr));
            }
        };
        img.onerror = function() {
            // foreignObject 渲染失败时，尝试纯 canvas 合成作为降级
            var padding = 24;
            var canvases = root.querySelectorAll('canvas');
            if (canvases.length > 0) {
                try {
                    var totalW = width + padding * 2;
                    var totalH = height + padding * 2;
                    var fallbackCanvas = document.createElement('canvas');
                    var scale = height > 8000 ? 1 : 2;
                    fallbackCanvas.width = totalW * scale;
                    fallbackCanvas.height = totalH * scale;
                    var fCtx = fallbackCanvas.getContext('2d');
                    fCtx.scale(scale, scale);
                    var isDark = document.documentElement.classList.contains('dark');
                    fCtx.fillStyle = isDark ? '#1e1e1e' : '#ffffff';
                    fCtx.fillRect(0, 0, totalW, totalH);

                    for (var fi = 0; fi < canvases.length; fi++) {
                        var fc = canvases[fi];
                        var fcRect = fc.getBoundingClientRect();
                        fCtx.drawImage(fc, fcRect.left - rootRect.left + padding, fcRect.top - rootRect.top + padding, fcRect.width, fcRect.height);
                    }

                    sendCaptureResult(requestId, fallbackCanvas.toDataURL('image/png'), totalW, totalH);
                } catch (_fallbackErr) {
                    sendCaptureError(requestId, 'SVG and canvas fallback both failed');
                }
            } else {
                sendCaptureError(requestId, 'SVG image load failed');
            }
        };

        var svgBase64 = btoa(unescape(encodeURIComponent(svgData)));
        img.src = 'data:image/svg+xml;base64,' + svgBase64;
    }

    // 通知宿主就绪
    currentPhase = 'bootstrap';
    parent.postMessage({ type: 'widget:ready' }, '*');
})();`.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/\n/g, "\\n")}';
var b=new Blob([s],{type:'text/javascript'});
var u=URL.createObjectURL(b);
var el=document.createElement('script');
var ttPolicy=null;

function initTrustedTypesPolicy(){
    if(ttPolicy||!window.trustedTypes||typeof window.trustedTypes.createPolicy!=='function') return;
    try{
        ttPolicy=window.trustedTypes.createPolicy('default',{
            createHTML:function(value){return value;},
            createScript:function(value){return value;},
            createScriptURL:function(value){return value;}
        });
    }catch(_error){
        ttPolicy=null;
    }
}

function toTrustedScriptURL(value){
    initTrustedTypesPolicy();
    if(ttPolicy&&typeof ttPolicy.createScriptURL==='function'){
        try{return ttPolicy.createScriptURL(value);}catch(_error){return value;}
    }
    return value;
}

function toTrustedScript(value){
    initTrustedTypesPolicy();
    if(ttPolicy&&typeof ttPolicy.createScript==='function'){
        try{return ttPolicy.createScript(value);}catch(_error){return value;}
    }
    return value;
}

function reportBootstrapFailure(reason, error){
    parent.postMessage({
        type:'widget:error',
        phase:'bootstrap',
        message:'Script load failed: '+(error&&error.message?error.message:'unknown'),
        detail:(error&&error.stack)||String(reason)||'',
        stack:error&&error.stack?String(error.stack):'',
        source:'bootstrap-script',
        runtime:{
            href:location.href,
            userAgent:navigator.userAgent,
            readyState:document.readyState,
            visibilityState:document.visibilityState,
            phase:'bootstrap',
            timestamp:new Date().toISOString()
        },
        extra:{
            bootstrapReason:String(reason||'')
        }
    },'*');
}

function tryEvalFallback(reason){
    try{
        // CSP 已允许 unsafe-eval，eval 不受 Trusted Types 约束
        (0,eval)(s);
        URL.revokeObjectURL(u);
        return true;
    }catch(error){
        reportBootstrapFailure(reason,error);
        URL.revokeObjectURL(u);
        return false;
    }
}

function tryInlineFallback(reason){
    try{
        var inline=document.createElement('script');
        var current=document.currentScript;
        var nonce=current&&typeof current.getAttribute==='function'?current.getAttribute('nonce'):'';
        if(nonce){
            inline.setAttribute('nonce',nonce);
        }
        inline.text=toTrustedScript(s);
        document.head.appendChild(inline);
        URL.revokeObjectURL(u);
        return true;
    }catch(error){
        // script.text 赋值被 Trusted Types 拦截时，使用 eval fallback
        return tryEvalFallback(reason);
    }
}

try {
    // 某些宿主启用 Trusted Types 时，script.src 需要 TrustedScriptURL
    el.src=toTrustedScriptURL(u);
} catch(assignError){
    tryInlineFallback(assignError&&assignError.message?assignError.message:assignError);
}

el.onload=function(){URL.revokeObjectURL(u)};
el.onerror=function(){
    tryInlineFallback('script-element-load-error');
};

try {
    document.head.appendChild(el);
} catch(appendError){
    tryInlineFallback(appendError&&appendError.message?appendError.message:appendError);
}`}<\/script>
</body>
</html>`;
}

/**
 * 剥离末尾未闭合的 `<style>` 块。
 *
 * 流式阶段 `<style>` 若尚未写出 `</style>`，HTML 解析器会把后续 body 文本当作 CSS
 * 吞掉，渲染出空白。因此当 `<style>` 开标签数 > `</style>` 闭标签数时，
 * 定位最后一个未闭合的 `<style` 起点，切掉起点及其后的所有内容，等到闭合标签
 * 真正到达时再整体显示。
 *
 * 与 script 的策略差异：script 用 display:none 隐藏（见 sanitizeForStreaming
 * 步骤 3），因为 script 标签本身不影响后续 DOM 解析；style 必须剥离，因为未闭合
 * style 会吞掉后续字符。
 */
function stripUnclosedStyle(html: string): string {
	if (!html) return html;
	const openMatches = html.match(/<style\b[^>]*>/gi);
	const closeMatches = html.match(/<\/style\s*>/gi);
	if ((openMatches ? openMatches.length : 0) <= (closeMatches ? closeMatches.length : 0)) return html;
	const lastCloseRegex = /<\/style\s*>/gi;
	let lastCloseEnd = -1;
	let match;
	while ((match = lastCloseRegex.exec(html)) !== null) lastCloseEnd = match.index + match[0].length;
	const openIterRegex = /<style\b[^>]*>/gi;
	let lastUnclosedStart = -1;
	while ((match = openIterRegex.exec(html)) !== null) if (match.index >= lastCloseEnd) {
		lastUnclosedStart = match.index;
		break;
	}
	if (lastUnclosedStart < 0) return html;
	return html.slice(0, lastUnclosedStart);
}

/**
 * 流式阶段消毒：移除所有 script + on* 事件 + 危险标签 + 不安全 URL，
 * 并裁掉末尾未闭合标签残片与未配对的 `<style>` 段。
 */
export function sanitizeForStreaming(html: string): string {
	if (!html) return html;
	let result = html;
	for (const tag of DANGEROUS_TAGS) {
		result = result.replace(new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}>`, "gi"), "");
		result = result.replace(new RegExp(`<${tag}\\b[^>]*/?>`, "gi"), "");
	}
	result = result.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
	result = result.replace(/<script\b[^>]*\/>/gi, "");
	const unclosedScriptMatch = result.match(/<script\b[^>]*>[\s\S]*$/i);
	if (unclosedScriptMatch) {
		const idx = result.lastIndexOf(unclosedScriptMatch[0]);
		result = result.slice(0, idx) + "<span style=\"display:none!important\">" + unclosedScriptMatch[0] + "</span>";
	}
	result = result.replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
	result = result.replace(/\b(href|src|action)\s*=\s*["'](javascript|data):[^"']*/gi, (_unused, attr) => `${attr}=""`);
	const lastLt = result.lastIndexOf("<");
	if (lastLt > result.lastIndexOf(">")) result = result.slice(0, lastLt);
	result = stripUnclosedStyle(result);
	if (result.trim() === "") return "";
	return result;
}

/**
 * 完成阶段消毒：保留 script，仅移除危险标签 + 不安全 URL
 */
export function sanitizeForIframe(html: string): string {
	if (!html) return html;
	let result = html;
	for (const tag of DANGEROUS_TAGS) {
		result = result.replace(new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}>`, "gi"), "");
		result = result.replace(new RegExp(`<${tag}\\b[^>]*/?>`, "gi"), "");
	}
	result = result.replace(/\b(href|action)\s*=\s*["'](javascript|data):[^"']*/gi, (_unused, attr) => `${attr}=""`);
	result = result.replace(/\bsrc\s*=\s*["']javascript:[^"']*/gi, "src=\"\"");
	return result;
}
