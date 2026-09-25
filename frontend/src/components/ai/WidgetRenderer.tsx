/**
 * Widget 渲染宿主：iframe 沙箱 + postMessage 协议（ready/update/finalize/theme/resize/截图）。
 * 流式期间把 widget_code 消毒后以 widget:update 增量下发；结束后 finalize 执行脚本。
 * 主题变量与明暗态从宿主收集后推入沙箱；高度由 iframe 上报并做抖动守卫。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	buildSandboxHtml,
	sanitizeForIframe,
	sanitizeForStreaming,
} from '../../utils/widgetSandbox';
import {
	WIDGET_MAX_HEIGHT,
	markWidgetHeightAccepted,
	normalizeWidgetHeight,
	shouldApplyWidgetHeight,
	type WidgetHeightGuardState,
} from '../../utils/widgetResizeGuard';
import './widgetRenderer.css';

export interface WidgetHostConfig {
	onSendMessage?: (text: string) => void;
	onOpenLink?: (href: string) => void;
}

interface WidgetRendererProps {
	widgetCode: string;
	isStreaming: boolean;
	title?: string;
	toolName?: string;
	showOverlay?: boolean;
	loadingLabel?: string;
	hostConfig?: WidgetHostConfig;
	chromeless?: boolean;
	showActions?: boolean;
}

// 容器 className 拼装：chromeless 下由样式表隐藏外框装饰
function resolveWidgetContainerClassName(chromeless: boolean): string {
	return chromeless ? 'cc-widget-container cc-widget-chromeless' : 'cc-widget-container';
}

// chromeless 下一律隐藏传统顶栏
function shouldRenderWidgetHeader(chromeless: boolean): boolean {
	return !chromeless;
}

// chromeless 下不再由本组件渲染骨架屏 overlay（调用方负责 pre-first-byte 占位）
function shouldRenderLoadingOverlay(params: {
	chromeless: boolean;
	showOverlay?: boolean;
	showCode: boolean;
	useHtmlFallback: boolean;
	hasError: boolean;
}): boolean {
	if (params.chromeless) return false;
	return !!params.showOverlay && !params.showCode && !params.useHtmlFallback && !params.hasError;
}

// iframe 已挂载、widgetCode 已到但尚未 ready 时的单行占位（bootstrap 窗口，避免空白突变）
function shouldRenderBootstrapPending(params: {
	hasWidgetCode: boolean;
	isReady: boolean;
	useHtmlFallback: boolean;
	hasError: boolean;
	showCode: boolean;
}): boolean {
	return params.hasWidgetCode && !params.isReady && !params.useHtmlFallback && !params.hasError && !params.showCode;
}

function stringifyUnknown(value: unknown): string {
	if (value == null) return "";
	if (typeof value === "string") return value;
	if (typeof value === "number" || typeof value === "boolean") return String(value);
	try {
		return JSON.stringify(value, null, 2);
	} catch {
		return Object.prototype.toString.call(value);
	}
}

function buildErrorDetail(options: { detail?: unknown; stack?: unknown; iframeDiagnostics?: unknown; hostDiagnostics?: unknown }): string {
	const sections: string[] = [];
	const detail = stringifyUnknown(options.detail);
	if (detail) sections.push(detail);
	const stack = stringifyUnknown(options.stack);
	if (stack && stack !== detail) sections.push(`Stack:\n${stack}`);
	if (options.iframeDiagnostics) sections.push(`Iframe Diagnostics:\n${stringifyUnknown(options.iframeDiagnostics)}`);
	if (options.hostDiagnostics) sections.push(`Host Diagnostics:\n${stringifyUnknown(options.hostDiagnostics)}`);
	return sections.join("\n\n");
}

async function copyTextWithFallback(text: string): Promise<boolean> {
	try {
		if (navigator?.clipboard?.writeText) {
			await navigator.clipboard.writeText(text);
			return true;
		}
	} catch (error) {
		console.warn("[WidgetRenderer] clipboard.writeText failed, fallback to execCommand.", error);
	}
	try {
		const textarea = document.createElement("textarea");
		textarea.value = text;
		textarea.setAttribute("readonly", "true");
		textarea.style.position = "fixed";
		textarea.style.opacity = "0";
		textarea.style.left = "-9999px";
		textarea.style.top = "0";
		document.body.appendChild(textarea);
		textarea.focus();
		textarea.select();
		textarea.setSelectionRange(0, textarea.value.length);
		const copied = document.execCommand("copy");
		document.body.removeChild(textarea);
		return copied;
	} catch (error) {
		console.warn("[WidgetRenderer] execCommand copy fallback failed.", error);
		return false;
	}
}

// widget 主题契约：规范文档承诺的宿主变量全集（背景/文本/边框语义色、字体、圆角、面板底色）。
// 按名单精确收集——不能按前缀扫描，宿主经通用主题层注入了大量无关调色板变量。
const WIDGET_THEME_VARS = [
	"--cc-panel-bg",
	"--color-background-primary",
	"--color-background-secondary",
	"--color-background-tertiary",
	"--color-background-info",
	"--color-background-danger",
	"--color-background-success",
	"--color-background-warning",
	"--color-text-primary",
	"--color-text-secondary",
	"--color-text-tertiary",
	"--color-text-info",
	"--color-text-danger",
	"--color-text-success",
	"--color-text-warning",
	"--color-border-primary",
	"--color-border-secondary",
	"--color-border-tertiary",
	"--color-border-info",
	"--color-border-danger",
	"--color-border-success",
	"--color-border-warning",
	"--font-sans",
	"--font-serif",
	"--font-mono",
	"--border-radius-sm",
	"--border-radius-md",
	"--border-radius-lg",
	"--border-radius-xl",
];

/**
 * 收集宿主 CSS 变量：按名单取值，body 优先、:root 兜底。
 * 沙箱文档是独立文档，不会继承宿主变量，需显式推送。
 */
function collectCssVariables(): Record<string, string> {
	const vars: Record<string, string> = {};
	try {
		const docElStyle = getComputedStyle(document.documentElement);
		const bodyStyle = document.body ? getComputedStyle(document.body) : null;
		for (const prop of WIDGET_THEME_VARS) {
			const bodyValue = bodyStyle ? bodyStyle.getPropertyValue(prop).trim() : "";
			const htmlValue = docElStyle.getPropertyValue(prop).trim();
			const value = bodyValue || htmlValue;
			if (value) vars[prop] = value;
		}
	} catch {}
	return vars;
}

/** 检测暗色：优先宿主显式 .dark/data-theme 信号，matchMedia 兜底 */
function isDarkMode(): boolean {
	if (typeof document === "undefined") return false;
	const html = document.documentElement;
	if (html.classList.contains("dark") || html.classList.contains("vscode-dark") || html.getAttribute("data-theme") === "dark" || html.getAttribute("data-vscode-theme-kind") === "vscode-dark") return true;
	const vscodeThemeKind = html.getAttribute("data-vscode-theme-kind");
	if (html.classList.contains("light") || html.classList.contains("cb-light") || html.classList.contains("vscode-light") || html.getAttribute("data-theme") === "light" || vscodeThemeKind === "vscode-light" || vscodeThemeKind === "vscode-high-contrast-light") return false;
	if (typeof window !== "undefined" && typeof window.matchMedia === "function") try {
		return window.matchMedia("(prefers-color-scheme: dark)").matches;
	} catch {
		return false;
	}
	return false;
}

/** 订阅宿主主题变化：<html> 属性观察 + matchMedia 双路 */
function subscribeThemeChanges(callback: () => void): () => void {
	if (typeof document === "undefined" || typeof window === "undefined") return () => void 0;
	let observer: MutationObserver | null = null;
	try {
		observer = new MutationObserver(() => callback());
		observer.observe(document.documentElement, {
			attributes: true,
			attributeFilter: [
				"class",
				"style",
				"data-theme",
				"data-vscode-theme-kind",
			],
		});
	} catch {
		observer = null;
	}
	let mediaQuery: MediaQueryList | null = null;
	const handleMediaChange = () => callback();
	if (typeof window.matchMedia === "function") try {
		mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
	} catch {
		mediaQuery = null;
	}
	if (mediaQuery) {
		if (typeof mediaQuery.addEventListener === "function") mediaQuery.addEventListener("change", handleMediaChange);
		else if (typeof (mediaQuery as any).addListener === "function") (mediaQuery as any).addListener(handleMediaChange);
	}
	return () => {
		if (observer) {
			observer.disconnect();
			observer = null;
		}
		if (mediaQuery) {
			if (typeof mediaQuery.removeEventListener === "function") mediaQuery.removeEventListener("change", handleMediaChange);
			else if (typeof (mediaQuery as any).removeListener === "function") (mediaQuery as any).removeListener(handleMediaChange);
			mediaQuery = null;
		}
	};
}

function sanitizeDownloadFileName(rawTitle: string): string {
	const withoutIllegalChars = (rawTitle || "widget").replace(/[\\/:*?"<>|]/g, "_");
	return Array.from(withoutIllegalChars).map((char) => char.charCodeAt(0) < 32 ? "_" : char).join("").replace(/\s+/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "") || "widget";
}

/** 通过 <a download> 触发浏览器下载 */
function triggerBrowserDownload(url: string, fileName: string): void {
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = fileName;
	anchor.rel = "noopener noreferrer";
	anchor.style.display = "none";
	document.body.appendChild(anchor);
	try {
		anchor.click();
	} catch {
		anchor.dispatchEvent(new MouseEvent("click", {
			bubbles: true,
			cancelable: true,
			view: window,
		}));
	} finally {
		window.setTimeout(() => {
			if (url.startsWith("blob:")) URL.revokeObjectURL(url);
			if (anchor.parentNode) anchor.parentNode.removeChild(anchor);
		}, 1e3);
	}
}

async function downloadWidgetCodeFallback(widgetCode: string, rawTitle: string): Promise<void> {
	const fileName = `${sanitizeDownloadFileName(rawTitle)}.html`;
	const blob = new Blob([widgetCode], { type: "text/html;charset=utf-8" });
	triggerBrowserDownload(URL.createObjectURL(blob), fileName);
}

const CODE_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>;
const DOWNLOAD_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>;
const COPY_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>;
const ERROR_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg>;
const WIDGET_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /></svg>;
const MORE_DOTS_ICON = <svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><circle cx="5" cy="12" r="1.75" /><circle cx="12" cy="12" r="1.75" /><circle cx="19" cy="12" r="1.75" /></svg>;
const IMAGE_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" /></svg>;

const heightCache = new Map<string, number>();
const DEFAULT_HEIGHT = 360;
const CAPTURE_TIMEOUT_MS = 3e4;

interface WidgetErrorState {
	phase: string;
	message: string;
	detail?: string;
	stack?: string;
}

export const WidgetRenderer = memo(function WidgetRenderer({
	widgetCode,
	isStreaming,
	title,
	toolName,
	showOverlay,
	loadingLabel,
	hostConfig,
	chromeless = false,
	showActions = true,
}: WidgetRendererProps) {
	const iframeRef = useRef<HTMLIFrameElement>(null);
	const [height, setHeight] = useState<number>(() => {
		if (title) {
			const cached = heightCache.get(title);
			if (cached) return cached;
		}
		return DEFAULT_HEIGHT;
	});
	const [showCode, setShowCode] = useState(false);
	const [showMenu, setShowMenu] = useState(false);
	const [isReady, setIsReady] = useState(false);
	const [useHtmlFallback, setUseHtmlFallback] = useState(false);
	const [widgetError, setWidgetError] = useState<WidgetErrorState | null>(null);
	const [copied, setCopied] = useState(false);
	const [downloadingImage, setDownloadingImage] = useState(false);
	const lastPayloadRef = useRef("");
	const menuRef = useRef<HTMLDivElement>(null);
	const fallbackContainerRef = useRef<HTMLDivElement>(null);
	const resizeGuardRef = useRef<WidgetHeightGuardState>({
		lastAcceptedAt: Date.now(),
		tinyGrowthStreak: 0,
		rapidGrowthStreak: 0,
	});
	const cspNonce = useMemo(() => {
		const scriptWithNonce = document.querySelector<HTMLScriptElement>("script[nonce]");
		const nonceFromScript = scriptWithNonce?.nonce || scriptWithNonce?.getAttribute("nonce") || "";
		if (nonceFromScript) return nonceFromScript;
		return (document.querySelector("meta[http-equiv=\"Content-Security-Policy\"]")?.getAttribute("content") || "").match(/'nonce-([^']+)'/i)?.[1] || "";
	}, []);
	const srcDoc = useMemo(() => buildSandboxHtml({ nonce: cspNonce }), [cspNonce]);

	// 握手重试：host 可能在消息监听挂载后才就绪，250ms 一次、上限 60s
	useEffect(() => {
		if (isReady || useHtmlFallback || !widgetCode) return;
		const maxDuration = 6e4;
		const intervalMs = 250;
		let elapsed = 0;
		const sendHandshake = () => {
			if (isReady || useHtmlFallback) return;
			const iframe = iframeRef.current?.contentWindow;
			if (!iframe) return;
			iframe.postMessage({ type: "widget:host-ready" }, "*");
		};
		sendHandshake();
		const interval = window.setInterval(() => {
			elapsed += intervalMs;
			sendHandshake();
			if (elapsed >= maxDuration) window.clearInterval(interval);
		}, intervalMs);
		return () => window.clearInterval(interval);
	}, [isReady, useHtmlFallback, widgetCode]);

	// iframe bootstrap 超时（5s）→ 退回宿主内联 HTML 渲染
	useEffect(() => {
		if (isReady || useHtmlFallback || !widgetCode) return;
		const timer = window.setTimeout(() => {
			if (!isReady) {
				setUseHtmlFallback(true);
				console.warn("[WidgetRenderer] iframe bootstrap timeout, fallback to inline html rendering.", {
					timestamp: new Date().toISOString(),
					title: title || "",
					toolName: toolName || "",
					widgetCodeLength: widgetCode.length,
				});
			}
		}, 5e3);
		return () => window.clearTimeout(timer);
	}, [isReady, title, toolName, useHtmlFallback, widgetCode]);

	const sendContent = useCallback(() => {
		const iframe = iframeRef.current?.contentWindow;
		if (!iframe || !widgetCode) return;
		if (isStreaming) {
			const sanitized = sanitizeForStreaming(widgetCode);
			if (!sanitized) return;
			if (sanitized === lastPayloadRef.current) return;
			lastPayloadRef.current = sanitized;
			iframe.postMessage({ type: "widget:update", html: sanitized }, "*");
		} else {
			const sanitized = sanitizeForIframe(widgetCode);
			lastPayloadRef.current = sanitized;
			iframe.postMessage({ type: "widget:finalize", html: sanitized }, "*");
		}
	}, [widgetCode, isStreaming]);

	const syncTheme = useCallback(() => {
		const push = () => {
			const iframe = iframeRef.current?.contentWindow;
			if (!iframe) return;
			const vars = collectCssVariables();
			const dark = isDarkMode();
			iframe.postMessage({ type: "widget:theme", vars, isDark: dark }, "*");
		};
		if (typeof window === "undefined" || typeof window.requestAnimationFrame !== "function") {
			push();
			return;
		}
		window.requestAnimationFrame(push);
	}, []);

	// 沙箱消息：ready/resize/sendMessage/link/error
	useEffect(() => {
		const handler = (e: MessageEvent) => {
			if (e.source !== iframeRef.current?.contentWindow) return;
			const data = e.data;
			if (!data || typeof data.type !== "string") return;
			switch (data.type) {
				case "widget:ready":
					setIsReady(true);
					setWidgetError(null);
					sendContent();
					syncTheme();
					break;
				case "widget:resize":
					setHeight((prevHeight) => {
						const nextHeight = normalizeWidgetHeight(data.height, DEFAULT_HEIGHT, WIDGET_MAX_HEIGHT);
						const now = Date.now();
						if (!shouldApplyWidgetHeight(nextHeight, prevHeight, now, resizeGuardRef.current)) return prevHeight;
						markWidgetHeightAccepted(now, resizeGuardRef.current);
						if (title) heightCache.set(title, nextHeight);
						return nextHeight;
					});
					break;
				case "widget:sendMessage":
					if (typeof data.text === "string" && data.text.length <= 500) hostConfig?.onSendMessage?.(data.text);
					break;
				case "widget:link":
					if (typeof data.href === "string") {
						if (!/^(javascript|data):/i.test(data.href)) hostConfig?.onOpenLink?.(data.href);
					}
					break;
				case "widget:error": {
					const phase = typeof data.phase === "string" ? data.phase : "script";
					// widget 代码自身报错不打扰用户（脚本执行有库兼容性波动），仅 console 记录
					const isWidgetCodeError = phase === "script" || phase === "update" || phase === "finalize";
					console.error("[WidgetRenderer] iframe reported widget:error", { data });
					if (isWidgetCodeError) break;
					setWidgetError({
						phase,
						message: data.message || "未知错误",
						detail: buildErrorDetail({
							detail: data.detail,
							stack: data.stack,
							iframeDiagnostics: {
								source: data.source,
								line: data.line,
								column: data.column,
								runtime: data.runtime,
							},
						}),
					});
					break;
				}
			}
		};
		window.addEventListener("message", handler);
		return () => window.removeEventListener("message", handler);
	}, [hostConfig, isReady, sendContent, syncTheme, title]);

	// 内容变化（流式追加）即推给沙箱
	useEffect(() => {
		if (useHtmlFallback) return;
		if (isReady && widgetCode) sendContent();
	}, [isReady, widgetCode, isStreaming, sendContent, useHtmlFallback]);

	useEffect(() => {
		if (!isReady || useHtmlFallback) return;
		return subscribeThemeChanges(syncTheme);
	}, [isReady, syncTheme, useHtmlFallback]);

	useEffect(() => {
		if (!showMenu) return;
		const handleClickOutside = (e: MouseEvent) => {
			if (menuRef.current && !menuRef.current.contains(e.target as globalThis.Node)) setShowMenu(false);
		};
		document.addEventListener("mousedown", handleClickOutside);
		return () => document.removeEventListener("mousedown", handleClickOutside);
	}, [showMenu]);

	// 退回宿主内联渲染时的兜底执行：仅完成态、无沙箱保护，作用域内手动执行脚本
	const fallbackHtml = useMemo(() => sanitizeForIframe(widgetCode), [widgetCode]);
	useEffect(() => {
		if (!useHtmlFallback || showCode) return;
		const container = fallbackContainerRef.current;
		if (!container) return;
		container.innerHTML = fallbackHtml;
		const bindInlineClickHandlers = () => {
			container.querySelectorAll("[onclick]").forEach((element) => {
				const code = element.getAttribute("onclick");
				if (!code) return;
				element.removeAttribute("onclick");
				element.addEventListener("click", (event) => {
					try {
						new Function("event", code).call(element, event);
					} catch (error) {
						console.warn("[WidgetRenderer] fallback onclick execution failed.", error);
					}
				});
			});
		};
		const executeScriptsSequentially = async () => {
			const scripts = Array.from(container.querySelectorAll("script"));
			scripts.forEach((script) => script.parentNode?.removeChild(script));
			for (const script of scripts) {
				const src = script.getAttribute("src");
				const code = script.textContent || "";
				try {
					if (src) {
						const response = await fetch(src);
						if (!response.ok) throw new Error(`fetch failed: ${response.status}`);
						new Function(await response.text())();
					} else if (code.trim()) new Function(code)();
				} catch (error) {
					console.warn("[WidgetRenderer] fallback script execution failed.", { src, error });
				}
			}
		};
		bindInlineClickHandlers();
		executeScriptsSequentially().catch((error) => {
			console.warn("[WidgetRenderer] fallback script queue failed.", { error });
			setWidgetError({
				phase: "fallback",
				message: "脚本执行失败",
				detail: error instanceof Error ? error.message : String(error),
			});
		});
	}, [fallbackHtml, showCode, useHtmlFallback, widgetCode]);

	// 向 iframe 发起截图并等待结果
	const requestIframeCapture = useCallback((requestId: string) => {
		return new Promise<{ dataUrl: string; width: number; height: number }>((resolve, reject) => {
			const iframe = iframeRef.current;
			if (!iframe) return reject(new Error("iframe unavailable"));
			const timer = window.setTimeout(() => {
				window.removeEventListener("message", handler);
				reject(new Error("Capture timed out"));
			}, CAPTURE_TIMEOUT_MS);
			const handler = (e: MessageEvent) => {
				if (e.source !== iframeRef.current?.contentWindow) return;
				const data = e.data;
				if (!data || data.requestId !== requestId) return;
				if (data.type === "widget:capture-result") {
					window.clearTimeout(timer);
					window.removeEventListener("message", handler);
					if (data.error) reject(new Error(data.error));
					else resolve({ dataUrl: data.dataUrl, width: data.width, height: data.height });
				}
			};
			window.addEventListener("message", handler);
			iframe.contentWindow?.postMessage({ type: "widget:capture", requestId }, "*");
		});
	}, []);

	const handleSaveFile = useCallback(async () => {
		setShowMenu(false);
		try {
			await downloadWidgetCodeFallback(widgetCode, title || "widget");
		} catch (error) {
			console.warn("[WidgetRenderer] fallback save file failed.", error);
		}
	}, [widgetCode, title]);

	const handleDownloadImage = useCallback(async () => {
		setShowMenu(false);
		if (downloadingImage) return;
		if (!iframeRef.current?.contentWindow) return;
		setDownloadingImage(true);
		try {
			const result = await requestIframeCapture(`capture_${Date.now()}`);
			const fileName = `${sanitizeDownloadFileName(title || "widget")}.png`;
			triggerBrowserDownload(result.dataUrl, fileName);
		} catch (error) {
			console.error("[WidgetRenderer] Download as image failed:", error);
		} finally {
			setDownloadingImage(false);
		}
	}, [downloadingImage, requestIframeCapture, title]);

	const handleCopyCode = useCallback(async () => {
		setShowMenu(false);
		if (!await copyTextWithFallback(widgetCode)) {
			console.warn("[WidgetRenderer] Copy widget code failed.");
			return;
		}
		setCopied(true);
		setTimeout(() => setCopied(false), 2e3);
	}, [widgetCode]);

	const handleCopyError = useCallback(async () => {
		if (!widgetError) return;
		if (!await copyTextWithFallback(`Phase: ${widgetError.phase}\nMessage: ${widgetError.message}${widgetError.detail ? `\nDetail: ${widgetError.detail}` : ""}`)) console.warn("[WidgetRenderer] Copy error detail failed.");
	}, [widgetError]);

	const toggleCode = useCallback(() => {
		setShowCode((prev) => !prev);
		setShowMenu(false);
	}, []);

	const containerClassName = resolveWidgetContainerClassName(chromeless);
	const renderHeader = shouldRenderWidgetHeader(chromeless);
	const renderLoadingOverlay = shouldRenderLoadingOverlay({
		chromeless,
		showOverlay,
		showCode,
		useHtmlFallback,
		hasError: !!widgetError,
	});
	const renderBootstrapPending = shouldRenderBootstrapPending({
		hasWidgetCode: !!widgetCode,
		isReady,
		useHtmlFallback,
		hasError: !!widgetError,
		showCode,
	});

	return (
		<div className={chromeless ? "cc-widget-wrapper cc-widget-chromeless" : "cc-widget-wrapper"}>
			{/* 顶栏常驻：chromeless 下仍提供「查看代码/下载/复制」菜单入口 */}
			<div className="cc-widget-wrapper-header">
				<span className="cc-widget-wrapper-title">
					<span className="cc-widget-wrapper-icon">{WIDGET_ICON}</span>
					{title || "可视化"}
				</span>
				{showActions && (
					<div className="cc-widget-header-right" ref={menuRef}>
						<button
							type="button"
							className="cc-widget-menu-trigger"
							onClick={() => setShowMenu(!showMenu)}
							title="更多操作"
							aria-label="更多操作"
						>
							{MORE_DOTS_ICON}
						</button>
						{showMenu && (
							<div className="cc-widget-dropdown">
								<button className="cc-widget-menu-item" onClick={handleSaveFile} disabled={isStreaming} title={isStreaming ? "渲染完成后可用" : undefined}>
									{DOWNLOAD_ICON}
									<span>保存到本地</span>
								</button>
								<button
									className="cc-widget-menu-item"
									onClick={handleDownloadImage}
									disabled={downloadingImage || !isReady || isStreaming}
									title={!isReady || isStreaming ? "渲染完成后可用" : undefined}
								>
									{IMAGE_ICON}
									<span>{downloadingImage ? "正在下载…" : "下载图片"}</span>
								</button>
								<button className="cc-widget-menu-item" onClick={handleCopyCode}>
									{COPY_ICON}
									<span>{copied ? "已复制" : "复制代码"}</span>
								</button>
								<button className="cc-widget-menu-item" onClick={toggleCode}>
									{showCode ? WIDGET_ICON : CODE_ICON}
									<span>{showCode ? "查看界面" : "查看代码"}</span>
								</button>
							</div>
						)}
					</div>
				)}
			</div>
			<div className={containerClassName}>
				{renderHeader && (
					<div className="cc-widget-header">
						<span className="cc-widget-icon">{WIDGET_ICON}</span>
						{title && <span className="cc-widget-title">{title}</span>}
					</div>
				)}
				{widgetError && (
					<div className="cc-widget-error-container">
						<div className="cc-widget-error-header">
							{ERROR_ICON}
							<span className="cc-widget-error-phase">[{widgetError.phase}]</span>
							<span className="cc-widget-error-message">{widgetError.message}</span>
						</div>
						{widgetError.detail && (
							<div className="cc-widget-error-detail">
								<pre>{widgetError.detail}</pre>
							</div>
						)}
						<button className="cc-widget-copy-error" onClick={handleCopyError}>
							{COPY_ICON}
							<span>复制错误</span>
						</button>
					</div>
				)}
				{!useHtmlFallback && (
					<iframe
						ref={iframeRef}
						className={`cc-widget-frame${showCode ? " cc-widget-frame-hidden" : ""}`}
						style={{ height }}
						sandbox="allow-scripts"
						srcDoc={srcDoc}
						title={title || "Interactive widget"}
					/>
				)}
				{renderBootstrapPending && (
					<div className="cc-widget-bootstrap-pending" role="status" aria-live="polite">
						<span className="cc-widget-bootstrap-spinner" aria-hidden="true" />
						<span className="cc-widget-bootstrap-text">正在初始化渲染器…</span>
					</div>
				)}
				{useHtmlFallback && !showCode && (
					<div
						ref={fallbackContainerRef}
						className="cc-widget-code-view"
						style={{
							display: "block",
							maxHeight: "min(80vh, 2000px)",
							overflowY: "auto",
							whiteSpace: "normal",
						}}
					/>
				)}
				{showCode && <div className="cc-widget-code-view">{widgetCode}</div>}
				{renderLoadingOverlay && (
					<div className="cc-widget-loading-overlay">
						{loadingLabel && (
							<div className="cc-widget-loading">
								<span className="cc-widget-loading-text">{loadingLabel}</span>
							</div>
						)}
					</div>
				)}
			</div>
		</div>
	);
});
